from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timezone
from time import monotonic
from typing import Any
from uuid import uuid4

from app.agents.service import agent_service
from app.core.registry import registry
from app.core.config import settings
from app.db.repository import (
    claim_workflow_run,
    enqueue_workflow_run_record,
    get_or_create_run_step,
    get_workflow_record,
    get_workflow_run_record,
    get_workflow_run_record_unscoped,
    list_run_step_records,
    request_workflow_run_cancel,
    save_event_record,
    save_run_step,
    save_workflow_run_record,
)
from app.events.bus import event_bus
from app.execution.engine import RunCancelled, StepExecutionFailed, WorkerLeaseLost, execute_step
from app.external.service import external_execution_service
from app.external.backends.base import ExternalBackendError
from app.models.schemas import (
    ExternalExecutionRecord,
    PlatformEvent,
    RunStepRecord,
    WorkflowRunRecord,
)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


class WorkflowService:
    async def enqueue_workflow(
        self,
        workflow_id: str,
        payload: dict[str, Any],
        app_id: str,
        context: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
        max_attempts: int = 3,
    ) -> WorkflowRunRecord:
        if not get_workflow_record(workflow_id, app_id):
            raise KeyError(f"Unknown workflow_id: {workflow_id}")

        run = WorkflowRunRecord(
            workflow_id=workflow_id,
            app_id=app_id,
            status="queued",
            input=payload,
            context={**(context or {}), "app_id": app_id},
            idempotency_key=idempotency_key,
            max_attempts=max_attempts,
        )
        persisted, created = enqueue_workflow_run_record(run)
        if created:
            await self._publish(
                app_id,
                "workflow.run.queued",
                {"run_id": persisted.run_id, "workflow_id": workflow_id},
            )
        return persisted

    async def run_workflow(
        self,
        workflow_id: str,
        payload: dict[str, Any],
        app_id: str,
        context: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
        max_attempts: int = 3,
    ) -> WorkflowRunRecord:
        """Compatibility path that queues, claims, and awaits one durable run."""
        run = await self.enqueue_workflow(
            workflow_id,
            payload,
            app_id,
            context,
            idempotency_key,
            max_attempts,
        )
        if run.status in {"completed", "failed", "cancelled"}:
            return run
        worker_id = f"inline-{uuid4().hex[:12]}"
        claimed = claim_workflow_run(run.run_id, worker_id)
        if claimed:
            return await self.execute_claimed_run(claimed.run_id, worker_id)
        return await self._wait_for_terminal(run.run_id, app_id)

    async def execute_claimed_run(self, run_id: str, worker_id: str) -> WorkflowRunRecord:
        run = get_workflow_run_record_unscoped(run_id)
        if not run:
            raise KeyError(f"Unknown workflow run_id: {run_id}")
        if run.status not in {"running", "cancelling"}:
            return run

        await self._publish(
            run.app_id,
            "workflow.run.started",
            {"run_id": run.run_id, "workflow_id": run.workflow_id, "attempt": run.attempt},
        )
        try:
            if run.cancel_requested_at is not None or run.status == "cancelling":
                raise RunCancelled("Run cancellation requested")
            workflow = get_workflow_record(run.workflow_id, run.app_id)
            if not workflow:
                raise RuntimeError(f"Workflow no longer exists: {run.workflow_id}")

            previous_output: dict[str, Any] = {}
            for index, definition in enumerate(workflow.steps):
                arguments = {
                    **run.input,
                    **definition.arguments,
                    "workflow_context": run.context,
                    "previous_output": previous_output,
                }
                step_type = {
                    "tool": "workflow_tool",
                    "agent": "workflow_agent",
                    "external_job": "workflow_external_job",
                }[definition.type]
                step = get_or_create_run_step(
                    RunStepRecord(
                        run_id=run.run_id,
                        run_type="workflow",
                        app_id=run.app_id,
                        step_index=index,
                        name=definition.name,
                        type=step_type,
                        target=definition.target,
                        max_attempts=definition.max_attempts,
                        timeout_seconds=definition.timeout_seconds,
                        input=arguments,
                    )
                )

                if definition.type == "tool":
                    tool = registry.tools.get(definition.target)
                    if not tool:
                        raise RuntimeError(f"Workflow references an unavailable tool: {definition.target}")

                    async def operation(fn=tool, args: dict[str, Any] = arguments) -> dict:
                        return await fn(args)

                elif definition.type == "agent":
                    nested_run: list[str | None] = [step.nested_run_id]

                    async def operation(
                        agent_id: str = definition.target,
                        args: dict[str, Any] = arguments,
                        max_attempts: int = definition.max_attempts,
                    ) -> dict:
                        agent_run = await agent_service.run_agent(
                            agent_id,
                            args,
                            run.app_id,
                            {**run.context, "workflow_run_id": run.run_id},
                            idempotency_key=(
                                f"workflow:{run.run_id}:step:{index}:attempt:{step.attempt}"
                            ),
                            max_attempts=max_attempts,
                        )
                        nested_run[0] = agent_run.run_id
                        if agent_run.status != "completed":
                            raise RuntimeError(agent_run.error.get("message", "Agent step failed"))
                        return agent_run.output

                else:
                    async def operation(
                        current_step: RunStepRecord = step,
                        static_arguments: dict[str, Any] = definition.arguments,
                        run_input: dict[str, Any] = run.input,
                    ) -> dict:
                        specification = self._external_specification(static_arguments, run_input)
                        return await external_execution_service.execute(
                            current_step,
                            specification,
                            definition.data_lineage,
                            lambda changed: self._publish_external_execution(run, changed),
                        )

                step = await execute_step(
                    step,
                    operation,
                    worker_id,
                    lambda changed: self._publish_step(run, changed),
                )
                if definition.type == "agent" and nested_run[0] and not step.nested_run_id:
                    step.nested_run_id = nested_run[0]
                    step.updated_at = _now_utc()
                    save_run_step(step)
                previous_output = step.output

            run.status = "completed"
            run.output = {
                "last_output": previous_output,
                "step_count": len(workflow.steps),
            }
            run.error = {}
            run.completed_at = _now_utc()
        except RunCancelled as exc:
            run.status = "cancelled"
            run.error = {"code": "run_cancelled", "message": str(exc)}
            run.output = {"error": str(exc)}
            run.completed_at = _now_utc()
        except StepExecutionFailed as exc:
            run.status = "failed"
            run.error = exc.step.error
            run.output = {"error": str(exc)}
            run.completed_at = _now_utc()
        except WorkerLeaseLost:
            raise
        except asyncio.CancelledError:
            run.status = "cancelled"
            run.error = {"code": "execution_cancelled", "message": "Execution task was cancelled"}
            run.output = {"error": run.error["message"]}
            run.completed_at = _now_utc()
            run.updated_at = _now_utc()
            run.steps = self._compatibility_steps(run.run_id)
            save_workflow_run_record(run)
            await self._publish(run.app_id, "workflow.run.cancelled", {"run_id": run.run_id})
            raise
        except Exception as exc:
            run.status = "failed"
            run.error = {
                "code": "run_execution_failed",
                "message": str(exc),
                "exception_type": type(exc).__name__,
            }
            run.output = {"error": str(exc)}
            run.completed_at = _now_utc()

        run.updated_at = _now_utc()
        run.heartbeat_at = _now_utc()
        run.steps = self._compatibility_steps(run.run_id)
        save_workflow_run_record(run)
        await self._publish(
            run.app_id,
            f"workflow.run.{run.status}",
            {"run_id": run.run_id, "output": run.output, "error": run.error},
        )
        return run

    async def _wait_for_terminal(self, run_id: str, app_id: str) -> WorkflowRunRecord:
        deadline = monotonic() + settings.synchronous_wait_timeout_seconds
        while monotonic() < deadline:
            run = get_workflow_run_record(run_id, app_id)
            if not run:
                raise KeyError(f"Unknown workflow run_id: {run_id}")
            if run.status in {"cancelled", "completed", "failed"}:
                return run
            await asyncio.sleep(0.05)
        raise TimeoutError(f"Workflow run {run_id} did not finish within the synchronous wait limit")

    async def cancel_run(self, run_id: str, app_id: str) -> WorkflowRunRecord:
        run = request_workflow_run_cancel(run_id, app_id)
        if not run:
            raise KeyError(f"Unknown workflow run_id: {run_id}")
        if run.status in {"completed", "failed"}:
            return run
        await self._publish(
            app_id,
            "workflow.run.cancellation.requested",
            {"run_id": run_id, "status": run.status},
        )
        if run.status == "cancelled":
            await self._publish(app_id, "workflow.run.cancelled", {"run_id": run_id})
        return run

    async def _publish_step(self, run: WorkflowRunRecord, step: RunStepRecord) -> None:
        await self._publish(
            run.app_id,
            f"workflow.step.{step.status}",
            {
                "run_id": run.run_id,
                "step_id": step.step_id,
                "step_index": step.step_index,
                "name": step.name,
                "type": step.type,
                "target": step.target,
                "attempt": step.attempt,
                "output": step.output,
                "error": step.error,
            },
        )

    async def _publish_external_execution(
        self,
        run: WorkflowRunRecord,
        execution: ExternalExecutionRecord,
    ) -> None:
        await self._publish(
            run.app_id,
            f"workflow.external.{execution.status}",
            {
                "run_id": run.run_id,
                "step_id": execution.step_id,
                "execution_id": execution.execution_id,
                "provider": execution.provider,
                "attempt": execution.attempt,
                "external_run_id": execution.external_run_id,
                "external_state": execution.external_state,
                "external_url": execution.external_url,
                "artifacts": [
                    artifact.model_dump(mode="json", by_alias=True)
                    for artifact in execution.artifacts
                ],
                "error": execution.error,
            },
        )

    def _compatibility_steps(self, run_id: str) -> list[dict[str, Any]]:
        return [
            {
                "index": step.step_index,
                "name": step.name,
                "type": {
                    "workflow_agent": "agent",
                    "workflow_tool": "tool",
                    "workflow_external_job": "external_job",
                }[step.type],
                "target": step.target,
                "status": step.status,
                "attempt": step.attempt,
                "output": step.output,
                "error": step.error,
                **({"agent_run_id": step.nested_run_id} if step.nested_run_id else {}),
            }
            for step in list_run_step_records("workflow", run_id)
        ]

    @staticmethod
    def _external_specification(
        static_arguments: dict[str, Any],
        run_input: dict[str, Any],
    ) -> dict[str, Any]:
        specification = deepcopy(static_arguments)
        runtime_parameters = run_input.get("job_parameters")
        if runtime_parameters is not None:
            if not isinstance(runtime_parameters, dict):
                raise ExternalBackendError(
                    "input.job_parameters must be an object",
                    code="invalid_external_job_parameters",
                    retryable=False,
                )
            static_parameters = specification.get("job_parameters", {})
            if not isinstance(static_parameters, dict):
                raise ExternalBackendError(
                    "external job_parameters must be an object",
                    code="invalid_external_job_parameters",
                    retryable=False,
                )
            specification["job_parameters"] = {
                **runtime_parameters,
                **static_parameters,
            }
        return specification

    async def _publish(self, app_id: str, topic: str, payload: dict[str, Any]) -> None:
        event = PlatformEvent(app_id=app_id, topic=topic, payload=payload, source="workflow-service")
        save_event_record(event)
        await event_bus.publish(event)


workflow_service = WorkflowService()
