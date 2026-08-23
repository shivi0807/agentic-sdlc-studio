from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import uuid4


class WorkspaceError(ValueError):
    """Raised when an untrusted generated artifact cannot be materialized safely."""


class WorkspaceEngine:
    """Materialize bounded agent output and run only application-owned checks."""

    MAX_FILES = 20
    MAX_FILE_BYTES = 100_000
    MAX_TOTAL_BYTES = 500_000
    MAX_OUTPUT_CHARS = 20_000
    VALIDATION_TIMEOUT_SECONDS = 30
    SUPPORTED_PROJECT_TYPES = {"python-stdlib"}
    _SAFE_ID = re.compile(r"\A[a-zA-Z0-9_-]{1,100}\Z")

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def materialize(
        self, project_id: str, artifact: dict[str, Any], project_name: str | None = None
    ) -> dict[str, Any]:
        """Build a fresh snapshot, then promote it without retaining stale generated files."""
        workspace = self._workspace_path(project_id)
        if workspace.exists():
            if not workspace.is_dir() or workspace.is_symlink():
                raise WorkspaceError("project workspace must be a real directory")
            self._assert_no_symlinks(workspace)
        project_type = artifact.get("project_type")
        if project_type not in self.SUPPORTED_PROJECT_TYPES:
            raise WorkspaceError("unsupported generated project type")
        files = artifact.get("files")
        if not isinstance(files, dict) or not files:
            raise WorkspaceError("developer artifact must contain a non-empty files mapping")
        if len(files) > self.MAX_FILES:
            raise WorkspaceError("developer artifact contains too many files")

        validated: list[tuple[PurePosixPath, str]] = []
        total_bytes = 0
        for untrusted_path, content in files.items():
            if not isinstance(untrusted_path, str) or not isinstance(content, str):
                raise WorkspaceError("generated file paths and contents must be strings")
            relative = self._safe_relative_path(untrusted_path)
            encoded_size = len(content.encode("utf-8"))
            if encoded_size > self.MAX_FILE_BYTES:
                raise WorkspaceError("generated file exceeds the size limit")
            total_bytes += encoded_size
            if total_bytes > self.MAX_TOTAL_BYTES:
                raise WorkspaceError("generated project exceeds the total size limit")
            validated.append((relative, content))

        staging = Path(tempfile.mkdtemp(prefix=f".{project_id}-staging-", dir=self.root))
        try:
            for relative, content in validated:
                target = staging.joinpath(*relative.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                self._reject_symlinks(staging, target)
                self._atomic_write_text(target, content)

            metadata = {
                "project_type": project_type,
                "files": [relative.as_posix() for relative, _content in validated],
            }
            metadata_dir = staging / ".sdlc"
            metadata_dir.mkdir()
            self._atomic_write_text(metadata_dir / "project.json", json.dumps(metadata, indent=2))
            self._copy_git_metadata(workspace, staging)
            self._promote_staging(workspace, staging)
        finally:
            if staging.exists():
                shutil.rmtree(staging)

        git = self._initialize_git(workspace, project_id, project_name)
        return {
            "path": project_id,
            "project_type": project_type,
            "files": metadata["files"],
            "git": git,
        }

    def validate(self, project_id: str, project_type: str | None = None) -> dict[str, Any]:
        """Run a fixed validation recipe; no model or user command reaches a shell."""
        workspace = self._workspace(project_id, create=False)
        recorded_type = self._project_type(workspace)
        if project_type is not None and project_type != recorded_type:
            raise WorkspaceError("project type does not match the materialized workspace")
        if recorded_type != "python-stdlib":
            raise WorkspaceError("unsupported generated project type")

        commands = [
            ("unittest", [sys.executable, "-m", "unittest", "discover", "-s", "tests"]),
            ("compileall", [sys.executable, "-m", "compileall", "-q", "src"]),
        ]
        checks = [self._run_check(name, command, workspace) for name, command in commands]
        evidence = {
            "deliverable": "validation_report",
            "project_type": recorded_type,
            "passed": all(check["passed"] for check in checks),
            "checks": checks,
            "note": "Commands are selected by the application allow-list, not by user input.",
        }
        self._atomic_write_text(
            workspace / ".sdlc" / "validation.json", json.dumps(evidence, indent=2)
        )
        return evidence

    def validation_evidence(self, project_id: str) -> dict[str, Any] | None:
        workspace = self._workspace(project_id, create=False)
        evidence_path = workspace / ".sdlc" / "validation.json"
        if not evidence_path.is_file() or evidence_path.is_symlink():
            return None
        try:
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        return evidence if isinstance(evidence, dict) else None

    def review_allowed(self, project_id: str) -> bool:
        evidence = self.validation_evidence(project_id)
        return bool(evidence and evidence.get("passed") is True)

    def review_evidence(self, project_id: str) -> dict[str, Any]:
        """Return bounded source excerpts plus real validation evidence for review."""
        workspace = self._workspace(project_id, create=False)
        metadata_path = workspace / ".sdlc" / "project.json"
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as error:
            raise WorkspaceError("workspace metadata is invalid") from error
        recorded_files = metadata.get("files")
        excerpts: dict[str, str] = {}
        if isinstance(recorded_files, list):
            for value in recorded_files:
                if len(excerpts) >= 4 or not isinstance(value, str):
                    break
                relative = self._safe_relative_path(value)
                if relative.suffix.lower() not in {".py", ".md", ".txt", ".json", ".toml"}:
                    continue
                target = workspace.joinpath(*relative.parts)
                self._reject_symlinks(workspace, target)
                if not target.is_file() or target.is_symlink():
                    continue
                try:
                    excerpts[relative.as_posix()] = target.read_text(encoding="utf-8")[:2_000]
                except (OSError, UnicodeDecodeError):
                    continue
        return {
            "source_excerpts": excerpts,
            "validation": self.validation_evidence(project_id) or {"passed": False, "checks": []},
        }

    def _workspace(self, project_id: str, *, create: bool) -> Path:
        workspace = self._workspace_path(project_id)
        if workspace.exists() and workspace.is_symlink():
            raise WorkspaceError("project workspace cannot be a symbolic link")
        if create:
            workspace.mkdir(parents=True, exist_ok=True)
        elif not workspace.is_dir():
            raise WorkspaceError("project workspace does not exist")
        return workspace

    def _workspace_path(self, project_id: str) -> Path:
        if not self._SAFE_ID.fullmatch(project_id):
            raise WorkspaceError("invalid project identifier")
        workspace = self.root / project_id
        if not workspace.resolve().is_relative_to(self.root):
            raise WorkspaceError("project workspace escapes the configured root")
        return workspace

    @staticmethod
    def _assert_no_symlinks(workspace: Path) -> None:
        for root, directories, files in os.walk(workspace, followlinks=False):
            root_path = Path(root)
            for name in [*directories, *files]:
                if (root_path / name).is_symlink():
                    raise WorkspaceError("project workspace contains a symbolic link")

    @staticmethod
    def _copy_git_metadata(workspace: Path, staging: Path) -> None:
        git_directory = workspace / ".git"
        if not git_directory.exists():
            return
        if not git_directory.is_dir() or git_directory.is_symlink():
            raise WorkspaceError("Git metadata must be a real directory")
        shutil.copytree(git_directory, staging / ".git", symlinks=False)

    def _promote_staging(self, workspace: Path, staging: Path) -> None:
        backup = self.root / f".{workspace.name}-previous-{uuid4().hex}"
        moved_existing = False
        try:
            if workspace.exists():
                workspace.replace(backup)
                moved_existing = True
            staging.replace(workspace)
        except OSError as error:
            if moved_existing and backup.exists() and not workspace.exists():
                backup.replace(workspace)
            raise WorkspaceError("could not promote the fresh workspace snapshot") from error
        if backup.exists():
            resolved_backup = backup.resolve()
            if not resolved_backup.is_relative_to(self.root) or backup.is_symlink():
                raise WorkspaceError("workspace backup failed its safety check")
            shutil.rmtree(backup)

    @staticmethod
    def _safe_relative_path(value: str) -> PurePosixPath:
        if not value or "\\" in value or any(unicodedata.category(char) == "Cc" for char in value):
            raise WorkspaceError("generated file path contains unsafe characters")
        path = PurePosixPath(value)
        if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            raise WorkspaceError("generated file path must be a normalized relative path")
        if path.parts[0] in {".git", ".sdlc"} or ":" in path.parts[0]:
            raise WorkspaceError("generated file path targets a reserved location")
        return path

    @staticmethod
    def _reject_symlinks(workspace: Path, target: Path) -> None:
        current = workspace
        for part in target.relative_to(workspace).parts:
            current = current / part
            if current.exists() and current.is_symlink():
                raise WorkspaceError("generated file path crosses a symbolic link")

    @staticmethod
    def _atomic_write_text(target: Path, content: str) -> None:
        descriptor, temporary_name = tempfile.mkstemp(prefix=".sdlc-write-", dir=target.parent)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)

    def _project_type(self, workspace: Path) -> str:
        metadata_path = workspace / ".sdlc" / "project.json"
        if not metadata_path.is_file() or metadata_path.is_symlink():
            raise WorkspaceError("workspace metadata is missing")
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as error:
            raise WorkspaceError("workspace metadata is invalid") from error
        project_type = metadata.get("project_type")
        if not isinstance(project_type, str):
            raise WorkspaceError("workspace project type is invalid")
        return project_type

    def _run_check(self, name: str, command: list[str], workspace: Path) -> dict[str, Any]:
        try:
            completed = subprocess.run(  # noqa: S603 - fixed internal allow-list
                command,
                cwd=workspace,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.VALIDATION_TIMEOUT_SECONDS,
                shell=False,
            )
            stdout = completed.stdout[-self.MAX_OUTPUT_CHARS :]
            stderr = completed.stderr[-self.MAX_OUTPUT_CHARS :]
            return {
                "name": name,
                "command": self._display_command(command),
                "exit_code": completed.returncode,
                "stdout": stdout,
                "stderr": stderr,
                "timed_out": False,
                "passed": completed.returncode == 0,
            }
        except subprocess.TimeoutExpired as error:
            return {
                "name": name,
                "command": self._display_command(command),
                "exit_code": None,
                "stdout": self._output_text(error.stdout),
                "stderr": self._output_text(error.stderr),
                "timed_out": True,
                "passed": False,
            }

    @staticmethod
    def _display_command(command: list[str]) -> list[str]:
        return ["python" if index == 0 else part for index, part in enumerate(command)]

    def _output_text(self, output: str | bytes | None) -> str:
        if isinstance(output, bytes):
            output = output.decode("utf-8", errors="replace")
        return (output or "")[-self.MAX_OUTPUT_CHARS :]

    @staticmethod
    def _initialize_git(
        workspace: Path, project_id: str, project_name: str | None
    ) -> dict[str, Any]:
        if project_name is None:
            branch = f"project/{project_id}"
        else:
            safe_name = re.sub(r"[^a-z0-9]+", "-", project_name.lower()).strip("-")
            branch = f"project/{safe_name[:48] or 'project'}-{project_id[:8]}"
        git_executable = shutil.which("git")
        if git_executable is None:
            return {"initialized": False, "reason": "git is unavailable"}
        try:
            version = subprocess.run(  # noqa: S603 - executable resolved by shutil.which
                [git_executable, "--version"],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
                shell=False,
            )
            if version.returncode != 0:
                return {"initialized": False, "reason": "git is unavailable"}
            initialized = subprocess.run(  # noqa: S603 - executable resolved by shutil.which
                [git_executable, "init", "-b", branch],
                cwd=workspace,
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
                shell=False,
            )
            return {
                "initialized": initialized.returncode == 0,
                "branch": branch if initialized.returncode == 0 else None,
                "message": (initialized.stdout or initialized.stderr)[-1000:],
            }
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return {"initialized": False, "reason": "git is unavailable"}
