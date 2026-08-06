from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.agents.service import agent_service
from app.core.registry import registry
from app.db.repository import (
    get_workflow_record,
    save_event_record,
    save_workflow_run_record,
)
from app.events.bus import event_bus
from app.models.schemas import PlatformEvent, WorkflowRunRecord


class WorkflowService:
    async def run_workflow(
        self,
        workflow_id: str,
        payload: dict[str, Any],
        app_id: str,
        context: dict[str, Any] | None = None,
    ) -> WorkflowRunRecord:
        workflow = get_workflow_record(workflow_id, app_id)
        if not workflow:
            raise KeyError(f"Unknown workflow_id: {workflow_id}")

        run_context = {**(context or {}), "app_id": app_id}
        run = WorkflowRunRecord(
            workflow_id=workflow_id,
            app_id=app_id,
            status="running",
            input=payload,
            context=run_context,
        )
        save_workflow_run_record(run)
        await self._publish(
            app_id,
            "workflow.run.started",
            {"run_id": run.run_id, "workflow_id": workflow_id},
        )

        try:
            completed_steps: list[dict[str, Any]] = []
            previous_output: dict[str, Any] = {}
            for index, step in enumerate(workflow.steps):
                arguments = {
                    **payload,
                    **step.arguments,
                    "workflow_context": run_context,
                    "previous_output": previous_output,
                }
                if step.type == "tool":
                    tool = registry.tools.get(step.target)
                    if not tool:
                        raise RuntimeError(f"Workflow references an unavailable tool: {step.target}")
                    step_output = await tool(arguments)
                    nested_run_id = None
                else:
                    agent_run = await agent_service.run_agent(
                        step.target,
                        arguments,
                        app_id,
                        {**run_context, "workflow_run_id": run.run_id},
                    )
                    if agent_run.status == "failed":
                        raise RuntimeError(agent_run.output.get("error", "Agent step failed"))
                    step_output = agent_run.output
                    nested_run_id = agent_run.run_id

                completed = {
                    "index": index,
                    "name": step.name,
                    "type": step.type,
                    "target": step.target,
                    "output": step_output,
                }
                if nested_run_id:
                    completed["agent_run_id"] = nested_run_id
                completed_steps.append(completed)
                previous_output = step_output
                await self._publish(
                    app_id,
                    "workflow.step.completed",
                    {"run_id": run.run_id, **completed},
                )

            run.status = "completed"
            run.steps = completed_steps
            run.output = {"last_output": previous_output, "step_count": len(completed_steps)}
            run.updated_at = datetime.now(timezone.utc)
            save_workflow_run_record(run)
            await self._publish(
                app_id,
                "workflow.run.completed",
                {"run_id": run.run_id, "workflow_id": workflow_id, "output": run.output},
            )
            return run
        except Exception as exc:
            run.status = "failed"
            run.steps = completed_steps
            run.output = {"error": str(exc)}
            run.updated_at = datetime.now(timezone.utc)
            save_workflow_run_record(run)
            await self._publish(
                app_id,
                "workflow.run.failed",
                {"run_id": run.run_id, "workflow_id": workflow_id, "error": str(exc)},
            )
            return run

    async def _publish(self, app_id: str, topic: str, payload: dict[str, Any]) -> None:
        event = PlatformEvent(app_id=app_id, topic=topic, payload=payload, source="workflow-service")
        save_event_record(event)
        await event_bus.publish(event)


workflow_service = WorkflowService()
