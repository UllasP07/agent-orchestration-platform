from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sse_starlette.sse import EventSourceResponse

from app.agents.service import agent_service
from app.api.deps import require_api_key
from app.core.registry import registry
from app.db.repository import (
    create_api_key,
    create_app_record,
    create_workflow_record,
    delete_agent_record,
    delete_workflow_record,
    get_agent_record,
    get_app_record,
    get_external_execution_record,
    get_run_record,
    get_workflow_record,
    get_workflow_run_record,
    list_agent_records,
    list_api_key_records,
    list_event_records,
    list_external_execution_records,
    list_run_records,
    list_run_step_records,
    list_workflow_records,
    list_workflow_run_records,
    revoke_api_key,
    save_event_record,
    upsert_agent_record,
    update_workflow_record,
    workflow_references_agent,
)
from app.events.bus import event_bus
from app.external.registry import external_backends
from app.models.schemas import (
    APIKeyCreate,
    APIKeyRecord,
    APIKeyWithSecret,
    AgentCreate,
    AgentDefinition,
    AgentPublic,
    AgentRunRecord,
    AgentRunRequest,
    AppCreate,
    AppRecord,
    AppWithAPIKey,
    EventPublishRequest,
    ExternalBackendPublic,
    ExternalExecutionRecord,
    PlatformEvent,
    RunStepRecord,
    ToolCallRequest,
    ToolResult,
    WorkflowCreate,
    WorkflowDefinition,
    WorkflowRunRecord,
    WorkflowRunRequest,
)
from app.workflows.service import workflow_service

router = APIRouter(prefix="/v1", tags=["platform"])


def _public_agent(agent: AgentDefinition) -> AgentPublic:
    return AgentPublic(
        id=agent.id,
        name=agent.name,
        description=agent.description,
        capabilities=agent.capabilities,
        tools=agent.tools,
        built_in=agent.app_id is None,
        created_at=agent.created_at,
        updated_at=agent.updated_at,
    )


def _validate_workflow_steps(payload: WorkflowCreate, app_id: str) -> None:
    for step in payload.steps:
        if step.type == "tool" and step.target not in registry.tools:
            raise HTTPException(status_code=422, detail=f"Unknown tool: {step.target}")
        if step.type == "agent" and not get_agent_record(step.target, app_id):
            raise HTTPException(status_code=422, detail=f"Unknown agent: {step.target}")
        if step.type == "external_job":
            backend = external_backends.get(step.target)
            if not backend:
                raise HTTPException(status_code=422, detail=f"Unknown external backend: {step.target}")
            try:
                backend.validate_spec(step.arguments)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/external-backends", response_model=list[ExternalBackendPublic])
async def list_external_backends(
    _: APIKeyRecord = Depends(require_api_key),
) -> list[ExternalBackendPublic]:
    return [
        ExternalBackendPublic(
            name=name,
            configured=bool(getattr(external_backends.get(name), "configured", True)),
        )
        for name in external_backends.names()
    ]


@router.post("/apps", response_model=AppWithAPIKey, status_code=status.HTTP_201_CREATED)
async def create_app(payload: AppCreate) -> AppWithAPIKey:
    app = create_app_record(AppRecord(**payload.model_dump()))
    issued = create_api_key(app.id)
    return AppWithAPIKey(app=app, api_key=issued.api_key)


@router.get("/apps", response_model=list[AppRecord])
async def list_apps(api_key: APIKeyRecord = Depends(require_api_key)) -> list[AppRecord]:
    app = get_app_record(api_key.app_id)
    return [app] if app else []


@router.post("/api-keys", response_model=APIKeyWithSecret, status_code=status.HTTP_201_CREATED)
async def issue_api_key(
    payload: APIKeyCreate,
    api_key: APIKeyRecord = Depends(require_api_key),
) -> APIKeyWithSecret:
    return create_api_key(api_key.app_id, payload.name)


@router.get("/api-keys", response_model=list[APIKeyRecord])
async def list_api_keys(api_key: APIKeyRecord = Depends(require_api_key)) -> list[APIKeyRecord]:
    return list_api_key_records(api_key.app_id)


@router.delete("/api-keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_app_api_key(
    key_id: str,
    api_key: APIKeyRecord = Depends(require_api_key),
) -> Response:
    if not revoke_api_key(key_id, api_key.app_id):
        raise HTTPException(status_code=404, detail="API key not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/agents", response_model=list[AgentPublic])
async def list_agents(api_key: APIKeyRecord = Depends(require_api_key)) -> list[AgentPublic]:
    return [_public_agent(agent) for agent in list_agent_records(api_key.app_id)]


@router.post("/agents", response_model=AgentPublic, status_code=status.HTTP_201_CREATED)
async def create_agent(
    payload: AgentCreate,
    api_key: APIKeyRecord = Depends(require_api_key),
) -> AgentPublic:
    unknown_tools = sorted(set(payload.tools) - set(registry.tools))
    if unknown_tools:
        raise HTTPException(status_code=422, detail={"unknown_tools": unknown_tools})
    agent = AgentDefinition(app_id=api_key.app_id, **payload.model_dump())
    upsert_agent_record(agent)
    registry.agents[agent.id] = agent
    return _public_agent(agent)


@router.put("/agents/{agent_id}", response_model=AgentPublic)
async def update_agent(
    agent_id: str,
    payload: AgentCreate,
    api_key: APIKeyRecord = Depends(require_api_key),
) -> AgentPublic:
    existing = get_agent_record(agent_id, api_key.app_id)
    if not existing or existing.app_id != api_key.app_id:
        raise HTTPException(status_code=404, detail="Agent not found or is built in")
    unknown_tools = sorted(set(payload.tools) - set(registry.tools))
    if unknown_tools:
        raise HTTPException(status_code=422, detail={"unknown_tools": unknown_tools})
    agent = AgentDefinition(
        id=existing.id,
        app_id=existing.app_id,
        created_at=existing.created_at,
        updated_at=datetime.now(timezone.utc),
        **payload.model_dump(),
    )
    upsert_agent_record(agent)
    registry.agents[agent.id] = agent
    return _public_agent(agent)


@router.get("/agents/{agent_id}", response_model=AgentPublic)
async def get_agent(
    agent_id: str,
    api_key: APIKeyRecord = Depends(require_api_key),
) -> AgentPublic:
    agent = get_agent_record(agent_id, api_key.app_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return _public_agent(agent)


@router.delete("/agents/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent(
    agent_id: str,
    api_key: APIKeyRecord = Depends(require_api_key),
) -> Response:
    if workflow_references_agent(agent_id, api_key.app_id):
        raise HTTPException(status_code=409, detail="Agent is referenced by a workflow")
    if not delete_agent_record(agent_id, api_key.app_id):
        raise HTTPException(status_code=404, detail="Agent not found or is built in")
    registry.agents.pop(agent_id, None)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/agents/run", response_model=AgentRunRecord, status_code=status.HTTP_202_ACCEPTED)
async def run_agent(
    request: AgentRunRequest,
    response: Response,
    wait: bool = False,
    api_key: APIKeyRecord = Depends(require_api_key),
) -> AgentRunRecord:
    try:
        if wait:
            run = await agent_service.run_agent(
                request.agent_id,
                request.input,
                api_key.app_id,
                request.context,
                request.idempotency_key,
                request.max_attempts,
            )
            response.status_code = status.HTTP_200_OK
        else:
            run = await agent_service.enqueue_agent(
                request.agent_id,
                request.input,
                api_key.app_id,
                request.context,
                request.idempotency_key,
                request.max_attempts,
            )
            if run.status in {"completed", "failed", "cancelled"}:
                response.status_code = status.HTTP_200_OK
        response.headers["Location"] = f"/v1/runs/{run.run_id}"
        return run
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except TimeoutError as exc:
        raise HTTPException(status_code=504, detail=str(exc)) from exc


@router.get("/runs", response_model=list[AgentRunRecord])
async def list_runs(
    limit: int = Query(default=100, ge=1, le=500),
    api_key: APIKeyRecord = Depends(require_api_key),
) -> list[AgentRunRecord]:
    return list_run_records(api_key.app_id, limit)


@router.get("/runs/{run_id}", response_model=AgentRunRecord)
async def get_run(
    run_id: str,
    api_key: APIKeyRecord = Depends(require_api_key),
) -> AgentRunRecord:
    run = get_run_record(run_id, api_key.app_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


@router.get("/runs/{run_id}/steps", response_model=list[RunStepRecord])
async def list_run_steps(
    run_id: str,
    api_key: APIKeyRecord = Depends(require_api_key),
) -> list[RunStepRecord]:
    if not get_run_record(run_id, api_key.app_id):
        raise HTTPException(status_code=404, detail="Run not found")
    return list_run_step_records("agent", run_id, api_key.app_id)


@router.post("/runs/{run_id}/cancel", response_model=AgentRunRecord)
async def cancel_run(
    run_id: str,
    api_key: APIKeyRecord = Depends(require_api_key),
) -> AgentRunRecord:
    try:
        return await agent_service.cancel_run(run_id, api_key.app_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Run not found") from exc


@router.post("/workflows", response_model=WorkflowDefinition, status_code=status.HTTP_201_CREATED)
async def create_workflow(
    payload: WorkflowCreate,
    api_key: APIKeyRecord = Depends(require_api_key),
) -> WorkflowDefinition:
    _validate_workflow_steps(payload, api_key.app_id)
    workflow = WorkflowDefinition(app_id=api_key.app_id, **payload.model_dump())
    return create_workflow_record(workflow)


@router.put("/workflows/{workflow_id}", response_model=WorkflowDefinition)
async def update_workflow(
    workflow_id: str,
    payload: WorkflowCreate,
    api_key: APIKeyRecord = Depends(require_api_key),
) -> WorkflowDefinition:
    existing = get_workflow_record(workflow_id, api_key.app_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Workflow not found")
    _validate_workflow_steps(payload, api_key.app_id)
    workflow = WorkflowDefinition(
        id=existing.id,
        app_id=existing.app_id,
        created_at=existing.created_at,
        updated_at=datetime.now(timezone.utc),
        **payload.model_dump(),
    )
    return update_workflow_record(workflow)


@router.get("/workflows", response_model=list[WorkflowDefinition])
async def list_workflows(api_key: APIKeyRecord = Depends(require_api_key)) -> list[WorkflowDefinition]:
    return list_workflow_records(api_key.app_id)


@router.get("/workflows/{workflow_id}", response_model=WorkflowDefinition)
async def get_workflow(
    workflow_id: str,
    api_key: APIKeyRecord = Depends(require_api_key),
) -> WorkflowDefinition:
    workflow = get_workflow_record(workflow_id, api_key.app_id)
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return workflow


@router.delete("/workflows/{workflow_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_workflow(
    workflow_id: str,
    api_key: APIKeyRecord = Depends(require_api_key),
) -> Response:
    if not delete_workflow_record(workflow_id, api_key.app_id):
        raise HTTPException(status_code=404, detail="Workflow not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/workflows/{workflow_id}/runs",
    response_model=WorkflowRunRecord,
    status_code=status.HTTP_202_ACCEPTED,
)
async def run_workflow(
    workflow_id: str,
    request: WorkflowRunRequest,
    response: Response,
    wait: bool = False,
    api_key: APIKeyRecord = Depends(require_api_key),
) -> WorkflowRunRecord:
    try:
        if wait:
            run = await workflow_service.run_workflow(
                workflow_id,
                request.input,
                api_key.app_id,
                request.context,
                request.idempotency_key,
                request.max_attempts,
            )
            response.status_code = status.HTTP_200_OK
        else:
            run = await workflow_service.enqueue_workflow(
                workflow_id,
                request.input,
                api_key.app_id,
                request.context,
                request.idempotency_key,
                request.max_attempts,
            )
            if run.status in {"completed", "failed", "cancelled"}:
                response.status_code = status.HTTP_200_OK
        response.headers["Location"] = f"/v1/workflow-runs/{run.run_id}"
        return run
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except TimeoutError as exc:
        raise HTTPException(status_code=504, detail=str(exc)) from exc


@router.get("/workflow-runs/{run_id}", response_model=WorkflowRunRecord)
async def get_workflow_run(
    run_id: str,
    api_key: APIKeyRecord = Depends(require_api_key),
) -> WorkflowRunRecord:
    run = get_workflow_run_record(run_id, api_key.app_id)
    if not run:
        raise HTTPException(status_code=404, detail="Workflow run not found")
    return run


@router.get("/workflow-runs", response_model=list[WorkflowRunRecord])
async def list_workflow_runs(
    limit: int = Query(default=100, ge=1, le=500),
    api_key: APIKeyRecord = Depends(require_api_key),
) -> list[WorkflowRunRecord]:
    return list_workflow_run_records(api_key.app_id, limit)


@router.get("/workflow-runs/{run_id}/steps", response_model=list[RunStepRecord])
async def list_workflow_run_steps(
    run_id: str,
    api_key: APIKeyRecord = Depends(require_api_key),
) -> list[RunStepRecord]:
    if not get_workflow_run_record(run_id, api_key.app_id):
        raise HTTPException(status_code=404, detail="Workflow run not found")
    return list_run_step_records("workflow", run_id, api_key.app_id)


@router.get(
    "/workflow-runs/{run_id}/external-executions",
    response_model=list[ExternalExecutionRecord],
)
async def list_workflow_external_executions(
    run_id: str,
    api_key: APIKeyRecord = Depends(require_api_key),
) -> list[ExternalExecutionRecord]:
    if not get_workflow_run_record(run_id, api_key.app_id):
        raise HTTPException(status_code=404, detail="Workflow run not found")
    return list_external_execution_records(run_id, api_key.app_id)


@router.get(
    "/external-executions/{execution_id}",
    response_model=ExternalExecutionRecord,
)
async def get_external_execution(
    execution_id: str,
    api_key: APIKeyRecord = Depends(require_api_key),
) -> ExternalExecutionRecord:
    execution = get_external_execution_record(execution_id, api_key.app_id)
    if not execution:
        raise HTTPException(status_code=404, detail="External execution not found")
    return execution


@router.post("/workflow-runs/{run_id}/cancel", response_model=WorkflowRunRecord)
async def cancel_workflow_run(
    run_id: str,
    api_key: APIKeyRecord = Depends(require_api_key),
) -> WorkflowRunRecord:
    try:
        return await workflow_service.cancel_run(run_id, api_key.app_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Workflow run not found") from exc


@router.post("/events/publish", response_model=PlatformEvent)
async def publish_event(
    request: EventPublishRequest,
    api_key: APIKeyRecord = Depends(require_api_key),
) -> PlatformEvent:
    event = PlatformEvent(
        app_id=api_key.app_id,
        topic=request.topic,
        payload=request.payload,
        source=request.source,
    )
    save_event_record(event)
    await event_bus.publish(event)
    return event


@router.get("/events", response_model=list[PlatformEvent])
async def list_events(
    limit: int = Query(default=100, ge=1, le=500),
    topic: str | None = None,
    api_key: APIKeyRecord = Depends(require_api_key),
) -> list[PlatformEvent]:
    return list_event_records(api_key.app_id, limit, topic)


@router.get("/events/stream")
async def stream_events(api_key: APIKeyRecord = Depends(require_api_key)) -> EventSourceResponse:
    async def event_generator():
        async for event in event_bus.subscribe(api_key.app_id):
            yield {
                "event": event.topic,
                "id": event.id,
                "data": event.model_dump_json(),
            }

    return EventSourceResponse(event_generator())


@router.post("/tools/call", response_model=ToolResult)
async def call_tool(
    request: ToolCallRequest,
    api_key: APIKeyRecord = Depends(require_api_key),
) -> ToolResult:
    tool = registry.tools.get(request.tool_name)
    if not tool:
        raise HTTPException(status_code=404, detail="Tool not found")
    data = await tool({**request.arguments, "app_id": api_key.app_id})
    return ToolResult(tool_name=request.tool_name, success=True, data=data)
