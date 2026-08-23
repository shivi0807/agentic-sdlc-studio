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

    @classmethod
    def from_env(cls) -> Settings:
        environment = os.getenv("APP_ENV", "development").lower()
        demo_enabled = os.getenv("DEMO_AUTH_ENABLED", "true").lower() == "true"
        if environment == "production" and demo_enabled:
            raise RuntimeError("Local demo authentication cannot be enabled in production")
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
        )
