from __future__ import annotations

from fastapi import APIRouter, HTTPException
from sse_starlette.sse import EventSourceResponse

from app.agents.service import agent_service
from app.core.registry import registry
from app.db.repository import (
    create_app_record,
    get_run_record,
    list_app_records,
    list_event_records,
    save_event_record,
)
from app.events.bus import event_bus
from app.models.schemas import (
    AgentRunRequest,
    AppCreate,
    AppRecord,
    EventPublishRequest,
    PlatformEvent,
    ToolCallRequest,
    ToolResult,
)

router = APIRouter(prefix="/v1", tags=["platform"])


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/apps", response_model=list[AppRecord])
async def list_apps() -> list[AppRecord]:
    return list_app_records()


@router.post("/apps", response_model=AppRecord)
async def create_app(payload: AppCreate) -> AppRecord:
    app = AppRecord(**payload.model_dump())
    return create_app_record(app)


@router.get("/agents")
async def list_agents() -> list[dict]:
    return [agent.model_dump() for agent in registry.agents.values()]


@router.get("/agents/{agent_id}")
async def get_agent(agent_id: str) -> dict:
    agent = registry.agents.get(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent.model_dump()


@router.post("/agents/run")
async def run_agent(request: AgentRunRequest) -> dict:
    try:
        run = await agent_service.run_agent(request.agent_id, request.input, request.context)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return run.model_dump(mode="json")


@router.get("/runs/{run_id}")
async def get_run(run_id: str) -> dict:
    run = get_run_record(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return run.model_dump(mode="json")


@router.post("/events/publish")
async def publish_event(request: EventPublishRequest) -> dict:
    event = PlatformEvent(topic=request.topic, payload=request.payload, source=request.source)
    save_event_record(event)
    await event_bus.publish(event)
    return event.model_dump(mode="json")


@router.get("/events")
async def list_events() -> list[dict]:
    return [event.model_dump(mode="json") for event in list_event_records(100)]


@router.get("/events/stream")
async def stream_events() -> EventSourceResponse:
    async def event_generator():
        async for event in event_bus.subscribe():
            yield {
                "event": event.topic,
                "id": event.id,
                "data": event.model_dump_json(),
            }

    return EventSourceResponse(event_generator())


@router.post("/tools/call", response_model=ToolResult)
async def call_tool(request: ToolCallRequest) -> ToolResult:
    tool = registry.tools.get(request.tool_name)
    if not tool:
        raise HTTPException(status_code=404, detail="Tool not found")
    data = await tool(request.arguments)
    return ToolResult(tool_name=request.tool_name, success=True, data=data)