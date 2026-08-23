from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    database_path: Path
    demo_auth_enabled: bool
    cookie_secure: bool
    agent_provider: str
    ollama_url: str
    ollama_model: str
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash-lite"
    workspace_root: Path = Path("workspaces")
    auth_mode: str = "demo"
    google_oauth_client_id: str = ""
    google_oauth_client_secret: str = ""
    google_oauth_redirect_uri: str = ""
    persistence_backend: str = "sqlite"
    firestore_project_id: str = ""
    workspace_backend: str = "local"
    cloud_storage_bucket: str = ""

    @classmethod
    def from_env(cls) -> Settings:
        environment = os.getenv("APP_ENV", "development").lower()
        demo_enabled = os.getenv("DEMO_AUTH_ENABLED", "true").lower() == "true"
        if environment == "production" and demo_enabled:
            raise RuntimeError("Local demo authentication cannot be enabled in production")
        auth_mode = os.getenv("AUTH_MODE", "demo").lower()
        if auth_mode not in {"demo", "google"}:
            raise RuntimeError("AUTH_MODE must be demo or google")
        client_id = os.getenv("GOOGLE_OAUTH_CLIENT_ID", "")
        client_secret = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET", "")
        redirect_uri = os.getenv("GOOGLE_OAUTH_REDIRECT_URI", "")
        if environment == "production" and auth_mode != "google":
            raise RuntimeError("Production requires AUTH_MODE=google")
        if auth_mode == "google" and not all((client_id, client_secret, redirect_uri)):
            raise RuntimeError("Google sign-in requires OAuth client ID, secret, and redirect URI")
        persistence_backend = os.getenv("PERSISTENCE_BACKEND", "sqlite").lower()
        workspace_backend = os.getenv("WORKSPACE_BACKEND", "local").lower()
        if persistence_backend not in {"sqlite", "firestore"}:
            raise RuntimeError("PERSISTENCE_BACKEND must be sqlite or firestore")
        if workspace_backend not in {"local", "gcs"}:
            raise RuntimeError("WORKSPACE_BACKEND must be local or gcs")
        firestore_project_id = os.getenv("FIRESTORE_PROJECT_ID", "")
        cloud_storage_bucket = os.getenv("CLOUD_STORAGE_BUCKET", "")
        if environment == "production" and persistence_backend != "firestore":
            raise RuntimeError("Production requires PERSISTENCE_BACKEND=firestore")
        if environment == "production" and workspace_backend != "gcs":
            raise RuntimeError("Production requires WORKSPACE_BACKEND=gcs")
        if persistence_backend == "firestore" and not firestore_project_id:
            raise RuntimeError("Firestore requires FIRESTORE_PROJECT_ID")
        if workspace_backend == "gcs" and not cloud_storage_bucket:
            raise RuntimeError("GCS workspaces require CLOUD_STORAGE_BUCKET")
        return cls(
            database_path=Path(os.getenv("DATABASE_PATH", "data/agentic_sdlc.db")),
            demo_auth_enabled=demo_enabled,
            cookie_secure=environment == "production",
            agent_provider=os.getenv("AGENT_PROVIDER", "deterministic").lower(),
            ollama_url=os.getenv("OLLAMA_URL", "http://127.0.0.1:11434"),
            ollama_model=os.getenv("OLLAMA_MODEL", "qwen2.5-coder:3b"),
            gemini_api_key=os.getenv("GEMINI_API_KEY", ""),
            gemini_model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite"),
            workspace_root=Path(os.getenv("WORKSPACE_ROOT", "workspaces")),
            auth_mode=auth_mode,
            google_oauth_client_id=client_id,
            google_oauth_client_secret=client_secret,
            google_oauth_redirect_uri=redirect_uri,
            persistence_backend=persistence_backend,
            firestore_project_id=firestore_project_id,
            workspace_backend=workspace_backend,
            cloud_storage_bucket=cloud_storage_bucket,
        )
