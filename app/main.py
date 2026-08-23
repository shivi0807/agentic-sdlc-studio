from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, Response, status
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles

from .api import router
from .auth import SESSION_COOKIE
from .config import Settings
from .database import Database
from .orchestrator import SDLCOrchestrator
from .providers import build_provider
from .repositories import StudioRepository
from .web import CSRF_COOKIE
from .web import router as web_router
from .workspaces import WorkspaceEngine


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or Settings.from_env()
    database = Database(resolved.database_path)
    repository = StudioRepository(database)
    workspace_engine = WorkspaceEngine(resolved.workspace_root)
    provider = build_provider(
        resolved.agent_provider,
        resolved.ollama_url,
        resolved.ollama_model,
        resolved.gemini_api_key,
        resolved.gemini_model,
    )

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        database.initialize()
        yield

    application = FastAPI(
        title="Agentic SDLC Studio",
        version="0.1.0",
        description="Human-governed multi-agent software delivery workflow",
        lifespan=lifespan,
    )
    application.state.settings = resolved
    application.state.database = database
    application.state.repository = repository
    application.state.workspace_engine = workspace_engine
    application.state.orchestrator = SDLCOrchestrator(repository, provider, workspace_engine)

    @application.middleware("http")
    async def security_middleware(request: Request, call_next: Any) -> Response:
        content_length = request.headers.get("content-length")
        if content_length and content_length.isdigit() and int(content_length) > 1_000_000:
            return PlainTextResponse(
                "Request too large", status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
            )
        response: Response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
            "frame-ancestors 'none'; form-action 'self'; base-uri 'self'"
        )
        if SESSION_COOKIE in request.cookies:
            response.headers["Cache-Control"] = "no-store"
        csrf_token = getattr(request.state, "csrf_token", None)
        if csrf_token:
            response.set_cookie(
                CSRF_COOKIE,
                csrf_token,
                max_age=12 * 60 * 60,
                httponly=True,
                secure=resolved.cookie_secure,
                samesite="strict",
                path="/",
            )
        return response

    application.include_router(router)
    application.include_router(web_router)
    application.mount(
        "/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static"
    )
    return application


app = create_app()
