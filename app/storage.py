from __future__ import annotations

from typing import Any, Protocol


class PersistentStudioStore(Protocol):
    """Port implemented by SQLite locally and Firestore in Cloud Run."""

    def get_project(self, project_id: str, owner_id: str) -> dict[str, Any] | None: ...

    def get_run(self, run_id: str, owner_id: str) -> dict[str, Any] | None: ...
