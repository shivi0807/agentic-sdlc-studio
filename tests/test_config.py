from __future__ import annotations

import pytest

from app.config import Settings


def test_production_rejects_deterministic_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DEMO_AUTH_ENABLED", "false")
    monkeypatch.setenv("AUTH_MODE", "google")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "client")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "secret")
    monkeypatch.setenv("GOOGLE_OAUTH_REDIRECT_URI", "https://example.test/callback")
    monkeypatch.setenv("PERSISTENCE_BACKEND", "firestore")
    monkeypatch.setenv("FIRESTORE_PROJECT_ID", "example-project")
    monkeypatch.setenv("WORKSPACE_BACKEND", "gcs")
    monkeypatch.setenv("CLOUD_STORAGE_BUCKET", "example-bucket")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("AGENT_PROVIDER", "deterministic")

    with pytest.raises(RuntimeError, match="AGENT_PROVIDER=gemini"):
        Settings.from_env()
