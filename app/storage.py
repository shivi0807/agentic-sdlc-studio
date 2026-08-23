from __future__ import annotations

from typing import Any, Protocol


class PersistentStudioStore(Protocol):
    """Port implemented by SQLite locally and by Firestore before Cloud Run deployment.

    The application currently wires ``StudioRepository``. A production Firestore adapter
    must provide the same semantic operations and atomic project/audit writes. This explicit
    boundary prevents treating Cloud Run's ephemeral filesystem as durable storage.
    """

    def get_project(self, project_id: str, owner_id: str) -> dict[str, Any] | None: ...

    def get_run(self, run_id: str, owner_id: str) -> dict[str, Any] | None: ...
