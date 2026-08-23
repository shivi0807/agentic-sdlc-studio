import pytest

from app.domain import AgentRole
from app.providers import (
    DeterministicAgentProvider,
    GeminiAgentProvider,
    OllamaAgentProvider,
    ProviderResponseError,
    safe_provider_context,
    validate_provider_result,
)


def test_deterministic_provider_returns_structured_artifact() -> None:
    result = DeterministicAgentProvider().run(
        AgentRole.TESTER,
        {
            "project": {
                "name": "Example",
                "requirement": "Build a general application",
                "sdlc_style": "agile",
            }
        },
    )
    assert result.passed is True
    assert result.artifact["deliverable"] == "validation_report"
    assert result.artifact["result"] == "delegated_to_workspace_engine"


def test_ollama_provider_rejects_non_loopback_url() -> None:
    with pytest.raises(ValueError, match="loopback"):
        OllamaAgentProvider("https://untrusted.example", "model")


def test_gemini_provider_requires_a_key_and_safe_model() -> None:
    with pytest.raises(ValueError, match="GEMINI_API_KEY"):
        GeminiAgentProvider("", "gemini-2.5-flash-lite")
    with pytest.raises(ValueError, match="model"):
        GeminiAgentProvider("not-a-real-secret", "../../unsafe")


@pytest.mark.parametrize(
    "result",
    [
        {"summary": "ok", "artifact": [], "passed": True},
        {"summary": "ok", "artifact": {}, "passed": 1},
        {"summary": 3, "artifact": {}, "passed": True},
    ],
)
def test_provider_result_schema_is_strict(result: object) -> None:
    with pytest.raises(ProviderResponseError):
        validate_provider_result(result)


def test_remote_context_redacts_workspace_and_limits_requirement() -> None:
    safe = safe_provider_context(
        {
            "project": {
                "name": "Example",
                "requirement": "x" * 20_000,
                "sdlc_style": "agile",
                "repository_url": "https://example.test/private",
                "workspace_hint": "private/path",
            },
            "run": {"id": "run", "status": "planning", "tasks": []},
            "assigned_agent": "product",
        }
    )
    assert len(safe["project"]["requirement"]) == 8_000
    assert "repository_url" not in safe["project"]
    assert "workspace_hint" not in safe["project"]


def test_remote_reviewer_context_has_bounded_evidence() -> None:
    safe = safe_provider_context(
        {
            "project": {"name": "Review", "requirement": "Requirement", "sdlc_style": "agile"},
            "run": {"id": "run", "status": "reviewing", "tasks": []},
            "assigned_agent": "reviewer",
            "review_evidence": {
                "source_excerpts": {f"src/file{index}.py": "x" * 5_000 for index in range(8)},
                "validation": {
                    "passed": True,
                    "checks": [{"name": "unittest", "passed": True, "stdout": "y" * 5_000}],
                },
            },
        }
    )
    evidence = safe["review_evidence"]
    assert len(evidence["source_excerpts"]) == 4
    assert all(len(excerpt) == 2_000 for excerpt in evidence["source_excerpts"].values())
    assert len(evidence["validation"]["checks"][0]["stdout"]) == 1_000


def test_only_built_in_deterministic_provider_is_locally_trusted() -> None:
    assert DeterministicAgentProvider.trusted_for_local_execution is True
    assert OllamaAgentProvider.trusted_for_local_execution is False
    assert GeminiAgentProvider.trusted_for_local_execution is False


def test_remote_context_redacts_common_secrets_and_email() -> None:
    safe = safe_provider_context(
        {
            "project": {
                "name": "Private customer@example.com",
                "requirement": "API_KEY=top-secret password: hunter2 contact me@example.com",
                "sdlc_style": "agile",
            },
            "run": {"id": "run", "status": "planning", "tasks": []},
            "assigned_agent": "product",
        }
    )
    combined = f"{safe['project']['name']} {safe['project']['requirement']}"
    assert "top-secret" not in combined
    assert "hunter2" not in combined
    assert "example.com" not in combined
    assert "[REDACTED]" in combined
