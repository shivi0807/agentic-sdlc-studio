from __future__ import annotations

import json
import secrets
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Form, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from markupsafe import escape
from pydantic import ValidationError

from .auth import SESSION_COOKIE
from .domain import RunStatus, SDLCStyle
from .orchestrator import SDLCOrchestrator, WorkflowError
from .repositories import StudioRepository
from .schemas import ProjectCreate

router = APIRouter(include_in_schema=False)
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
DEMO_PASSWORD = "DemoOnly!2026"  # noqa: S105 - intentionally public local demo credential
CSRF_COOKIE = "sdlc_csrf"


def _repository(request: Request) -> StudioRepository:
    return request.app.state.repository  # type: ignore[no-any-return]


def _orchestrator(request: Request) -> SDLCOrchestrator:
    return request.app.state.orchestrator  # type: ignore[no-any-return]


def _user_or_none(request: Request) -> dict[str, Any] | None:
    token = request.cookies.get(SESSION_COOKIE)
    return _repository(request).user_for_session(token) if token else None


def _require_user(request: Request) -> dict[str, Any] | RedirectResponse:
    return _user_or_none(request) or RedirectResponse(
        "/login", status_code=status.HTTP_303_SEE_OTHER
    )


def csrf_for(request: Request) -> str:
    existing = request.cookies.get(CSRF_COOKIE)
    if existing and len(existing) >= 32:
        return existing
    token = secrets.token_urlsafe(32)
    request.state.csrf_token = token
    return token


def _validate_csrf(request: Request, submitted: str) -> None:
    expected = request.cookies.get(CSRF_COOKIE, "")
    if not expected or not secrets.compare_digest(expected, submitted):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid CSRF token")


@router.get("/")
def home(request: Request) -> RedirectResponse:
    target = "/projects" if _user_or_none(request) else "/login"
    return RedirectResponse(target, status_code=status.HTTP_303_SEE_OTHER)


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request) -> Response:
    if _user_or_none(request):
        return RedirectResponse("/projects", status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(
        request,
        "login.html",
        {"current_user": None, "csrf_token": csrf_for(request)},
    )


@router.post("/login", response_class=HTMLResponse)
def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    csrf_token: str = Form(...),
) -> Response:
    _validate_csrf(request, csrf_token)
    settings = request.app.state.settings
    if not settings.demo_auth_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    if email.lower().strip() != "developer@demo.local" or password != DEMO_PASSWORD:
        return templates.TemplateResponse(
            request,
            "login.html",
            {
                "current_user": None,
                "email": email,
                "error": "Use the local demo credentials.",
                "csrf_token": csrf_for(request),
            },
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    repository = _repository(request)
    user = repository.ensure_demo_user()
    token = repository.create_session(user["id"])
    response = RedirectResponse("/projects", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=12 * 60 * 60,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )
    return response


@router.post("/logout")
def logout(request: Request, csrf_token: str = Form(...)) -> RedirectResponse:
    _validate_csrf(request, csrf_token)
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        _repository(request).delete_session(token)
    response = RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(SESSION_COOKIE, path="/", samesite="lax")
    return response


@router.get("/projects", response_class=HTMLResponse)
def projects_page(request: Request) -> Response:
    user = _require_user(request)
    if isinstance(user, RedirectResponse):
        return user
    repository = _repository(request)
    projects = []
    awaiting = completed = tasks = 0
    for project in repository.list_projects(user["id"]):
        run = repository.latest_run_for_project(project["id"], user["id"])
        view = _project_view(project, run)
        projects.append(view)
        if run:
            awaiting += int("awaiting" in run["status"])
            completed += int(run["status"] == RunStatus.COMPLETED.value)
            tasks += len(run["tasks"])
    return templates.TemplateResponse(
        request,
        "projects.html",
        {
            "current_user": user,
            "projects": projects,
            "stats": {
                "active": len(projects) - completed,
                "awaiting_approval": awaiting,
                "completed": completed,
                "agent_tasks": tasks,
            },
            "csrf_token": csrf_for(request),
        },
    )


@router.get("/projects/new", response_class=HTMLResponse)
def new_project_page(request: Request) -> Response:
    user = _require_user(request)
    if isinstance(user, RedirectResponse):
        return user
    return templates.TemplateResponse(
        request,
        "new_project.html",
        {
            "current_user": user,
            "form": None,
            "errors": {},
            "csrf_token": csrf_for(request),
        },
    )


@router.post("/projects", response_class=HTMLResponse)
def create_project_page(
    request: Request,
    name: str = Form(...),
    requirements: str = Form(...),
    methodology: str = Form("agile"),
    repository_path: str = Form(""),
    technology_stack: str = Form(""),
    constraints: str = Form(""),
    csrf_token: str = Form(...),
) -> Response:
    _validate_csrf(request, csrf_token)
    user = _require_user(request)
    if isinstance(user, RedirectResponse):
        return user
    form = {
        "name": name,
        "requirements": requirements,
        "methodology": methodology,
        "repository_path": repository_path,
        "technology_stack": technology_stack,
        "constraints": constraints,
    }
    enriched = requirements.strip()
    if technology_stack.strip():
        enriched += f"\n\nPreferred technology stack:\n{technology_stack.strip()}"
    if constraints.strip():
        enriched += f"\n\nConstraints and acceptance criteria:\n{constraints.strip()}"
    try:
        payload = ProjectCreate(
            name=name,
            requirement=enriched,
            sdlc_style=SDLCStyle(methodology),
            workspace_hint=repository_path or None,
        )
    except (ValidationError, ValueError) as error:
        message = error.errors()[0]["msg"] if isinstance(error, ValidationError) else str(error)
        return templates.TemplateResponse(
            request,
            "new_project.html",
            {
                "current_user": user,
                "form": form,
                "errors": {"form": message},
                "csrf_token": csrf_for(request),
            },
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    repository = _repository(request)
    project = repository.create_project(
        user["id"],
        payload.name,
        payload.requirement,
        payload.sdlc_style,
        payload.repository_url,
        payload.workspace_hint,
    )
    repository.create_run(project["id"], user["id"])
    return RedirectResponse(f"/projects/{project['id']}", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/projects/{project_id}", response_class=HTMLResponse)
def project_page(project_id: str, request: Request) -> Response:
    user = _require_user(request)
    if isinstance(user, RedirectResponse):
        return user
    repository = _repository(request)
    project = repository.get_project(project_id, user["id"])
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    run = repository.latest_run_for_project(project_id, user["id"])
    activities = repository.audit_events(project_id, user["id"])
    context = _project_context(project, run, activities)
    context.update({"request": request, "current_user": user, "csrf_token": csrf_for(request)})
    return templates.TemplateResponse(request, "project.html", context)


@router.post("/projects/{project_id}/run-next-stage")
def run_next_stage(
    project_id: str, request: Request, csrf_token: str = Form(...)
) -> RedirectResponse:
    _validate_csrf(request, csrf_token)
    user, run = _web_run(project_id, request)
    try:
        _orchestrator(request).process_next(run["id"], user["id"])
    except WorkflowError:
        pass
    return RedirectResponse(f"/projects/{project_id}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/projects/{project_id}/approve")
def approve_plan(
    project_id: str, request: Request, csrf_token: str = Form(...)
) -> RedirectResponse:
    _validate_csrf(request, csrf_token)
    user, run = _web_run(project_id, request)
    _orchestrator(request).approve_plan(run["id"], user["id"], True, "Approved in UI")
    return RedirectResponse(f"/projects/{project_id}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/projects/{project_id}/request-changes")
def request_changes(
    project_id: str,
    request: Request,
    feedback: str = Form(...),
    csrf_token: str = Form(...),
) -> RedirectResponse:
    _validate_csrf(request, csrf_token)
    user, run = _web_run(project_id, request)
    _orchestrator(request).approve_plan(run["id"], user["id"], False, feedback[:1000])
    return RedirectResponse(f"/projects/{project_id}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/projects/{project_id}/approve-release")
def approve_release(
    project_id: str, request: Request, csrf_token: str = Form(...)
) -> RedirectResponse:
    _validate_csrf(request, csrf_token)
    user, run = _web_run(project_id, request)
    if run["status"] == RunStatus.AWAITING_RELEASE_APPROVAL.value:
        _orchestrator(request).approve_release(
            run["id"], user["id"], True, "Release approved in UI"
        )
    return RedirectResponse(f"/projects/{project_id}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/projects/{project_id}/defects")
def report_defect(
    project_id: str,
    request: Request,
    title: str = Form(...),
    description: str = Form(...),
    severity: str = Form("medium"),
    csrf_token: str = Form(...),
) -> RedirectResponse:
    _validate_csrf(request, csrf_token)
    user, run = _web_run(project_id, request)
    if run["status"] == RunStatus.COMPLETED.value:
        issue = f"[{severity[:20]}] {title[:200]}\n{description[:4000]}"
        _orchestrator(request).report_support_issue(run["id"], user["id"], issue)
    return RedirectResponse(f"/projects/{project_id}", status_code=status.HTTP_303_SEE_OTHER)


def _web_run(project_id: str, request: Request) -> tuple[dict[str, Any], dict[str, Any]]:
    user = _user_or_none(request)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sign in required")
    run = _repository(request).latest_run_for_project(project_id, user["id"])
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
    return user, run


def _project_view(project: dict[str, Any], run: dict[str, Any] | None) -> dict[str, Any]:
    view = dict(project)
    view["requirements"] = project["requirement"]
    view["description"] = project["requirement"][:180]
    view["methodology"] = project["sdlc_style"].title()
    view["status"] = run["status"] if run else "planning"
    view["current_phase"] = _phase(run["status"] if run else "planning")
    view["phase_index"] = {"plan": 1, "implement": 2, "test": 3, "review": 4}.get(
        view["current_phase"], 4
    )
    return view


def _project_context(
    project: dict[str, Any], run: dict[str, Any] | None, events: list[dict[str, Any]]
) -> dict[str, Any]:
    view = _project_view(project, run)
    tasks = run["tasks"] if run else []
    parsed = []
    for task in tasks:
        artifact: Any = None
        if task.get("artifact"):
            try:
                artifact = json.loads(task["artifact"])
            except json.JSONDecodeError:
                artifact = {"content": task["artifact"]}
        parsed.append((task, artifact))
    phase = view["current_phase"]
    plan_items = [
        artifact for task, artifact in parsed if task["agent_role"] in {"product", "architect"}
    ]
    plan = "".join(
        f"<pre>{escape(json.dumps(item, indent=2))}</pre>" for item in plan_items if item
    )
    deliverables = [
        {
            "name": task["summary"],
            "type": task["agent_role"].title(),
            "updated_at": task["completed_at"],
        }
        for task, _artifact in parsed
        if task["status"] == "completed"
    ]
    agents = []
    for role in (
        "coordinator",
        "product",
        "architect",
        "developer",
        "tester",
        "reviewer",
        "devops",
        "support",
    ):
        agents.append(
            {
                "name": f"{role.title()} Agent",
                "role": role,
                "status": "working" if run and run["current_agent"] == role else "idle",
                "current_task": "Active stage" if run and run["current_agent"] == role else "Ready",
            }
        )
    current_agent = run.get("current_agent") if run else None
    next_task = next((task for task in tasks if task["status"] == "queued"), None)
    coordination = {
        "current": current_agent.title() if isinstance(current_agent, str) else "Human approval",
        "next": next_task["agent_role"].title() if next_task else "Release decision",
        "relationship": (
            f"{current_agent.title()} → {next_task['agent_role'].title()}"
            if isinstance(current_agent, str) and next_task
            else "Human → delivery team"
        ),
        "working": sum(task["status"] == "running" for task in tasks),
        "queued": sum(task["status"] == "queued" for task in tasks),
        "completed": sum(task["status"] == "completed" for task in tasks),
    }
    approval_gate = None
    if run and run["status"] == RunStatus.AWAITING_PLAN_APPROVAL.value:
        approval_gate = {
            "id": "plan",
            "title": "Approve the delivery plan",
            "description": "Review Product and Architecture outputs before implementation.",
        }
    run_status = run["status"] if run else RunStatus.PLANNING.value
    run_blocked = run_status in {
        RunStatus.AWAITING_PLAN_APPROVAL.value,
        RunStatus.AWAITING_RELEASE_APPROVAL.value,
        RunStatus.COMPLETED.value,
    }
    review_artifact: dict[str, Any] = (
        next((artifact for task, artifact in parsed if task["agent_role"] == "reviewer"), {}) or {}
    )
    return {
        "project": view,
        "current_phase": phase,
        "approval_gate": approval_gate,
        "run_blocked": run_blocked,
        "release_gate": run_status == RunStatus.AWAITING_RELEASE_APPROVAL.value,
        "plan": plan,
        "implementation": {"completed": 0, "total": 1, "changes": []},
        "test_results": [],
        "review": {
            "title": "Independent review",
            "summary": review_artifact.get("decision", "Pending"),
            "findings": review_artifact.get("findings", []),
        },
        "deliverables": deliverables,
        "defects": run.get("defects", []) if run else [],
        "agents": agents,
        "run_id": run["id"] if run else None,
        "usage": run.get("usage_summary", {}) if run else {},
        "coordination": coordination,
        "activities": [
            {
                "actor": event["actor"],
                "message": event["detail"],
                "created_at": event["created_at"],
            }
            for event in reversed(events)
        ],
    }


def _phase(status_value: str) -> str:
    if status_value in {RunStatus.PLANNING.value, RunStatus.AWAITING_PLAN_APPROVAL.value}:
        return "plan"
    if status_value in {RunStatus.IMPLEMENTING.value, RunStatus.CHANGES_REQUESTED.value}:
        return "implement"
    if status_value == RunStatus.TESTING.value:
        return "test"
    if status_value == RunStatus.REVIEWING.value:
        return "review"
    return "complete" if status_value == RunStatus.COMPLETED.value else "release"
