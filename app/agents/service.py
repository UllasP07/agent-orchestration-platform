from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from app.core.registry import registry
from app.db.repository import save_event_record, save_run_record
from app.events.bus import event_bus
from app.models.schemas import AgentDefinition, AgentRunRecord, PlatformEvent

ToolFn = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


class AgentService:
    async def run_agent(
        self,
        agent_id: str,
        payload: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> AgentRunRecord:
        if agent_id not in registry.agents:
            raise KeyError(f"Unknown agent_id: {agent_id}")

        agent = registry.agents[agent_id]
        run = AgentRunRecord(agent_id=agent_id, status="running", input=payload, context=context or {})
        save_run_record(run)
        await self._publish("run.started", {"run_id": run.run_id, "agent_id": agent_id})

        try:
            steps = []
            tool_outputs: dict[str, Any] = {}
            for tool_name in agent.tools:
                tool_fn: ToolFn | None = registry.tools.get(tool_name)
                if not tool_fn:
                    continue
                result = await tool_fn({**payload, **(context or {})})
                steps.append({"type": "tool_call", "tool_name": tool_name, "result": result})
                tool_outputs[tool_name] = result
                await self._publish(
                    "run.step.completed",
                    {"run_id": run.run_id, "tool_name": tool_name, "result": result},
                )

            answer = self._compose_answer(agent, payload, tool_outputs)
            run.status = "completed"
            run.output = {
                "summary": answer,
                "tool_outputs": tool_outputs,
                "agent": agent.name,
            }
            run.steps = steps
            run.updated_at = datetime.now(timezone.utc)
            save_run_record(run)
            await self._publish("run.completed", {"run_id": run.run_id, "output": run.output})
            return run
        except Exception as exc:  # pragma: no cover
            run.status = "failed"
            run.output = {"error": str(exc)}
            run.updated_at = datetime.now(timezone.utc)
            save_run_record(run)
            await self._publish("run.failed", {"run_id": run.run_id, "error": str(exc)})
            return run

    async def _publish(self, topic: str, payload: dict[str, Any]) -> None:
        event = PlatformEvent(topic=topic, payload=payload, source="agent-service")
        save_event_record(event)
        await event_bus.publish(event)

    def _compose_answer(self, agent: AgentDefinition, payload: dict[str, Any], tool_outputs: dict[str, Any]) -> str:
        question = payload.get("question", "No question provided.")
        if not tool_outputs:
            return f"{agent.name} processed the request: {question}"
        lines = [f"{agent.name} processed: {question}"]
        for name, result in tool_outputs.items():
            lines.append(f"- {name}: {result}")
        return "\n".join(lines)


agent_service = AgentService()