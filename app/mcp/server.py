from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends

from app.agents.service import agent_service
from app.api.deps import require_api_key
from app.db.repository import (
    list_agent_records,
    list_workflow_records,
    save_event_record,
)
from app.events.bus import event_bus
from app.models.schemas import APIKeyRecord, MCPRequest, MCPResponse, PlatformEvent
from app.workflows.service import workflow_service

router = APIRouter(tags=["mcp"])

CURRENT_PROTOCOL_VERSION = "2026-07-28"
LEGACY_PROTOCOL_VERSION = "2025-11-25"
SERVER_INFO = {"name": "developer-platform-mcp", "version": "0.3.0"}


def _tool_catalog() -> list[dict[str, Any]]:
    tools = [
        {
            "name": "list_agents",
            "title": "List Agents",
            "description": "List built-in and app-owned AI agents",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
            "annotations": {"readOnlyHint": True},
        },
        {
            "name": "list_workflows",
            "title": "List Workflows",
            "description": "List workflows owned by the authenticated app",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
            "annotations": {"readOnlyHint": True},
        },
        {
            "name": "publish_event",
            "title": "Publish Event",
            "description": "Publish a persisted app-scoped platform event",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string", "minLength": 1},
                    "payload": {"type": "object"},
                },
                "required": ["topic"],
                "additionalProperties": False,
            },
        },
        {
            "name": "run_agent",
            "title": "Run Agent",
            "description": "Run an accessible agent with a structured payload",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "agent_id": {"type": "string"},
                    "payload": {"type": "object"},
                    "context": {"type": "object"},
                },
                "required": ["agent_id", "payload"],
                "additionalProperties": False,
            },
        },
        {
            "name": "run_workflow",
            "title": "Run Workflow",
            "description": "Run an app-owned workflow with a structured payload",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "workflow_id": {"type": "string"},
                    "payload": {"type": "object"},
                    "context": {"type": "object"},
                },
                "required": ["workflow_id", "payload"],
                "additionalProperties": False,
            },
        },
    ]
    return sorted(tools, key=lambda tool: tool["name"])


def _result(request_id: int | str | None, result: dict[str, Any]) -> MCPResponse:
    result.setdefault("_meta", {})["io.modelcontextprotocol/serverInfo"] = SERVER_INFO
    result["_meta"]["io.modelcontextprotocol/protocolVersion"] = CURRENT_PROTOCOL_VERSION
    return MCPResponse(id=request_id, result=result)


def _tool_result(request_id: int | str | None, value: dict[str, Any], is_error: bool = False) -> MCPResponse:
    return _result(
        request_id,
        {
            "content": [{"type": "text", "text": json.dumps(value, default=str)}],
            "structuredContent": value,
            "isError": is_error,
        },
    )


async def process_mcp(request: MCPRequest, api_key: APIKeyRecord) -> MCPResponse:
    try:
        if request.method == "server/discover":
            return _result(
                request.id,
                {
                    "capabilities": {"tools": {"listChanged": False}},
                    "ttlMs": 60_000,
                    "cacheScope": "private",
                },
            )

        # Kept for clients using the still-supported 2025-11-25 handshake.
        if request.method == "initialize":
            requested_version = request.params.get("protocolVersion")
            protocol_version = requested_version if requested_version == LEGACY_PROTOCOL_VERSION else LEGACY_PROTOCOL_VERSION
            return _result(
                request.id,
                {
                    "protocolVersion": protocol_version,
                    "serverInfo": SERVER_INFO,
                    "capabilities": {"tools": {"listChanged": False}},
                },
            )

        if request.method in {"notifications/initialized", "ping"}:
            return _result(request.id, {})

        if request.method == "tools/list":
            return _result(
                request.id,
                {
                    "tools": _tool_catalog(),
                    "ttlMs": 60_000,
                    "cacheScope": "private",
                },
            )

        if request.method == "tools/call":
            name = request.params.get("name")
            arguments = request.params.get("arguments", {})
            if not isinstance(arguments, dict):
                return MCPResponse(id=request.id, error={"code": -32602, "message": "arguments must be an object"})

            if name == "list_agents":
                agents = [
                    {
                        "id": agent.id,
                        "name": agent.name,
                        "description": agent.description,
                        "capabilities": agent.capabilities,
                        "tools": agent.tools,
                        "built_in": agent.app_id is None,
                    }
                    for agent in list_agent_records(api_key.app_id)
                ]
                return _tool_result(request.id, {"agents": agents})

            if name == "list_workflows":
                workflows = [
                    workflow.model_dump(mode="json")
                    for workflow in list_workflow_records(api_key.app_id)
                ]
                return _tool_result(request.id, {"workflows": workflows})

            if name == "run_agent":
                if "agent_id" not in arguments:
                    return MCPResponse(id=request.id, error={"code": -32602, "message": "agent_id is required"})
                run = await agent_service.run_agent(
                    arguments["agent_id"],
                    arguments.get("payload", {}),
                    api_key.app_id,
                    arguments.get("context", {}),
                )
                return _tool_result(
                    request.id,
                    run.model_dump(mode="json"),
                    is_error=run.status == "failed",
                )

            if name == "run_workflow":
                if "workflow_id" not in arguments:
                    return MCPResponse(id=request.id, error={"code": -32602, "message": "workflow_id is required"})
                run = await workflow_service.run_workflow(
                    arguments["workflow_id"],
                    arguments.get("payload", {}),
                    api_key.app_id,
                    arguments.get("context", {}),
                )
                return _tool_result(
                    request.id,
                    run.model_dump(mode="json"),
                    is_error=run.status == "failed",
                )

            if name == "publish_event":
                topic = arguments.get("topic")
                if not topic:
                    return MCPResponse(id=request.id, error={"code": -32602, "message": "topic is required"})
                event = PlatformEvent(
                    app_id=api_key.app_id,
                    topic=topic,
                    payload=arguments.get("payload", {}),
                    source="mcp",
                )
                save_event_record(event)
                await event_bus.publish(event)
                return _tool_result(request.id, event.model_dump(mode="json"))

            return MCPResponse(id=request.id, error={"code": -32601, "message": f"Unknown tool: {name}"})

        return MCPResponse(id=request.id, error={"code": -32601, "message": "Method not found"})
    except KeyError as exc:
        return MCPResponse(id=request.id, error={"code": -32602, "message": str(exc)})
    except Exception as exc:  # pragma: no cover - last-resort protocol boundary
        return MCPResponse(id=request.id, error={"code": -32603, "message": str(exc)})


@router.post("/mcp", response_model=MCPResponse)
async def handle_mcp(
    request: MCPRequest,
    api_key: APIKeyRecord = Depends(require_api_key),
) -> MCPResponse:
    return await process_mcp(request, api_key)
