from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from .auth import SESSION_COOKIE, current_user
from .orchestrator import SDLCOrchestrator, WorkflowError
from .repositories import StudioRepository
from .schemas import ApprovalInput, ProjectCreate, SupportInput

router = APIRouter()
User = Annotated[dict[str, Any], Depends(current_user)]


def _repository(request: Request) -> StudioRepository:
    return request.app.state.repository  # type: ignore[no-any-return]


def _orchestrator(request: Request) -> SDLCOrchestrator:
    return request.app.state.orchestrator  # type: ignore[no-any-return]


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "Agentic SDLC Studio"}


@router.post("/auth/demo")
def demo_login(request: Request, response: Response) -> dict[str, Any]:
    settings = request.app.state.settings
    if not settings.demo_auth_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    repository = _repository(request)
    user = repository.ensure_demo_user()
    token = repository.create_session(user["id"])
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=12 * 60 * 60,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )
    return {"user": user}


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request, response: Response) -> None:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        _repository(request).delete_session(token)
    response.delete_cookie(SESSION_COOKIE, path="/", samesite="lax")


@router.get("/api/me")
def me(user: User) -> dict[str, Any]:
    return user


@router.get("/api/projects")
def list_projects(request: Request, user: User) -> list[dict[str, Any]]:
    return _repository(request).list_projects(user["id"])


@router.post("/api/projects", status_code=status.HTTP_201_CREATED)
def create_project(payload: ProjectCreate, request: Request, user: User) -> dict[str, Any]:
    return _repository(request).create_project(
        owner_id=user["id"],
        name=payload.name,
        requirement=payload.requirement,
        style=payload.sdlc_style,
        repository_url=payload.repository_url,
        workspace_hint=payload.workspace_hint,
    )


@router.get("/api/projects/{project_id}")
def get_project(project_id: str, request: Request, user: User) -> dict[str, Any]:
    project = _repository(request).get_project(project_id, user["id"])
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return project


@router.post("/api/projects/{project_id}/runs", status_code=status.HTTP_201_CREATED)
def create_run(project_id: str, request: Request, user: User) -> dict[str, Any]:
    try:
        return _repository(request).create_run(project_id, user["id"])
    except KeyError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error


@router.get("/api/runs/{run_id}")
def get_run(run_id: str, request: Request, user: User) -> dict[str, Any]:
    run = _repository(request).get_run(run_id, user["id"])
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
    return run


@router.post("/api/runs/{run_id}/next")
def process_next(run_id: str, request: Request, user: User) -> dict[str, Any]:
    try:
        return _orchestrator(request).process_next(run_id, user["id"])
    except KeyError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except WorkflowError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error


@router.post("/api/runs/{run_id}/plan-approval")
def approve_plan(
    run_id: str, payload: ApprovalInput, request: Request, user: User
) -> dict[str, Any]:
    try:
        return _orchestrator(request).approve_plan(
            run_id, user["id"], payload.approved, payload.comment
        )
    except KeyError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except WorkflowError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error


@router.post("/api/runs/{run_id}/release-approval")
def approve_release(
    run_id: str, payload: ApprovalInput, request: Request, user: User
) -> dict[str, Any]:
    try:
        return _orchestrator(request).approve_release(
            run_id, user["id"], payload.approved, payload.comment
        )
    except KeyError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except WorkflowError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error


@router.post("/api/runs/{run_id}/support")
def support_issue(
    run_id: str, payload: SupportInput, request: Request, user: User
) -> dict[str, Any]:
    try:
        return _orchestrator(request).report_support_issue(run_id, user["id"], payload.issue)
    except KeyError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except WorkflowError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error


@router.get("/api/projects/{project_id}/audit")
def audit_events(project_id: str, request: Request, user: User) -> list[dict[str, Any]]:
    try:
        return _repository(request).audit_events(project_id, user["id"])
    except KeyError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
