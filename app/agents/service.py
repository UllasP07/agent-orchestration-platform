from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from time import monotonic
from typing import Any, Awaitable, Callable
from uuid import uuid4

from app.core.config import settings
from app.core.registry import registry
from app.db.repository import (
    claim_agent_run,
    enqueue_run_record,
    get_agent_record,
    get_or_create_run_step,
    get_run_record,
    get_run_record_unscoped,
    list_run_step_records,
    request_agent_run_cancel,
    save_event_record,
    save_run_record,
)
from app.events.bus import event_bus
from app.execution.engine import RunCancelled, StepExecutionFailed, WorkerLeaseLost, execute_step
from app.models.schemas import AgentDefinition, AgentRunRecord, PlatformEvent, RunStepRecord

ToolFn = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


class AgentService:
    async def enqueue_agent(
        self,
        agent_id: str,
        payload: dict[str, Any],
        app_id: str,
        context: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
        max_attempts: int = 3,
    ) -> AgentRunRecord:
        if not get_agent_record(agent_id, app_id):
            raise KeyError(f"Unknown agent_id: {agent_id}")

        run = AgentRunRecord(
            app_id=app_id,
            agent_id=agent_id,
            status="queued",
            input=payload,
            context={**(context or {}), "app_id": app_id},
            idempotency_key=idempotency_key,
            max_attempts=max_attempts,
        )
        persisted, created = enqueue_run_record(run)
        if created:
            await self._publish(
                app_id,
                "run.queued",
                {"run_id": persisted.run_id, "agent_id": agent_id},
            )
        return persisted

    async def run_agent(
        self,
        agent_id: str,
        payload: dict[str, Any],
        app_id: str,
        context: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
        max_attempts: int = 3,
    ) -> AgentRunRecord:
        """Compatibility path that queues, claims, and awaits one durable run."""
        run = await self.enqueue_agent(
            agent_id,
            payload,
            app_id,
            context,
            idempotency_key,
            max_attempts,
        )
        if run.status in {"completed", "failed", "cancelled"}:
            return run
        worker_id = f"inline-{uuid4().hex[:12]}"
        claimed = claim_agent_run(run.run_id, worker_id)
        if claimed:
            return await self.execute_claimed_run(claimed.run_id, worker_id)
        return await self._wait_for_terminal(run.run_id, app_id)

    async def execute_claimed_run(self, run_id: str, worker_id: str) -> AgentRunRecord:
        run = get_run_record_unscoped(run_id)
        if not run:
            raise KeyError(f"Unknown run_id: {run_id}")
        if run.status not in {"running", "cancelling"}:
            return run

        await self._publish(
            run.app_id,
            "run.started",
            {"run_id": run.run_id, "agent_id": run.agent_id, "attempt": run.attempt},
        )
        try:
            if run.cancel_requested_at is not None or run.status == "cancelling":
                raise RunCancelled("Run cancellation requested")

            agent = get_agent_record(run.agent_id, run.app_id)
            if not agent:
                raise RuntimeError(f"Agent no longer exists: {run.agent_id}")

            tool_outputs: dict[str, Any] = {}
            for index, tool_name in enumerate(agent.tools):
                tool_fn: ToolFn | None = registry.tools.get(tool_name)
                if not tool_fn:
                    raise RuntimeError(f"Agent references an unavailable tool: {tool_name}")

                arguments = {**run.input, **run.context}
                step = get_or_create_run_step(
                    RunStepRecord(
                        run_id=run.run_id,
                        run_type="agent",
                        app_id=run.app_id,
                        step_index=index,
                        name=tool_name,
                        type="agent_tool",
                        target=tool_name,
                        max_attempts=settings.default_step_max_attempts,
                        timeout_seconds=settings.default_step_timeout_seconds,
                        input=arguments,
                    )
                )

                async def operation(fn: ToolFn = tool_fn, args: dict[str, Any] = arguments) -> dict:
                    return await fn(args)

                step = await execute_step(
                    step,
                    operation,
                    worker_id,
                    lambda changed: self._publish_step(run, changed),
                )
                tool_outputs[tool_name] = step.output

            run.status = "completed"
            run.output = {
                "summary": self._compose_answer(agent, run.input, tool_outputs),
                "tool_outputs": tool_outputs,
                "agent": agent.name,
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
            save_run_record(run)
            await self._publish(run.app_id, "run.cancelled", {"run_id": run.run_id})
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
        save_run_record(run)
        await self._publish(
            run.app_id,
            f"run.{run.status}",
            {"run_id": run.run_id, "output": run.output, "error": run.error},
        )
        return run

    async def _wait_for_terminal(self, run_id: str, app_id: str) -> AgentRunRecord:
        deadline = monotonic() + settings.synchronous_wait_timeout_seconds
        while monotonic() < deadline:
            run = get_run_record(run_id, app_id)
            if not run:
                raise KeyError(f"Unknown run_id: {run_id}")
            if run.status in {"cancelled", "completed", "failed"}:
                return run
            await asyncio.sleep(0.05)
        raise TimeoutError(f"Run {run_id} did not finish within the synchronous wait limit")

    async def cancel_run(self, run_id: str, app_id: str) -> AgentRunRecord:
        run = request_agent_run_cancel(run_id, app_id)
        if not run:
            raise KeyError(f"Unknown run_id: {run_id}")
        if run.status in {"completed", "failed"}:
            return run
        await self._publish(
            app_id,
            "run.cancellation.requested",
            {"run_id": run_id, "status": run.status},
        )
        if run.status == "cancelled":
            await self._publish(app_id, "run.cancelled", {"run_id": run_id})
        return run

    async def _publish_step(self, run: AgentRunRecord, step: RunStepRecord) -> None:
        await self._publish(
            run.app_id,
            f"run.step.{step.status}",
            {
                "run_id": run.run_id,
                "step_id": step.step_id,
                "step_index": step.step_index,
                "tool_name": step.target,
                "attempt": step.attempt,
                "output": step.output,
                "error": step.error,
            },
        )

    def _compatibility_steps(self, run_id: str) -> list[dict[str, Any]]:
        return [
            {
                "type": "tool_call",
                "tool_name": step.target,
                "status": step.status,
                "attempt": step.attempt,
                "result": step.output,
                "error": step.error,
            }
            for step in list_run_step_records("agent", run_id)
        ]

    async def _publish(self, app_id: str, topic: str, payload: dict[str, Any]) -> None:
        event = PlatformEvent(app_id=app_id, topic=topic, payload=payload, source="agent-service")
        save_event_record(event)
        await event_bus.publish(event)

    def _compose_answer(
        self,
        agent: AgentDefinition,
        payload: dict[str, Any],
        tool_outputs: dict[str, Any],
    ) -> str:
        question = payload.get("question", "No question provided.")
        if not tool_outputs:
            return f"{agent.name} processed the request: {question}"
        lines = [f"{agent.name} processed: {question}"]
        for name, result in tool_outputs.items():
            lines.append(f"- {name}: {result}")
        return "\n".join(lines)


agent_service = AgentService()
