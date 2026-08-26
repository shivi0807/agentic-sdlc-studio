from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from google.cloud.storage import Client  # type: ignore[import-untyped]

from .workspaces import WorkspaceEngine, WorkspaceError


class CloudStorageWorkspaceEngine(WorkspaceEngine):
    """Cloud Storage-backed snapshots with a bounded local working directory.

    Cloud Run's filesystem is temporary. This adapter synchronizes only the
    application-owned workspace snapshot before and after an allowed operation.
    It deliberately does not upload Git metadata or arbitrary host files.
    """

    def __init__(self, root: Path, bucket_name: str, project_id: str) -> None:
        super().__init__(root)
        self.client: Any = Client(project=project_id)
        self.bucket: Any = self.client.bucket(bucket_name)

    def materialize(
        self, project_id: str, artifact: dict[str, Any], project_name: str | None = None
    ) -> dict[str, Any]:
        result = super().materialize(project_id, artifact, project_name)
        self._upload_snapshot(project_id)
        result["storage"] = "gcs"
        return result

    def validate(self, project_id: str, project_type: str | None = None) -> dict[str, Any]:
        self._download_snapshot(project_id)
        evidence = super().validate(project_id, project_type)
        self._upload_snapshot(project_id)
        return evidence

    def validation_evidence(self, project_id: str) -> dict[str, Any] | None:
        self._download_snapshot(project_id)
        return super().validation_evidence(project_id)

    def review_evidence(self, project_id: str) -> dict[str, Any]:
        self._download_snapshot(project_id)
        return super().review_evidence(project_id)

    def _prefix(self, project_id: str) -> str:
        self._workspace_path(project_id)
        return f"workspaces/{project_id}/"

    def _upload_snapshot(self, project_id: str) -> None:
        workspace = self._workspace(project_id, create=False)
        self._assert_no_symlinks(workspace)
        prefix = self._prefix(project_id)
        for blob in self.client.list_blobs(self.bucket, prefix=prefix):
            blob.delete()
        for path in workspace.rglob("*"):
            if not path.is_file() or path.is_symlink() or ".git" in path.parts:
                continue
            relative = path.relative_to(workspace).as_posix()
            self.bucket.blob(prefix + relative).upload_from_filename(str(path))

    def _download_snapshot(self, project_id: str) -> None:
        workspace = self._workspace_path(project_id)
        prefix = self._prefix(project_id)
        blobs = list(self.client.list_blobs(self.bucket, prefix=prefix))
        if not blobs:
            raise WorkspaceError("cloud workspace snapshot does not exist")
        if workspace.exists():
            if workspace.is_symlink() or not workspace.is_dir():
                raise WorkspaceError("project workspace must be a real directory")
            shutil.rmtree(workspace)
        workspace.mkdir(parents=True)
        for blob in blobs:
            relative = blob.name.removeprefix(prefix)
            if not relative:
                continue
            safe = self._safe_relative_path(relative)
            target = workspace.joinpath(*safe.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            self._reject_symlinks(workspace, target)
            blob.download_to_filename(str(target))
