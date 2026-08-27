from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from app.cloud_workspaces import CloudStorageWorkspaceEngine
from app.workspaces import WorkspaceEngine, WorkspaceError


def python_artifact(test_body: str | None = None) -> dict[str, object]:
    return {
        "deliverable": "implementation",
        "project_type": "python-stdlib",
        "files": {
            "src/__init__.py": "",
            "src/application.py": (
                '"""Generated application."""\n\n'
                "def project_name() -> str:\n"
                '    return "Generated project"\n'
            ),
            "tests/test_application.py": test_body
            or (
                "import unittest\n\n"
                "from src.application import project_name\n\n\n"
                "class ApplicationTests(unittest.TestCase):\n"
                "    def test_project_name(self) -> None:\n"
                '        self.assertEqual(project_name(), "Generated project")\n'
            ),
        },
    }


def test_materializes_files_inside_project_and_initializes_git(tmp_path: Path) -> None:
    engine = WorkspaceEngine(tmp_path / "workspaces")

    result = engine.materialize("project-123", python_artifact())

    workspace = tmp_path / "workspaces" / result["path"]
    assert (workspace / "src" / "application.py").is_file()
    assert (workspace / "tests" / "test_application.py").is_file()
    assert result["files"] == [
        "src/__init__.py",
        "src/application.py",
        "tests/test_application.py",
    ]
    if result["git"]["initialized"]:
        assert (workspace / ".git").is_dir()
        assert result["git"]["branch"] == "project/project-123"


@pytest.mark.parametrize(
    "unsafe_path",
    ["../escape.py", "/absolute.py", "src\\escape.py", ".git/config", ".sdlc/a", "C:/x"],
)
def test_rejects_paths_that_can_escape_or_target_metadata(tmp_path: Path, unsafe_path: str) -> None:
    engine = WorkspaceEngine(tmp_path / "workspaces")
    artifact = python_artifact()
    artifact["files"] = {unsafe_path: "unsafe"}

    with pytest.raises(WorkspaceError):
        engine.materialize("project-123", artifact)

    assert not (tmp_path / "escape.py").exists()


def test_rejects_symlink_path_components(tmp_path: Path) -> None:
    engine = WorkspaceEngine(tmp_path / "workspaces")
    workspace = tmp_path / "workspaces" / "project-123"
    workspace.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (workspace / "src").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symbolic links are unavailable for this test account")

    with pytest.raises(WorkspaceError, match="symbolic link"):
        engine.materialize("project-123", python_artifact())

    assert not (outside / "application.py").exists()


def test_real_allowlisted_validation_records_evidence(tmp_path: Path) -> None:
    engine = WorkspaceEngine(tmp_path / "workspaces")
    engine.materialize("project-123", python_artifact())

    evidence = engine.validate("project-123")

    assert evidence["passed"] is True
    assert [check["name"] for check in evidence["checks"]] == ["unittest", "compileall"]
    assert all(check["exit_code"] == 0 for check in evidence["checks"])
    assert all(isinstance(check["stdout"], str) for check in evidence["checks"])
    assert engine.review_allowed("project-123") is True
    persisted = json.loads(
        (tmp_path / "workspaces/project-123/.sdlc/validation.json").read_text(encoding="utf-8")
    )
    assert persisted == evidence


def test_cloud_static_validation_syncs_snapshot_before_and_after_check() -> None:
    engine = object.__new__(CloudStorageWorkspaceEngine)
    download_snapshot = Mock()
    upload_snapshot = Mock()
    engine._download_snapshot = download_snapshot  # type: ignore[method-assign]
    engine._upload_snapshot = upload_snapshot  # type: ignore[method-assign]
    expected = {"passed": True, "checks": []}

    with patch.object(WorkspaceEngine, "static_validate", return_value=expected) as validate:
        result = engine.static_validate("project-123")

    assert result == expected
    download_snapshot.assert_called_once_with("project-123")
    validate.assert_called_once_with("project-123")
    upload_snapshot.assert_called_once_with("project-123")


def test_failing_tests_produce_real_failure_and_block_review(tmp_path: Path) -> None:
    engine = WorkspaceEngine(tmp_path / "workspaces")
    failing_test = (
        "import unittest\n\n\n"
        "class ApplicationTests(unittest.TestCase):\n"
        "    def test_failure(self) -> None:\n"
        '        self.fail("intentional regression")\n'
    )
    engine.materialize("project-123", python_artifact(failing_test))

    evidence = engine.validate("project-123")

    assert evidence["passed"] is False
    assert evidence["checks"][0]["exit_code"] != 0
    assert "intentional regression" in evidence["checks"][0]["stderr"]
    assert engine.review_allowed("project-123") is False


def test_retry_uses_fresh_snapshot_and_cannot_execute_omitted_old_files(tmp_path: Path) -> None:
    engine = WorkspaceEngine(tmp_path / "workspaces")
    first = python_artifact()
    first_files = first["files"]
    assert isinstance(first_files, dict)
    first_files["src/legacy.py"] = "SHOULD_NOT_REMAIN = True\n"
    first_files["tests/test_stale.py"] = (
        "import unittest\n\n\n"
        "class StaleTests(unittest.TestCase):\n"
        "    def test_stale_failure(self) -> None:\n"
        '        self.fail("stale test executed")\n'
    )
    initial = engine.materialize("project-123", first)
    assert engine.validate("project-123")["passed"] is False

    retry = engine.materialize("project-123", python_artifact())
    workspace = tmp_path / "workspaces" / retry["path"]

    assert not (workspace / "src/legacy.py").exists()
    assert not (workspace / "tests/test_stale.py").exists()
    assert engine.validate("project-123")["passed"] is True
    if initial["git"]["initialized"]:
        assert (workspace / ".git").is_dir()


def test_failed_snapshot_promotion_restores_previous_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = WorkspaceEngine(tmp_path / "workspaces")
    engine.materialize("project-123", python_artifact())
    workspace = tmp_path / "workspaces/project-123"
    original_source = (workspace / "src/application.py").read_text(encoding="utf-8")
    original_replace = Path.replace

    def fail_staging_promotion(self: Path, target: str | Path) -> Path:
        destination = Path(target)
        if "-staging-" in self.name and destination.name == "project-123":
            raise OSError("simulated promotion failure")
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", fail_staging_promotion)
    replacement = python_artifact()
    replacement_files = replacement["files"]
    assert isinstance(replacement_files, dict)
    replacement_files["src/application.py"] = "REPLACEMENT = True\n"

    with pytest.raises(WorkspaceError, match="promote"):
        engine.materialize("project-123", replacement)

    assert (workspace / "src/application.py").read_text(encoding="utf-8") == original_source
    assert not list((tmp_path / "workspaces").glob(".project-123-staging-*"))
    assert not list((tmp_path / "workspaces").glob(".project-123-previous-*"))


def test_file_and_byte_limits_are_enforced(tmp_path: Path) -> None:
    engine = WorkspaceEngine(tmp_path / "workspaces")
    too_many = python_artifact()
    too_many["files"] = {f"src/file_{index}.py": "" for index in range(21)}
    with pytest.raises(WorkspaceError, match="too many"):
        engine.materialize("project-123", too_many)

    too_large = python_artifact()
    too_large["files"] = {"src/application.py": "x" * 100_001}
    with pytest.raises(WorkspaceError, match="size limit"):
        engine.materialize("project-456", too_large)
