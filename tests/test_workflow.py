import re
from pathlib import Path
from typing import Any
from unittest.mock import Mock

from fastapi.testclient import TestClient

from app.config import Settings
from app.domain import AgentResult, AgentRole, SDLCStyle
from app.firestore_repository import FirestoreStudioRepository
from app.main import create_app
from app.providers import AgentProvider, DeterministicAgentProvider
from app.schemas import ProjectCreate


def settings(path: Path, demo_auth_enabled: bool = True) -> Settings:
    return Settings(
        database_path=path,
        demo_auth_enabled=demo_auth_enabled,
        cookie_secure=False,
        agent_provider="deterministic",
        ollama_url="http://127.0.0.1:11434",
        ollama_model="qwen2.5-coder:3b",
        workspace_root=path.parent / "workspaces",
    )


def test_project_requirements_accept_windows_line_endings() -> None:
    project = ProjectCreate(
        name="Team Task Tracker",
        requirement="Build a task tracker.\r\nInclude login and tests.",
        sdlc_style=SDLCStyle.AGILE,
    )
    assert project.requirement == "Build a task tracker.\nInclude login and tests."


def test_complete_human_governed_sdlc(tmp_path: Path) -> None:
    app = create_app(settings(tmp_path / "studio.db"))
    with TestClient(app) as client:
        assert client.post("/auth/demo").status_code == 200
        project_response = client.post(
            "/api/projects",
            json={
                "name": "General Software Product",
                "requirement": "Build a secure task tracking application for distributed teams.",
                "sdlc_style": "agile",
                "repository_url": "https://github.com/example/project",
                "workspace_hint": "projects/task-tracker",
            },
        )
        assert project_response.status_code == 201
        project = project_response.json()
        run_response = client.post(f"/api/projects/{project['id']}/runs")
        assert run_response.status_code == 201
        run = run_response.json()

        for expected_role in ("product", "architect", None):
            response = client.post(f"/api/runs/{run['id']}/next")
            assert response.status_code == 200
            run = response.json()
            assert run["current_agent"] == expected_role
        assert run["status"] == "awaiting_plan_approval"

        response = client.post(
            f"/api/runs/{run['id']}/plan-approval",
            json={"approved": True, "comment": "Plan approved"},
        )
        assert response.status_code == 200
        assert response.json()["current_agent"] == "product"

        for expected_role in ("developer", "tester", "reviewer", "devops", None):
            response = client.post(f"/api/runs/{run['id']}/next")
            assert response.status_code == 200
            assert response.json()["current_agent"] == expected_role
        assert response.json()["status"] == "awaiting_release_approval"

        response = client.post(
            f"/api/runs/{run['id']}/release-approval",
            json={"approved": True, "comment": "Release approved"},
        )
        assert response.status_code == 200
        assert response.json()["status"] == "completed"
        usage = response.json()["usage_summary"]
        assert usage["prompt_tokens"] == 0
        assert usage["completion_tokens"] == 0
        assert usage["estimated_cost_usd"] == 0.0
        assert len(usage["models"]) == 7

        audit = client.get(f"/api/projects/{project['id']}/audit")
        assert audit.status_code == 200
        event_types = [event["event_type"] for event in audit.json()]
        assert "project.created" in event_types
        assert event_types.count("agent.completed") == 8
        assert event_types.count("approval.recorded") == 2


def test_auth_and_ownership_are_enforced(tmp_path: Path) -> None:
    app = create_app(settings(tmp_path / "studio.db"))
    with TestClient(app) as client:
        assert client.get("/api/projects").status_code == 401
        assert client.post("/auth/demo").status_code == 200
        assert client.get("/api/projects/not-owned").status_code == 404
        assert client.post("/auth/logout").status_code == 204
        assert client.get("/api/projects").status_code == 401


def test_project_delete_removes_owner_project_and_workspace(tmp_path: Path) -> None:
    app = create_app(settings(tmp_path / "studio.db"))
    with TestClient(app) as client:
        assert client.post("/auth/demo").status_code == 200
        project = client.post(
            "/api/projects",
            json={
                "name": "Disposable Demo",
                "requirement": "Build a small text-only demonstration project.",
                "sdlc_style": "agile",
            },
        ).json()
        app.state.workspace_engine.materialize(
            project["id"],
            {
                "project_type": "python-stdlib",
                "files": {"src/__init__.py": ""},
            },
        )

        response = client.delete(f"/api/projects/{project['id']}")

        assert response.status_code == 204
        assert client.get(f"/api/projects/{project['id']}").status_code == 404
        assert not (tmp_path / "workspaces" / project["id"]).exists()


def test_project_input_rejects_unsafe_workspace_and_controls(tmp_path: Path) -> None:
    app = create_app(settings(tmp_path / "studio.db"))
    with TestClient(app) as client:
        client.post("/auth/demo")
        unsafe_path = client.post(
            "/api/projects",
            json={
                "name": "Unsafe project",
                "requirement": "Build a normal application requirement.",
                "sdlc_style": "waterfall",
                "workspace_hint": "../outside",
            },
        )
        assert unsafe_path.status_code == 422
        control = client.post(
            "/api/projects",
            json={
                "name": "Bad\u0000name",
                "requirement": "Build a normal application requirement.",
                "sdlc_style": "hybrid",
            },
        )
        assert control.status_code == 422


def test_demo_auth_can_be_disabled(tmp_path: Path) -> None:
    app = create_app(settings(tmp_path / "studio.db", demo_auth_enabled=False))
    with TestClient(app) as client:
        assert client.post("/auth/demo").status_code == 404


def test_firestore_run_presentation_summarizes_usage_without_leaking_storage_fields() -> None:
    run = FirestoreStudioRepository._present_run(  # noqa: SLF001 - pure adapter contract test
        {
            "id": "run-1",
            "project_id": "project-1",
            "tasks": [],
            "defects": [],
            "usage": [
                {
                    "provider": "gemini",
                    "model": "gemini-2.5-flash-lite",
                    "prompt_tokens": 100,
                    "completion_tokens": 25,
                    "estimated_cost_usd": 0.001,
                },
                {
                    "provider": "gemini",
                    "model": "gemini-2.5-flash-lite",
                    "prompt_tokens": 50,
                    "completion_tokens": 10,
                    "estimated_cost_usd": 0.002,
                },
            ],
        }
    )

    assert "usage" not in run
    assert run["usage_summary"] == {
        "prompt_tokens": 150,
        "completion_tokens": 35,
        "estimated_cost_usd": 0.003,
        "models": [
            {"provider": "gemini", "model": "gemini-2.5-flash-lite"},
            {"provider": "gemini", "model": "gemini-2.5-flash-lite"},
        ],
    }


def test_google_sign_in_start_uses_state_and_keeps_demo_api_disabled(tmp_path: Path) -> None:
    cloud_settings = Settings(
        database_path=tmp_path / "studio.db",
        demo_auth_enabled=False,
        cookie_secure=True,
        agent_provider="deterministic",
        ollama_url="http://127.0.0.1:11434",
        ollama_model="qwen2.5-coder:3b",
        workspace_root=tmp_path / "workspaces",
        auth_mode="google",
        google_oauth_client_id="test-client.apps.googleusercontent.com",
        google_oauth_client_secret="test-secret",  # noqa: S106 - non-secret test value
        google_oauth_redirect_uri="https://studio.example/auth/google/callback",
    )
    app = create_app(cloud_settings)
    with TestClient(app) as client:
        response = client.get("/auth/google/start", follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"].startswith(
            "https://accounts.google.com/o/oauth2/v2/auth?"
        )
        assert "sdlc_google_oauth" in response.headers["set-cookie"]
        assert client.post("/auth/demo").status_code == 404


def test_html_demo_flow_renders_control_room(tmp_path: Path) -> None:
    app = create_app(settings(tmp_path / "studio.db"))
    with TestClient(app) as client:
        login_page = client.get("/login")
        assert re.search(
            r'<form method="post" action="/login"[^>]*>.*name="csrf_token"',
            login_page.text,
            re.DOTALL,
        )
        csrf_token = client.cookies["sdlc_csrf"]
        login = client.post(
            "/login",
            data={
                "email": "developer@demo.local",
                "password": "DemoOnly!2026",
                "csrf_token": csrf_token,
            },
            follow_redirects=False,
        )
        assert login.status_code == 303
        created = client.post(
            "/projects",
            data={
                "name": "Issue Tracker",
                "requirements": "Build a general issue tracker for a software development team.",
                "methodology": "hybrid",
                "repository_path": "workspaces/issue-tracker",
                "technology_stack": "Python and FastAPI",
                "constraints": "Must run locally without paid services.",
                "csrf_token": csrf_token,
            },
            follow_redirects=False,
        )
        assert created.status_code == 303
        control_room = client.get(created.headers["location"])
        assert control_room.status_code == 200
        assert "Issue Tracker" in control_room.text
        assert "Coordinator Agent" in control_room.text
        assert control_room.headers["cache-control"] == "no-store"
        assert control_room.headers["x-frame-options"] == "DENY"


def test_html_post_requires_csrf(tmp_path: Path) -> None:
    app = create_app(settings(tmp_path / "studio.db"))
    with TestClient(app) as client:
        response = client.post(
            "/login",
            data={"email": "developer@demo.local", "password": "DemoOnly!2026"},
        )
        assert response.status_code == 422


def test_methodologies_have_distinct_observable_sequences(tmp_path: Path) -> None:
    app = create_app(settings(tmp_path / "studio.db"))
    expected = {
        "agile": ["coordinator", "product", "architect"],
        "waterfall": ["product", "architect", "coordinator"],
        "hybrid": ["architect", "product", "coordinator"],
    }
    with TestClient(app) as client:
        client.post("/auth/demo")
        for style, roles in expected.items():
            project = client.post(
                "/api/projects",
                json={
                    "name": f"{style.title()} Project",
                    "requirement": "Build a general software system with governed delivery.",
                    "sdlc_style": style,
                },
            ).json()
            run = client.post(f"/api/projects/{project['id']}/runs").json()
            assert [task["agent_role"] for task in run["tasks"]] == roles


def test_methodologies_schedule_different_delivery_work(tmp_path: Path) -> None:
    app = create_app(settings(tmp_path / "studio.db"))
    expected = {
        "agile": ["product", "developer", "tester"],
        "waterfall": ["developer"],
        "hybrid": ["developer", "tester", "reviewer"],
    }
    with TestClient(app) as client:
        client.post("/auth/demo")
        for style, delivery_roles in expected.items():
            project = client.post(
                "/api/projects",
                json={
                    "name": f"{style.title()} Delivery",
                    "requirement": "Build software using the selected lifecycle behavior.",
                    "sdlc_style": style,
                },
            ).json()
            run = client.post(f"/api/projects/{project['id']}/runs").json()
            for _ in range(3):
                run = client.post(f"/api/runs/{run['id']}/next").json()
            approved = client.post(
                f"/api/runs/{run['id']}/plan-approval", json={"approved": True}
            ).json()
            assert [task["agent_role"] for task in approved["tasks"][-len(delivery_roles) :]] == (
                delivery_roles
            )


class BrokenProvider(AgentProvider):
    def run(self, role: AgentRole, context: dict[str, object]) -> object:  # type: ignore[override]
        raise RuntimeError("provider unavailable")


class ExternalProvider(AgentProvider):
    def run(self, role: AgentRole, context: dict[str, Any]) -> AgentResult:
        return DeterministicAgentProvider().run(role, context)


class DeterministicSubclass(DeterministicAgentProvider):
    pass


def test_external_provider_code_is_never_validated_in_studio_process(tmp_path: Path) -> None:
    app = create_app(settings(tmp_path / "studio.db"))
    app.state.orchestrator.provider = ExternalProvider()
    validate = Mock(side_effect=AssertionError("subprocess validation must not run"))
    app.state.workspace_engine.validate = validate
    result = app.state.orchestrator._run_agent(  # noqa: SLF001 - security boundary test
        AgentRole.TESTER, {}, "safe-project-id"
    )
    assert result.passed is False
    assert result.artifact["reason"] == "isolated_worker_required"
    assert result.artifact["command_executed"] is False
    validate.assert_not_called()


def test_deterministic_subclass_does_not_inherit_execution_trust(tmp_path: Path) -> None:
    app = create_app(settings(tmp_path / "studio.db"))
    app.state.orchestrator.provider = DeterministicSubclass()
    validate = Mock(side_effect=AssertionError("subclass code must not run"))
    app.state.workspace_engine.validate = validate
    result = app.state.orchestrator._run_agent(  # noqa: SLF001 - security boundary test
        AgentRole.TESTER, {}, "safe-project-id"
    )
    assert result.passed is False
    assert result.artifact["reason"] == "isolated_worker_required"
    validate.assert_not_called()


def test_provider_exception_fails_and_requeues_without_advancing(tmp_path: Path) -> None:
    app = create_app(settings(tmp_path / "studio.db"))
    with TestClient(app) as client:
        client.post("/auth/demo")
        project = client.post(
            "/api/projects",
            json={
                "name": "Resilient Project",
                "requirement": "Build a general project that safely recovers from provider errors.",
                "sdlc_style": "agile",
            },
        ).json()
        run = client.post(f"/api/projects/{project['id']}/runs").json()
        app.state.orchestrator.provider = BrokenProvider()
        recovered = client.post(f"/api/runs/{run['id']}/next").json()
        assert recovered["status"] == "planning"
        assert recovered["current_agent"] == "coordinator"
        statuses = [task["status"] for task in recovered["tasks"]]
        assert "running" not in statuses
        assert statuses.count("failed") == 1
        assert statuses.count("queued") == 3
        app.state.orchestrator.provider = DeterministicAgentProvider()
        retried = client.post(f"/api/runs/{run['id']}/next").json()
        completed_roles = [
            task["agent_role"] for task in retried["tasks"] if task["status"] == "completed"
        ]
        assert completed_roles == ["coordinator"]
        assert retried["current_agent"] == "product"


class FailingGeneratedProjectProvider(DeterministicAgentProvider):
    def run(self, role: AgentRole, context: dict[str, Any]) -> AgentResult:
        result = super().run(role, context)
        if role == AgentRole.DEVELOPER:
            files = result.artifact["files"]
            assert isinstance(files, dict)
            files["tests/test_application.py"] = (
                "import unittest\n\n\n"
                "class GeneratedApplicationTests(unittest.TestCase):\n"
                "    def test_regression(self) -> None:\n"
                '        self.fail("real validation failure")\n'
            )
        return result


def test_real_test_failure_returns_work_to_developer_and_skips_review(tmp_path: Path) -> None:
    app = create_app(settings(tmp_path / "studio.db"))
    provider = DeterministicAgentProvider()
    failing_provider = FailingGeneratedProjectProvider()
    provider.run = failing_provider.run  # type: ignore[method-assign]
    app.state.orchestrator.provider = provider
    with TestClient(app) as client:
        client.post("/auth/demo")
        project = client.post(
            "/api/projects",
            json={
                "name": "Failing generated project",
                "requirement": "Build a general Python project and run its real tests.",
                "sdlc_style": "agile",
            },
        ).json()
        run = client.post(f"/api/projects/{project['id']}/runs").json()
        for _ in range(3):
            run = client.post(f"/api/runs/{run['id']}/next").json()
        run = client.post(
            f"/api/runs/{run['id']}/plan-approval",
            json={"approved": True, "comment": "Plan approved"},
        ).json()
        run = client.post(f"/api/runs/{run['id']}/next").json()  # Agile product handoff.
        run = client.post(f"/api/runs/{run['id']}/next").json()  # Materialize files.
        workspace = tmp_path / "workspaces" / project["id"]
        assert (workspace / "src/application.py").is_file()

        run = client.post(f"/api/runs/{run['id']}/next").json()  # Execute real tests.

        assert run["status"] == "changes_requested"
        assert run["current_agent"] == "developer"
        tester_tasks = [task for task in run["tasks"] if task["agent_role"] == "tester"]
        assert tester_tasks[-1]["status"] == "failed"
        assert "real validation failure" in tester_tasks[-1]["artifact"]
        assert not any(
            task["agent_role"] == "reviewer" and task["status"] == "completed"
            for task in run["tasks"]
        )
        assert run["defects"][-1]["title"] == "Automated validation failed"

        # Two more failed validation cycles exhaust the bounded retry budget.
        for _ in range(4):
            run = client.post(f"/api/runs/{run['id']}/next").json()
        assert run["status"] == "changes_requested"
        assert run["current_agent"] is None
        assert not any(task["status"] == "queued" for task in run["tasks"])
