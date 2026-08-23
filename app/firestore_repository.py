from __future__ import annotations

import hashlib
import json
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from .domain import AgentRole, ApprovalKind, ModelUsage, RunStatus, SDLCStyle, TaskStatus
from .repositories import StudioRepository, utc_now


class FirestoreStudioRepository(StudioRepository):
    """Firestore implementation of the Studio persistence boundary.

    Runs keep their small, bounded task/defect/usage aggregates in one document so a
    workflow transition can use Firestore's optimistic transaction protection. Audit
    events remain separate documents and are committed atomically with mutations.
    """

    def __init__(self, project_id: str, client: Any | None = None) -> None:
        self.client: Any = client or firestore.Client(project=project_id)

    def ensure_demo_user(self) -> dict[str, Any]:
        return self.ensure_user("developer@demo.local", "Demo Developer")

    def ensure_user(self, email: str, display_name: str) -> dict[str, Any]:
        normalized = email.strip().lower()
        email_id = hashlib.sha256(normalized.encode()).hexdigest()
        mapping_ref = self.client.collection("user_emails").document(email_id)
        mapping = mapping_ref.get()
        if mapping.exists:
            user = self.client.collection("users").document(mapping.to_dict()["user_id"]).get()
            if user.exists:
                return dict(user.to_dict())
        user_id = str(uuid4())
        user = {
            "id": user_id,
            "email": normalized,
            "display_name": display_name,
            "created_at": utc_now(),
        }
        batch = self.client.batch()
        batch.create(self.client.collection("users").document(user_id), user)
        batch.set(mapping_ref, {"user_id": user_id, "email": normalized})
        batch.commit()
        return user

    def create_session(self, user_id: str) -> str:
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        now = datetime.now(UTC)
        self.client.collection("sessions").document(token_hash).set(
            {
                "user_id": user_id,
                "expires_at": (now + timedelta(hours=12)).isoformat(),
                "created_at": now.isoformat(),
            }
        )
        return token

    def user_for_session(self, token: str) -> dict[str, Any] | None:
        digest = hashlib.sha256(token.encode()).hexdigest()
        session = self.client.collection("sessions").document(digest).get()
        if not session.exists:
            return None
        payload = session.to_dict()
        if payload["expires_at"] <= utc_now():
            session.reference.delete()
            return None
        user = self.client.collection("users").document(payload["user_id"]).get()
        return dict(user.to_dict()) if user.exists else None

    def delete_session(self, token: str) -> None:
        digest = hashlib.sha256(token.encode()).hexdigest()
        self.client.collection("sessions").document(digest).delete()

    def create_project(
        self,
        owner_id: str,
        name: str,
        requirement: str,
        style: SDLCStyle,
        repository_url: str | None,
        workspace_hint: str | None,
    ) -> dict[str, Any]:
        project_id = str(uuid4())
        now = utc_now()
        project = {
            "id": project_id,
            "owner_id": owner_id,
            "name": name,
            "requirement": requirement,
            "sdlc_style": style.value,
            "repository_url": repository_url,
            "workspace_hint": workspace_hint,
            "created_at": now,
            "updated_at": now,
        }
        batch = self.client.batch()
        batch.create(self.client.collection("projects").document(project_id), project)
        self._add_audit(batch, project_id, None, owner_id, "project.created", name, now)
        batch.commit()
        return project

    def list_projects(self, owner_id: str) -> list[dict[str, Any]]:
        query = self.client.collection("projects").where(
            filter=FieldFilter("owner_id", "==", owner_id)
        )
        projects = [dict(item.to_dict()) for item in query.stream()]
        return sorted(projects, key=lambda item: item["created_at"], reverse=True)

    def get_project(self, project_id: str, owner_id: str) -> dict[str, Any] | None:
        snapshot = self.client.collection("projects").document(project_id).get()
        if not snapshot.exists:
            return None
        project = dict(snapshot.to_dict())
        return project if project.get("owner_id") == owner_id else None

    def create_run(self, project_id: str, owner_id: str) -> dict[str, Any]:
        project = self.get_project(project_id, owner_id)
        if project is None:
            raise KeyError("project not found")
        roles_by_style = {
            SDLCStyle.AGILE.value: [AgentRole.COORDINATOR, AgentRole.PRODUCT, AgentRole.ARCHITECT],
            SDLCStyle.WATERFALL.value: [
                AgentRole.PRODUCT,
                AgentRole.ARCHITECT,
                AgentRole.COORDINATOR,
            ],
            SDLCStyle.HYBRID.value: [
                AgentRole.ARCHITECT,
                AgentRole.PRODUCT,
                AgentRole.COORDINATOR,
            ],
        }
        roles = roles_by_style[project["sdlc_style"]]
        run_id = str(uuid4())
        now = utc_now()
        run = {
            "id": run_id,
            "project_id": project_id,
            "status": RunStatus.PLANNING.value,
            "current_agent": roles[0].value,
            "created_at": now,
            "updated_at": now,
            "tasks": [
                self._new_task(run_id, sequence, role, now)
                for sequence, role in enumerate(roles, 1)
            ],
            "defects": [],
            "usage": [],
        }
        batch = self.client.batch()
        batch.create(self.client.collection("runs").document(run_id), run)
        self._add_audit(batch, project_id, run_id, owner_id, "run.created", "Planning started", now)
        batch.commit()
        return self._present_run(run)

    def get_run(self, run_id: str, owner_id: str) -> dict[str, Any] | None:
        snapshot = self.client.collection("runs").document(run_id).get()
        if not snapshot.exists:
            return None
        run = dict(snapshot.to_dict())
        if self.get_project(run["project_id"], owner_id) is None:
            return None
        return self._present_run(run)

    def latest_run_for_project(self, project_id: str, owner_id: str) -> dict[str, Any] | None:
        if self.get_project(project_id, owner_id) is None:
            return None
        query = self.client.collection("runs").where(
            filter=FieldFilter("project_id", "==", project_id)
        )
        runs = [dict(item.to_dict()) for item in query.stream()]
        if not runs:
            return None
        return self._present_run(max(runs, key=lambda item: item["created_at"]))

    def claim_next_task(self, run_id: str, owner_id: str) -> dict[str, Any] | None:
        run = self.get_run(run_id, owner_id)
        if run is None:
            raise KeyError("run not found")
        queued = [task for task in run["tasks"] if task["status"] == TaskStatus.QUEUED.value]
        if not queued:
            return None
        queued.sort(key=lambda task: (task["agent_role"] != run["current_agent"], task["sequence"]))
        selected_id = queued[0]["id"]
        ref = self.client.collection("runs").document(run_id)
        transaction = self.client.transaction()
        snapshot = ref.get(transaction=transaction)
        stored = dict(snapshot.to_dict())
        tasks = list(stored["tasks"])
        selected = next((task for task in tasks if task["id"] == selected_id), None)
        if selected is None or selected["status"] != TaskStatus.QUEUED.value:
            transaction.rollback()
            return None
        now = utc_now()
        selected["status"] = TaskStatus.RUNNING.value
        selected["started_at"] = now
        transaction.update(
            ref,
            {"tasks": tasks, "current_agent": selected["agent_role"], "updated_at": now},
        )
        transaction.commit()
        return dict(selected)

    def complete_task(
        self, run_id: str, task_id: str, owner_id: str, summary: str, artifact: str, passed: bool
    ) -> None:
        run = self.get_run(run_id, owner_id)
        if run is None:
            raise KeyError("run not found")
        tasks = list(run["tasks"])
        task = next(
            (
                item
                for item in tasks
                if item["id"] == task_id and item["status"] == TaskStatus.RUNNING.value
            ),
            None,
        )
        if task is None:
            raise ValueError("task is not running")
        now = utc_now()
        task.update(
            {
                "status": (TaskStatus.COMPLETED if passed else TaskStatus.FAILED).value,
                "summary": summary,
                "artifact": artifact,
                "completed_at": now,
            }
        )
        batch = self.client.batch()
        batch.update(self.client.collection("runs").document(run_id), {"tasks": tasks})
        self._add_audit(
            batch,
            run["project_id"],
            run_id,
            task["agent_role"],
            "agent.completed",
            json.dumps({"agent": task["agent_role"], "summary": summary}),
            now,
        )
        batch.commit()

    def record_model_usage(
        self, run_id: str, task_id: str, owner_id: str, role: AgentRole, usage: ModelUsage
    ) -> None:
        presented = self.get_run(run_id, owner_id)
        if presented is None:
            raise KeyError("run not found")
        ref = self.client.collection("runs").document(run_id)
        snapshot = ref.get()
        stored = dict(snapshot.to_dict())
        records = [item for item in stored.get("usage", []) if item["task_id"] != task_id]
        records.append(
            {
                "id": str(uuid4()),
                "run_id": run_id,
                "task_id": task_id,
                "agent_role": role.value,
                "provider": usage.provider[:40],
                "model": usage.model[:100],
                "prompt_tokens": usage.prompt_tokens,
                "completion_tokens": usage.completion_tokens,
                "estimated_cost_usd": usage.estimated_cost_usd,
                "created_at": utc_now(),
            }
        )
        self.client.collection("runs").document(run_id).update({"usage": records})

    def set_run_status(
        self, run_id: str, owner_id: str, status: RunStatus, current_agent: AgentRole | None
    ) -> None:
        run = self.get_run(run_id, owner_id)
        if run is None:
            raise KeyError("run not found")
        now = utc_now()
        batch = self.client.batch()
        batch.update(
            self.client.collection("runs").document(run_id),
            {
                "status": status.value,
                "current_agent": current_agent.value if current_agent else None,
                "updated_at": now,
            },
        )
        self._add_audit(
            batch,
            run["project_id"],
            run_id,
            "system",
            "run.status_changed",
            status.value,
            now,
        )
        batch.commit()

    def enqueue_task(self, run_id: str, role: AgentRole) -> None:
        ref = self.client.collection("runs").document(run_id)
        transaction = self.client.transaction()
        snapshot = ref.get(transaction=transaction)
        if not snapshot.exists:
            transaction.rollback()
            raise KeyError("run not found")
        run = dict(snapshot.to_dict())
        tasks = list(run["tasks"])
        sequence = max((int(task["sequence"]) for task in tasks), default=0) + 1
        tasks.append(self._new_task(run_id, sequence, role, utc_now()))
        transaction.update(ref, {"tasks": tasks})
        transaction.commit()

    def enqueue_tasks(self, run_id: str, roles: list[AgentRole]) -> None:
        for role in roles:
            self.enqueue_task(run_id, role)

    def record_approval(
        self,
        run_id: str,
        owner_id: str,
        kind: ApprovalKind,
        approved: bool,
        comment: str | None,
    ) -> None:
        run = self.get_run(run_id, owner_id)
        if run is None:
            raise KeyError("run not found")
        now = utc_now()
        approval_id = str(uuid4())
        batch = self.client.batch()
        batch.create(
            self.client.collection("approvals").document(approval_id),
            {
                "id": approval_id,
                "run_id": run_id,
                "kind": kind.value,
                "approved": approved,
                "comment": comment,
                "decided_by": owner_id,
                "created_at": now,
            },
        )
        self._add_audit(
            batch,
            run["project_id"],
            run_id,
            owner_id,
            "approval.recorded",
            json.dumps({"kind": kind.value, "approved": approved}),
            now,
        )
        batch.commit()

    def record_defect(
        self,
        run_id: str,
        owner_id: str,
        title: str,
        description: str,
        severity: str,
        source: str,
    ) -> dict[str, Any]:
        run = self.get_run(run_id, owner_id)
        if run is None:
            raise KeyError("run not found")
        now = utc_now()
        defect = {
            "id": str(uuid4()),
            "run_id": run_id,
            "title": title,
            "description": description,
            "severity": severity,
            "source": source,
            "status": "open",
            "created_at": now,
        }
        defects = [*run["defects"], defect]
        batch = self.client.batch()
        batch.update(self.client.collection("runs").document(run_id), {"defects": defects})
        self._add_audit(
            batch,
            run["project_id"],
            run_id,
            source,
            "defect.reported",
            json.dumps({"defect_id": defect["id"], "severity": severity, "title": title}),
            now,
        )
        batch.commit()
        return defect

    def audit_events(self, project_id: str, owner_id: str) -> list[dict[str, Any]]:
        if self.get_project(project_id, owner_id) is None:
            raise KeyError("project not found")
        query = self.client.collection("audit_events").where(
            filter=FieldFilter("project_id", "==", project_id)
        )
        events = [dict(item.to_dict()) for item in query.stream()]
        return sorted(events, key=lambda item: item["created_at"])

    @staticmethod
    def _new_task(run_id: str, sequence: int, role: AgentRole, created_at: str) -> dict[str, Any]:
        return {
            "id": str(uuid4()),
            "run_id": run_id,
            "sequence": sequence,
            "agent_role": role.value,
            "status": TaskStatus.QUEUED.value,
            "summary": None,
            "artifact": None,
            "started_at": None,
            "completed_at": None,
            "created_at": created_at,
        }

    @staticmethod
    def _present_run(run: dict[str, Any]) -> dict[str, Any]:
        presented = dict(run)
        usage = list(presented.pop("usage", []))
        costs = [item["estimated_cost_usd"] for item in usage]
        presented["usage_summary"] = {
            "prompt_tokens": sum(int(item["prompt_tokens"]) for item in usage),
            "completion_tokens": sum(int(item["completion_tokens"]) for item in usage),
            "estimated_cost_usd": (
                sum(float(cost) for cost in costs)
                if all(cost is not None for cost in costs)
                else None
            ),
            "models": [{"provider": item["provider"], "model": item["model"]} for item in usage],
        }
        return presented

    def _add_audit(
        self,
        batch: Any,
        project_id: str,
        run_id: str | None,
        actor: str,
        event_type: str,
        detail: str,
        created_at: str,
    ) -> None:
        event_id = str(uuid4())
        batch.create(
            self.client.collection("audit_events").document(event_id),
            {
                "id": event_id,
                "project_id": project_id,
                "run_id": run_id,
                "actor": actor,
                "event_type": event_type,
                "detail": detail,
                "created_at": created_at,
            },
        )
