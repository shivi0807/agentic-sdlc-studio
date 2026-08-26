from __future__ import annotations

import json
import logging
import re
from abc import ABC, abstractmethod
from dataclasses import replace
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from .domain import AgentResult, AgentRole, ModelUsage

logger = logging.getLogger(__name__)

ROLE_INSTRUCTIONS: dict[AgentRole, str] = {
    AgentRole.COORDINATOR: "Coordinate dependencies, stage gates, ownership, and team handoffs.",
    AgentRole.PRODUCT: "Turn the idea into user stories, acceptance criteria, scope, and risks.",
    AgentRole.ARCHITECT: "Propose modular architecture, interfaces, security, and trade-offs.",
    AgentRole.DEVELOPER: (
        "Produce the smallest implementation matching the approved scope and technology "
        "requirements. Use project_type='python-fastapi' when the scope calls "
        "for FastAPI/Jinja2, otherwise project_type='python-stdlib', plus a files object mapping "
        "safe relative paths to text, including focused test coverage."
    ),
    AgentRole.TESTER: "Design validation and report evidence; never invent executed results.",
    AgentRole.REVIEWER: "Independently review requirements, security, quality, and test evidence.",
    AgentRole.DEVOPS: "Prepare a reversible build and release plan; never deploy without approval.",
    AgentRole.SUPPORT: "Reproduce defects, identify root cause, and require regression coverage.",
}
MAX_PROVIDER_REQUEST_BYTES = 32_000
MAX_REQUIREMENT_CHARS = 8_000
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(password|passwd|api[ _-]?key|access[ _-]?token|token|secret)\b"
    r"\s*[:=]\s*([^\s,;]+)"
)
_EMAIL_ADDRESS = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_PRIVATE_KEY = re.compile(
    r"-----BEGIN [^-\r\n]*PRIVATE KEY-----.*?-----END [^-\r\n]*PRIVATE KEY-----",
    re.DOTALL,
)


class ProviderResponseError(ValueError):
    pass


def redact_sensitive_text(value: str, limit: int) -> str:
    """Remove common credentials and direct identifiers before remote inference."""
    redacted = _PRIVATE_KEY.sub("[REDACTED PRIVATE KEY]", value)
    redacted = _SECRET_ASSIGNMENT.sub(lambda match: f"{match.group(1)}=[REDACTED]", redacted)
    redacted = _EMAIL_ADDRESS.sub("[REDACTED EMAIL]", redacted)
    return redacted[:limit]


def validate_provider_result(value: Any) -> AgentResult:
    if not isinstance(value, dict):
        raise ProviderResponseError("Provider response must be an object")
    summary = value.get("summary")
    artifact = value.get("artifact")
    passed = value.get("passed")
    if not isinstance(summary, str) or not summary.strip() or len(summary) > 1000:
        raise ProviderResponseError("Provider summary must be a non-empty string of at most 1000")
    if not isinstance(artifact, dict):
        raise ProviderResponseError("Provider artifact must be an object")
    if type(passed) is not bool:
        raise ProviderResponseError("Provider passed value must be a boolean")
    encoded_artifact = json.dumps(artifact)
    if len(encoded_artifact.encode()) > 50_000:
        raise ProviderResponseError("Provider artifact exceeds 50000 bytes")
    return AgentResult(summary=summary.strip(), artifact=artifact, passed=passed)


def safe_provider_context(context: dict[str, Any]) -> dict[str, Any]:
    project_value = context.get("project")
    project: dict[str, Any] = project_value if isinstance(project_value, dict) else {}
    run_value = context.get("run")
    run: dict[str, Any] = run_value if isinstance(run_value, dict) else {}
    tasks_value = run.get("tasks")
    tasks: list[Any] = tasks_value if isinstance(tasks_value, list) else []
    safe_tasks: list[dict[str, str]] = []
    for task in tasks[-20:]:
        if not isinstance(task, dict):
            continue
        safe_tasks.append(
            {
                "agent_role": str(task.get("agent_role", ""))[:30],
                "status": str(task.get("status", ""))[:30],
                "summary": redact_sensitive_text(str(task.get("summary", "")), 500),
            }
        )
    safe: dict[str, Any] = {
        "project": {
            "name": redact_sensitive_text(str(project.get("name", "")), 100),
            "requirement": redact_sensitive_text(
                str(project.get("requirement", "")), MAX_REQUIREMENT_CHARS
            ),
            "sdlc_style": str(project.get("sdlc_style", ""))[:20],
        },
        "run": {
            "id": str(run.get("id", ""))[:40],
            "status": str(run.get("status", ""))[:40],
            "iteration": int(run.get("iteration", 1))
            if str(run.get("iteration", 1)).isdigit()
            else 1,
            "tasks": safe_tasks,
        },
        "assigned_agent": str(context.get("assigned_agent", ""))[:30],
    }
    review_value = context.get("review_evidence")
    if isinstance(review_value, dict):
        sources_value = review_value.get("source_excerpts")
        sources: dict[str, str] = {}
        if isinstance(sources_value, dict):
            for path, excerpt in list(sources_value.items())[:4]:
                sources[str(path)[:200]] = str(excerpt)[:2_000]
        validation_value = review_value.get("validation")
        validation: dict[str, Any] = {}
        if isinstance(validation_value, dict):
            checks_value = validation_value.get("checks")
            checks = []
            if isinstance(checks_value, list):
                for check in checks_value[:10]:
                    if isinstance(check, dict):
                        checks.append(
                            {
                                "name": str(check.get("name", ""))[:100],
                                "passed": check.get("passed") is True,
                                "exit_code": check.get("exit_code"),
                                "stdout": str(check.get("stdout", ""))[-1_000:],
                                "stderr": str(check.get("stderr", ""))[-1_000:],
                            }
                        )
            validation = {
                "passed": validation_value.get("passed") is True,
                "checks": checks,
            }
        safe["review_evidence"] = {
            "source_excerpts": sources,
            "validation": validation,
        }
    feedback_value = context.get("review_feedback")
    if isinstance(feedback_value, dict):
        safe["review_feedback"] = {
            "summary": redact_sensitive_text(str(feedback_value.get("summary", "")), 2_000),
            "artifact": redact_sensitive_text(str(feedback_value.get("artifact", "")), 6_000),
        }
    return safe


def _encode_payload(payload: dict[str, Any]) -> bytes:
    encoded = json.dumps(payload).encode()
    if len(encoded) > MAX_PROVIDER_REQUEST_BYTES:
        raise ProviderResponseError("Provider request exceeds the safe size limit")
    return encoded


class AgentProvider(ABC):
    """Model boundary. Providers return artifacts; they never mutate repositories or files."""

    trusted_for_local_execution = False

    @abstractmethod
    def run(self, role: AgentRole, context: dict[str, Any]) -> AgentResult:
        raise NotImplementedError


class DeterministicAgentProvider(AgentProvider):
    """Free, offline provider used for local demonstrations and repeatable tests."""

    trusted_for_local_execution = True

    def run(self, role: AgentRole, context: dict[str, Any]) -> AgentResult:
        name = str(context["project"]["name"])
        requirement = str(context["project"]["requirement"])
        style = str(context["project"]["sdlc_style"])
        artifacts: dict[AgentRole, dict[str, Any]] = {
            AgentRole.COORDINATOR: {
                "deliverable": "work_breakdown",
                "workflow": ["plan", "implement", "test", "review", "release"],
                "method": style,
                "team": [role.value for role in AgentRole],
            },
            AgentRole.PRODUCT: {
                "deliverable": "requirements",
                "problem": requirement,
                "user_stories": [f"As a user, I want {requirement[:180]}"],
                "acceptance_criteria": [
                    "The approved requirement is implemented",
                    "Automated validation passes",
                    "An independent review records a decision",
                ],
            },
            AgentRole.ARCHITECT: {
                "deliverable": "architecture",
                "project": name,
                "decisions": [
                    "Use modular boundaries and dependency inversion",
                    "Keep secrets outside source control",
                    "Require human approval before implementation and release",
                ],
            },
            AgentRole.DEVELOPER: {
                "deliverable": "implementation",
                "project_type": "python-stdlib",
                "proposed_files": ["src/application.py", "tests/test_application.py"],
                "changes": ["Implement the approved acceptance criteria", "Add focused unit tests"],
                "files": {
                    "src/__init__.py": "",
                    "src/application.py": (
                        '"""Generated application boundary."""\n\n'
                        f"PROJECT_NAME = {name[:200]!r}\n"
                        f"REQUIREMENT = {requirement[:1000]!r}\n\n\n"
                        "def project_summary() -> str:\n"
                        '    return f"{PROJECT_NAME}: {REQUIREMENT}"\n'
                    ),
                    "tests/test_application.py": (
                        "import unittest\n\n"
                        "from src.application import (\n"
                        "    PROJECT_NAME,\n"
                        "    REQUIREMENT,\n"
                        "    project_summary,\n"
                        ")\n\n\n"
                        "class GeneratedApplicationTests(unittest.TestCase):\n"
                        "    def test_summary_contains_approved_inputs(self) -> None:\n"
                        "        self.assertIn(PROJECT_NAME, project_summary())\n"
                        "        self.assertIn(REQUIREMENT, project_summary())\n"
                    ),
                    "README.md": (
                        f"# {name[:200]}\n\n"
                        "Generated by Agentic SDLC Studio after human plan approval.\n"
                    ),
                },
                "note": "Files are materialized only by the bounded workspace engine.",
            },
            AgentRole.TESTER: {
                "deliverable": "validation_report",
                "checks": ["unittest", "compileall"],
                "result": "delegated_to_workspace_engine",
                "evidence": (
                    "The orchestrator replaces this with real allow-listed command evidence."
                ),
            },
            AgentRole.REVIEWER: {
                "deliverable": "independent_review",
                "decision": "approved",
                "findings": [],
                "checks": [
                    "requirements",
                    "security boundaries",
                    "maintainability",
                    "test evidence",
                ],
            },
            AgentRole.DEVOPS: {
                "deliverable": "release_plan",
                "status": "ready_for_human_approval",
                "steps": [
                    "build immutable artifact",
                    "scan",
                    "deploy to staging",
                    "verify",
                    "promote",
                ],
                "deployment_executed": False,
            },
            AgentRole.SUPPORT: {
                "deliverable": "support_triage",
                "actions": [
                    "reproduce",
                    "root-cause analysis",
                    "regression test",
                    "update team rules",
                ],
            },
        }
        artifact = artifacts[role]
        return AgentResult(
            summary=(
                f"{role.value.title()} agent completed its {artifact['deliverable']} for {name}."
            ),
            artifact=artifact,
            passed=True,
            usage=ModelUsage(
                provider="deterministic", model="built-in-demo", estimated_cost_usd=0.0
            ),
        )


class OllamaAgentProvider(AgentProvider):
    """Optional local open-source model provider using Ollama's HTTP API."""

    def __init__(self, base_url: str, model: str) -> None:
        parsed = urlsplit(base_url)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("Ollama URL must be a loopback HTTP endpoint")
        self.endpoint = f"{base_url.rstrip('/')}/api/generate"
        self.model = model

    def run(self, role: AgentRole, context: dict[str, Any]) -> AgentResult:
        filtered_context = safe_provider_context(context)
        prompt = (
            f"You are the {role.value} agent in a human-governed SDLC. "
            f"{ROLE_INSTRUCTIONS[role]} Return JSON only with keys "
            "summary, artifact, passed. Never claim commands ran unless evidence is present.\n"
            f"Context:\n{json.dumps(filtered_context, default=str)}"
        )
        payload = _encode_payload(
            {"model": self.model, "prompt": prompt, "stream": False, "format": "json"}
        )
        request = Request(  # noqa: S310 - URL is loopback-validated
            self.endpoint, data=payload, headers={"Content-Type": "application/json"}
        )
        with urlopen(request, timeout=120) as response:  # noqa: S310 - loopback only
            wrapper = json.loads(response.read().decode())
        result = validate_provider_result(json.loads(wrapper["response"]))
        return replace(
            result,
            usage=ModelUsage(
                provider="ollama",
                model=self.model,
                prompt_tokens=_usage_count(wrapper.get("prompt_eval_count")),
                completion_tokens=_usage_count(wrapper.get("eval_count")),
                estimated_cost_usd=0.0,
            ),
        )


class GeminiAgentProvider(AgentProvider):
    """Optional Gemini Developer API free-tier provider.

    The API key is sent only in a request header. Free-tier quotas and model
    availability are controlled by Google and are not a zero-cost guarantee.
    """

    def __init__(self, api_key: str, model: str) -> None:
        if not api_key.strip():
            raise ValueError("GEMINI_API_KEY is required for the Gemini provider")
        if not model or any(
            character not in "abcdefghijklmnopqrstuvwxyz0123456789.-_" for character in model
        ):
            raise ValueError("Invalid Gemini model name")
        self.endpoint = (
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        )
        self.api_key = api_key
        self.model = model

    def run(self, role: AgentRole, context: dict[str, Any]) -> AgentResult:
        filtered_context = safe_provider_context(context)
        prompt = (
            f"You are the {role.value} agent in a human-governed SDLC. "
            f"{ROLE_INSTRUCTIONS[role]} Return one JSON object with keys summary, artifact, "
            "and passed. Never claim commands ran unless evidence is present. "
            "Do not include secrets.\nContext:\n"
            f"{json.dumps(filtered_context, default=str)}"
        )
        payload = _encode_payload(
            {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"responseMimeType": "application/json"},
            }
        )
        request = Request(  # noqa: S310 - fixed Google API origin
            self.endpoint,
            data=payload,
            headers={"Content-Type": "application/json", "x-goog-api-key": self.api_key},
        )
        try:
            with urlopen(request, timeout=120) as response:  # noqa: S310 - fixed origin
                wrapper = json.loads(response.read().decode())
        except HTTPError as error:
            # Log Google's safe error envelope (never the API key) so Cloud Run
            # diagnostics distinguish an invalid key from an unavailable model.
            body = error.read().decode("utf-8", errors="replace")[:1_000]
            logger.error(
                "Gemini API request failed: status=%s reason=%s body=%s",
                error.code,
                error.reason,
                body,
            )
            raise
        text = wrapper["candidates"][0]["content"]["parts"][0]["text"]
        result = validate_provider_result(json.loads(text))
        usage = wrapper.get("usageMetadata")
        usage_map = usage if isinstance(usage, dict) else {}
        return replace(
            result,
            usage=ModelUsage(
                provider="gemini",
                model=self.model,
                prompt_tokens=_usage_count(usage_map.get("promptTokenCount")),
                completion_tokens=_usage_count(usage_map.get("candidatesTokenCount")),
                estimated_cost_usd=None,
            ),
        )


def _usage_count(value: Any) -> int:
    return value if isinstance(value, int) and value >= 0 else 0


def build_provider(
    name: str,
    ollama_url: str,
    ollama_model: str,
    gemini_api_key: str = "",
    gemini_model: str = "gemini-2.5-flash-lite",
) -> AgentProvider:
    if name == "deterministic":
        return DeterministicAgentProvider()
    if name == "ollama":
        return OllamaAgentProvider(ollama_url, ollama_model)
    if name == "gemini":
        return GeminiAgentProvider(gemini_api_key, gemini_model)
    raise ValueError(f"Unsupported agent provider: {name}")
