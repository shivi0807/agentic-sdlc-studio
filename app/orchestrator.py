from __future__ import annotations

import json
from typing import Any

from .domain import AgentResult, AgentRole, ApprovalKind, RunStatus
from .providers import AgentProvider, DeterministicAgentProvider
from .repositories import StudioRepository
from .workspaces import WorkspaceEngine


class WorkflowError(ValueError):
    pass


class SDLCOrchestrator:
    def __init__(
        self,
        repository: StudioRepository,
        provider: AgentProvider,
        workspace_engine: WorkspaceEngine,
    ) -> None:
        self.repository = repository
        self.provider = provider
        self.workspace_engine = workspace_engine

    def process_next(self, run_id: str, owner_id: str) -> dict[str, Any]:
        run = self._run(run_id, owner_id)
        blocked = {
            RunStatus.AWAITING_PLAN_APPROVAL.value,
            RunStatus.AWAITING_RELEASE_APPROVAL.value,
            RunStatus.COMPLETED.value,
        }
        if run["status"] in blocked:
            raise WorkflowError("This run is waiting for human action or already complete")
        task = self.repository.claim_next_task(run_id, owner_id)
        if task is None:
            raise WorkflowError("No agent task is queued")
        project = self.repository.get_project(run["project_id"], owner_id)
        if project is None:
            raise KeyError("project not found")
        role = AgentRole(task["agent_role"])
        context = {"project": project, "run": run, "assigned_agent": role.value}
        try:
            result = self._run_agent(role, context, project["id"])
        except Exception:  # Provider, workspace, and validation errors are safely isolated.
            self.repository.complete_task(
                run_id,
                task["id"],
                owner_id,
                "Agent execution failed; the task was safely requeued.",
                json.dumps({"deliverable": "execution_error", "retryable": True}),
                False,
            )
            self._transition_after(role, False, run_id, owner_id)
            return self._run(run_id, owner_id)
        self.repository.complete_task(
            run_id,
            task["id"],
            owner_id,
            result.summary,
            json.dumps(result.artifact, indent=2, sort_keys=True),
            result.passed,
        )
        if result.usage is not None:
            self.repository.record_model_usage(run_id, task["id"], owner_id, role, result.usage)
        if role == AgentRole.TESTER and not result.passed:
            self.repository.record_defect(
                run_id,
                owner_id,
                "Automated validation failed",
                result.summary,
                "high",
                AgentRole.TESTER.value,
            )
        self._transition_after(role, result.passed, run_id, owner_id)
        return self._run(run_id, owner_id)

    def _run_agent(self, role: AgentRole, context: dict[str, Any], project_id: str) -> AgentResult:
        if role == AgentRole.TESTER:
            if type(self.provider) is not DeterministicAgentProvider:
                return AgentResult(
                    summary=(
                        "Validation blocked: externally generated code requires an isolated worker."
                    ),
                    artifact={
                        "deliverable": "validation_blocked",
                        "passed": False,
                        "command_executed": False,
                        "reason": "isolated_worker_required",
                        "message": (
                            "Ollama/Gemini output is never executed inside the Studio process. "
                            "Configure an isolated validation worker before continuing."
                        ),
                    },
                    passed=False,
                )
            evidence = self.workspace_engine.validate(project_id)
            passed = evidence["passed"] is True
            return AgentResult(
                summary=f"Real allow-listed validation {'passed' if passed else 'failed'}.",
                artifact=evidence,
                passed=passed,
            )
        if role == AgentRole.REVIEWER and not self.workspace_engine.review_allowed(project_id):
            return AgentResult(
                summary="Independent review blocked because real validation did not pass.",
                artifact={
                    "deliverable": "independent_review",
                    "decision": "changes_required",
                    "findings": ["Passing real validation evidence is required."],
                },
                passed=False,
            )
        if role == AgentRole.REVIEWER:
            context = {
                **context,
                "review_evidence": self.workspace_engine.review_evidence(project_id),
            }
        result = self.provider.run(role, context)
        if role == AgentRole.DEVELOPER and result.passed:
            workspace = self.workspace_engine.materialize(
                project_id, result.artifact, str(context["project"]["name"])
            )
            result.artifact["workspace"] = workspace
        if role == AgentRole.REVIEWER:
            result.artifact["validation_verified"] = True
        return result

    def approve_plan(
        self, run_id: str, owner_id: str, approved: bool, comment: str | None
    ) -> dict[str, Any]:
        run = self._run(run_id, owner_id)
        if run["status"] != RunStatus.AWAITING_PLAN_APPROVAL.value:
            raise WorkflowError("The run is not waiting for plan approval")
        self.repository.record_approval(run_id, owner_id, ApprovalKind.PLAN, approved, comment)
        if approved:
            project = self.repository.get_project(run["project_id"], owner_id)
            if project is None:
                raise KeyError("project not found")
            work_by_style = {
                "agile": [AgentRole.PRODUCT, AgentRole.DEVELOPER, AgentRole.TESTER],
                "waterfall": [AgentRole.DEVELOPER],
                "hybrid": [AgentRole.DEVELOPER, AgentRole.TESTER, AgentRole.REVIEWER],
            }
            roles = work_by_style[project["sdlc_style"]]
            self.repository.enqueue_tasks(run_id, roles)
            first_role = roles[0]
            self.repository.set_run_status(
                run_id, owner_id, self._status_for(first_role), first_role
            )
        else:
            for role in (AgentRole.COORDINATOR, AgentRole.PRODUCT, AgentRole.ARCHITECT):
                self.repository.enqueue_task(run_id, role)
            self.repository.set_run_status(
                run_id, owner_id, RunStatus.CHANGES_REQUESTED, AgentRole.COORDINATOR
            )
        return self._run(run_id, owner_id)

    def approve_release(
        self, run_id: str, owner_id: str, approved: bool, comment: str | None
    ) -> dict[str, Any]:
        run = self._run(run_id, owner_id)
        if run["status"] != RunStatus.AWAITING_RELEASE_APPROVAL.value:
            raise WorkflowError("The run is not waiting for release approval")
        self.repository.record_approval(run_id, owner_id, ApprovalKind.RELEASE, approved, comment)
        if approved:
            self.repository.set_run_status(run_id, owner_id, RunStatus.COMPLETED, None)
        else:
            self.repository.enqueue_task(run_id, AgentRole.DEVELOPER)
            self.repository.set_run_status(
                run_id, owner_id, RunStatus.CHANGES_REQUESTED, AgentRole.DEVELOPER
            )
        return self._run(run_id, owner_id)

    def report_support_issue(self, run_id: str, owner_id: str, issue: str) -> dict[str, Any]:
        run = self._run(run_id, owner_id)
        if run["status"] != RunStatus.COMPLETED.value:
            raise WorkflowError("Support triage starts only after a completed release")
        self.repository.record_defect(
            run_id, owner_id, "Reported support issue", issue, "medium", AgentRole.SUPPORT.value
        )
        self.repository.enqueue_task(run_id, AgentRole.SUPPORT)
        self.repository.set_run_status(
            run_id, owner_id, RunStatus.CHANGES_REQUESTED, AgentRole.SUPPORT
        )
        return self._run(run_id, owner_id)

    def _transition_after(self, role: AgentRole, passed: bool, run_id: str, owner_id: str) -> None:
        if not passed:
            retry_role = (
                AgentRole.DEVELOPER if role in {AgentRole.TESTER, AgentRole.REVIEWER} else role
            )
            self.repository.enqueue_task(run_id, retry_role)
            retry_status = (
                RunStatus.PLANNING
                if retry_role in {AgentRole.COORDINATOR, AgentRole.PRODUCT, AgentRole.ARCHITECT}
                else RunStatus.CHANGES_REQUESTED
            )
            self.repository.set_run_status(run_id, owner_id, retry_status, retry_role)
            return
        refreshed = self._run(run_id, owner_id)
        queued = [task for task in refreshed["tasks"] if task["status"] == "queued"]
        if queued:
            next_role = AgentRole(queued[0]["agent_role"])
            status = (
                RunStatus.PLANNING
                if next_role
                in {
                    AgentRole.COORDINATOR,
                    AgentRole.PRODUCT,
                    AgentRole.ARCHITECT,
                }
                else self._status_for(next_role)
            )
            self.repository.set_run_status(run_id, owner_id, status, next_role)
            return
        if role in {AgentRole.COORDINATOR, AgentRole.PRODUCT, AgentRole.ARCHITECT}:
            self.repository.set_run_status(run_id, owner_id, RunStatus.AWAITING_PLAN_APPROVAL, None)
        elif role == AgentRole.DEVELOPER:
            self.repository.enqueue_task(run_id, AgentRole.TESTER)
            self.repository.set_run_status(run_id, owner_id, RunStatus.TESTING, AgentRole.TESTER)
        elif role == AgentRole.TESTER:
            self.repository.enqueue_task(run_id, AgentRole.REVIEWER)
            self.repository.set_run_status(
                run_id, owner_id, RunStatus.REVIEWING, AgentRole.REVIEWER
            )
        elif role == AgentRole.REVIEWER:
            self.repository.enqueue_task(run_id, AgentRole.DEVOPS)
            self.repository.set_run_status(run_id, owner_id, RunStatus.REVIEWING, AgentRole.DEVOPS)
        elif role == AgentRole.DEVOPS:
            self.repository.set_run_status(
                run_id, owner_id, RunStatus.AWAITING_RELEASE_APPROVAL, None
            )
        elif role == AgentRole.SUPPORT:
            self.repository.enqueue_task(run_id, AgentRole.DEVELOPER)
            self.repository.set_run_status(
                run_id, owner_id, RunStatus.CHANGES_REQUESTED, AgentRole.DEVELOPER
            )

    @staticmethod
    def _status_for(role: AgentRole) -> RunStatus:
        if role == AgentRole.DEVELOPER:
            return RunStatus.IMPLEMENTING
        if role == AgentRole.TESTER:
            return RunStatus.TESTING
        return RunStatus.REVIEWING

    def _run(self, run_id: str, owner_id: str) -> dict[str, Any]:
        run = self.repository.get_run(run_id, owner_id)
        if run is None:
            raise KeyError("run not found")
        return run
