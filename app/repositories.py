from __future__ import annotations

import hashlib
import json
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from .database import Database
from .domain import AgentRole, ApprovalKind, ModelUsage, RunStatus, SDLCStyle, TaskStatus


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class StudioRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def ensure_demo_user(self) -> dict[str, Any]:
        return self.ensure_user("developer@demo.local", "Demo Developer")

    def ensure_user(self, email: str, display_name: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
            if row:
                return dict(row)
            user_id = str(uuid4())
            now = utc_now()
            connection.execute(
                "INSERT INTO users(id,email,display_name,created_at) VALUES(?,?,?,?)",
                (user_id, email, display_name, now),
            )
            connection.commit()
            return {
                "id": user_id,
                "email": email,
                "display_name": display_name,
                "created_at": now,
            }

    def create_session(self, user_id: str) -> str:
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        now = datetime.now(UTC)
        with self.database.connect() as connection:
            connection.execute(
                "INSERT INTO sessions(token_hash,user_id,expires_at,created_at) VALUES(?,?,?,?)",
                (token_hash, user_id, (now + timedelta(hours=12)).isoformat(), now.isoformat()),
            )
            connection.commit()
        return token

    def user_for_session(self, token: str) -> dict[str, Any] | None:
        digest = hashlib.sha256(token.encode()).hexdigest()
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT users.* FROM sessions JOIN users ON users.id=sessions.user_id
                   WHERE sessions.token_hash=? AND sessions.expires_at>?""",
                (digest, utc_now()),
            ).fetchone()
            return dict(row) if row else None

    def delete_session(self, token: str) -> None:
        digest = hashlib.sha256(token.encode()).hexdigest()
        with self.database.connect() as connection:
            connection.execute("DELETE FROM sessions WHERE token_hash=?", (digest,))
            connection.commit()

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
        with self.database.connect() as connection:
            connection.execute("BEGIN")
            connection.execute(
                """INSERT INTO projects
                   (id,owner_id,name,requirement,sdlc_style,repository_url,workspace_hint,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    project_id,
                    owner_id,
                    name,
                    requirement,
                    style.value,
                    repository_url,
                    workspace_hint,
                    now,
                    now,
                ),
            )
            self._audit(connection, project_id, None, owner_id, "project.created", name, now)
            connection.commit()
        return self.get_project(project_id, owner_id) or {}

    def list_projects(self, owner_id: str) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM projects WHERE owner_id=? ORDER BY created_at DESC", (owner_id,)
            ).fetchall()
            return [dict(row) for row in rows]

    def get_project(self, project_id: str, owner_id: str) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM projects WHERE id=? AND owner_id=?", (project_id, owner_id)
            ).fetchone()
            return dict(row) if row else None

    def create_run(self, project_id: str, owner_id: str) -> dict[str, Any]:
        project = self.get_project(project_id, owner_id)
        if project is None:
            raise KeyError("project not found")
        run_id = str(uuid4())
        now = utc_now()
        roles_by_style = {
            SDLCStyle.AGILE.value: [
                AgentRole.COORDINATOR,
                AgentRole.PRODUCT,
                AgentRole.ARCHITECT,
            ],
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
        with self.database.connect() as connection:
            connection.execute("BEGIN")
            connection.execute(
                """INSERT INTO runs
                   (id,project_id,status,current_agent,created_at,updated_at)
                   VALUES(?,?,?,?,?,?)""",
                (run_id, project_id, RunStatus.PLANNING.value, roles[0].value, now, now),
            )
            for sequence, role in enumerate(roles, 1):
                connection.execute(
                    """INSERT INTO agent_tasks
                       (id,run_id,sequence,agent_role,status,created_at) VALUES(?,?,?,?,?,?)""",
                    (str(uuid4()), run_id, sequence, role.value, TaskStatus.QUEUED.value, now),
                )
            self._audit(
                connection, project_id, run_id, owner_id, "run.created", "Planning started", now
            )
            connection.commit()
        return self.get_run(run_id, owner_id) or {}

    def get_run(self, run_id: str, owner_id: str) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """SELECT runs.* FROM runs JOIN projects ON projects.id=runs.project_id
                   WHERE runs.id=? AND projects.owner_id=?""",
                (run_id, owner_id),
            ).fetchone()
            if not row:
                return None
            result = dict(row)
            result["tasks"] = [
                dict(item)
                for item in connection.execute(
                    "SELECT * FROM agent_tasks WHERE run_id=? ORDER BY sequence", (run_id,)
                ).fetchall()
            ]
            result["defects"] = [
                dict(item)
                for item in connection.execute(
                    "SELECT * FROM defects WHERE run_id=? ORDER BY created_at", (run_id,)
                ).fetchall()
            ]
            usage_rows = connection.execute(
                """SELECT provider, model, prompt_tokens, completion_tokens, estimated_cost_usd
                   FROM model_usage WHERE run_id=? ORDER BY created_at""",
                (run_id,),
            ).fetchall()
            result["usage_summary"] = {
                "prompt_tokens": sum(row["prompt_tokens"] for row in usage_rows),
                "completion_tokens": sum(row["completion_tokens"] for row in usage_rows),
                "estimated_cost_usd": (
                    sum(row["estimated_cost_usd"] for row in usage_rows)
                    if usage_rows
                    and all(row["estimated_cost_usd"] is not None for row in usage_rows)
                    else None
                ),
                "models": [
                    {"provider": row["provider"], "model": row["model"]} for row in usage_rows
                ],
            }
            return result

    def latest_run_for_project(self, project_id: str, owner_id: str) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT runs.id FROM runs JOIN projects ON projects.id=runs.project_id
                   WHERE runs.project_id=? AND projects.owner_id=?
                   ORDER BY runs.created_at DESC LIMIT 1""",
                (project_id, owner_id),
            ).fetchone()
        return self.get_run(row["id"], owner_id) if row else None

    def claim_next_task(self, run_id: str, owner_id: str) -> dict[str, Any] | None:
        run = self.get_run(run_id, owner_id)
        if run is None:
            raise KeyError("run not found")
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT * FROM agent_tasks WHERE run_id=? AND status=?
                   ORDER BY CASE WHEN agent_role=? THEN 0 ELSE 1 END, sequence LIMIT 1""",
                (run_id, TaskStatus.QUEUED.value, run["current_agent"]),
            ).fetchone()
            if not row:
                connection.commit()
                return None
            now = utc_now()
            updated = connection.execute(
                "UPDATE agent_tasks SET status=?,started_at=? WHERE id=? AND status=?",
                (TaskStatus.RUNNING.value, now, row["id"], TaskStatus.QUEUED.value),
            )
            if updated.rowcount != 1:
                connection.rollback()
                return None
            connection.execute(
                "UPDATE runs SET current_agent=?,updated_at=? WHERE id=?",
                (row["agent_role"], now, run_id),
            )
            connection.commit()
            claimed = dict(row)
            claimed["status"] = TaskStatus.RUNNING.value
            return claimed

    def complete_task(
        self, run_id: str, task_id: str, owner_id: str, summary: str, artifact: str, passed: bool
    ) -> None:
        run = self.get_run(run_id, owner_id)
        if run is None:
            raise KeyError("run not found")
        now = utc_now()
        with self.database.connect() as connection:
            connection.execute("BEGIN")
            task = connection.execute(
                "SELECT * FROM agent_tasks WHERE id=? AND run_id=? AND status=?",
                (task_id, run_id, TaskStatus.RUNNING.value),
            ).fetchone()
            if not task:
                raise ValueError("task is not running")
            status = TaskStatus.COMPLETED if passed else TaskStatus.FAILED
            connection.execute(
                "UPDATE agent_tasks SET status=?,summary=?,artifact=?,completed_at=? WHERE id=?",
                (status.value, summary, artifact, now, task_id),
            )
            event_detail = json.dumps({"agent": task["agent_role"], "summary": summary})
            self._audit(
                connection,
                run["project_id"],
                run_id,
                task["agent_role"],
                "agent.completed",
                event_detail,
                now,
            )
            connection.commit()

    def record_model_usage(
        self, run_id: str, task_id: str, owner_id: str, role: AgentRole, usage: ModelUsage
    ) -> None:
        if self.get_run(run_id, owner_id) is None:
            raise KeyError("run not found")
        now = utc_now()
        with self.database.connect() as connection:
            connection.execute(
                """INSERT OR REPLACE INTO model_usage
                   (id,run_id,task_id,agent_role,provider,model,prompt_tokens,completion_tokens,
                    estimated_cost_usd,created_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    str(uuid4()),
                    run_id,
                    task_id,
                    role.value,
                    usage.provider[:40],
                    usage.model[:100],
                    usage.prompt_tokens,
                    usage.completion_tokens,
                    usage.estimated_cost_usd,
                    now,
                ),
            )
            connection.commit()

    def set_run_status(
        self, run_id: str, owner_id: str, status: RunStatus, current_agent: AgentRole | None
    ) -> None:
        run = self.get_run(run_id, owner_id)
        if run is None:
            raise KeyError("run not found")
        now = utc_now()
        with self.database.connect() as connection:
            connection.execute("BEGIN")
            connection.execute(
                "UPDATE runs SET status=?,current_agent=?,updated_at=? WHERE id=?",
                (status.value, current_agent.value if current_agent else None, now, run_id),
            )
            self._audit(
                connection,
                run["project_id"],
                run_id,
                "system",
                "run.status_changed",
                status.value,
                now,
            )
            connection.commit()

    def enqueue_task(self, run_id: str, role: AgentRole) -> None:
        with self.database.connect() as connection:
            sequence = connection.execute(
                """SELECT COALESCE(MAX(sequence),0)+1 AS next_sequence
                   FROM agent_tasks WHERE run_id=?""",
                (run_id,),
            ).fetchone()["next_sequence"]
            connection.execute(
                """INSERT INTO agent_tasks
                   (id,run_id,sequence,agent_role,status,created_at)
                   VALUES(?,?,?,?,?,?)""",
                (str(uuid4()), run_id, sequence, role.value, TaskStatus.QUEUED.value, utc_now()),
            )
            connection.commit()

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
        with self.database.connect() as connection:
            connection.execute("BEGIN")
            connection.execute(
                """INSERT INTO approvals
                   (id,run_id,kind,approved,comment,decided_by,created_at)
                   VALUES(?,?,?,?,?,?,?)""",
                (str(uuid4()), run_id, kind.value, int(approved), comment, owner_id, now),
            )
            self._audit(
                connection,
                run["project_id"],
                run_id,
                owner_id,
                "approval.recorded",
                json.dumps({"kind": kind.value, "approved": approved}),
                now,
            )
            connection.commit()

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
        defect_id = str(uuid4())
        now = utc_now()
        with self.database.connect() as connection:
            connection.execute("BEGIN")
            connection.execute(
                """INSERT INTO defects
                   (id,run_id,title,description,severity,source,status,created_at)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (defect_id, run_id, title, description, severity, source, "open", now),
            )
            self._audit(
                connection,
                run["project_id"],
                run_id,
                source,
                "defect.reported",
                json.dumps({"defect_id": defect_id, "severity": severity, "title": title}),
                now,
            )
            connection.commit()
        return {
            "id": defect_id,
            "run_id": run_id,
            "title": title,
            "description": description,
            "severity": severity,
            "source": source,
            "status": "open",
            "created_at": now,
        }

    def audit_events(self, project_id: str, owner_id: str) -> list[dict[str, Any]]:
        if self.get_project(project_id, owner_id) is None:
            raise KeyError("project not found")
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM audit_events WHERE project_id=? ORDER BY id", (project_id,)
            ).fetchall()
            return [dict(row) for row in rows]

    @staticmethod
    def _audit(
        connection: Any,
        project_id: str,
        run_id: str | None,
        actor: str,
        event_type: str,
        detail: str,
        created_at: str,
    ) -> None:
        connection.execute(
            """INSERT INTO audit_events(project_id,run_id,actor,event_type,detail,created_at)
               VALUES(?,?,?,?,?,?)""",
            (project_id, run_id, actor, event_type, detail, created_at),
        )
