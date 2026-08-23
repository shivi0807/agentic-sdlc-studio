from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class SDLCStyle(StrEnum):
    AGILE = "agile"
    WATERFALL = "waterfall"
    HYBRID = "hybrid"


class RunStatus(StrEnum):
    PLANNING = "planning"
    AWAITING_PLAN_APPROVAL = "awaiting_plan_approval"
    IMPLEMENTING = "implementing"
    TESTING = "testing"
    REVIEWING = "reviewing"
    AWAITING_RELEASE_APPROVAL = "awaiting_release_approval"
    COMPLETED = "completed"
    CHANGES_REQUESTED = "changes_requested"


class AgentRole(StrEnum):
    COORDINATOR = "coordinator"
    PRODUCT = "product"
    ARCHITECT = "architect"
    DEVELOPER = "developer"
    TESTER = "tester"
    REVIEWER = "reviewer"
    DEVOPS = "devops"
    SUPPORT = "support"


class TaskStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class ApprovalKind(StrEnum):
    PLAN = "plan"
    RELEASE = "release"


@dataclass(frozen=True)
class ModelUsage:
    """Provider-reported usage attached to one completed agent task."""

    provider: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    estimated_cost_usd: float | None = None


@dataclass(frozen=True)
class AgentResult:
    summary: str
    artifact: dict[str, Any]
    passed: bool = True
    usage: ModelUsage | None = None
