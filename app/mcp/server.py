from __future__ import annotations

from fastapi import APIRouter

from app.agents.service import agent_service
from app.core.registry import registry
from app.events.bus import event_bus
from app.models.schemas import MCPRequest, MCPResponse, PlatformEvent

router = APIRouter(tags=["mcp"])


@router.post("/mcp", response_model=MCPResponse)
async def handle_mcp(request: MCPRequest) -> MCPResponse:
    try:
        if request.method == "initialize":
            return MCPResponse(
                id=request.id,
                result={
                    "serverInfo": {"name": "developer-platform-mcp", "version": "0.1.0"},
                    "capabilities": {"tools": {}, "logging": {}},
                },
            )

        if request.method == "tools/list":
            return MCPResponse(
                id=request.id,
                result={
                    "tools": [
                        {
                            "name": "list_agents",
                            "description": "List registered AI agents",
                            "inputSchema": {"type": "object", "properties": {}},
                        },
                        {
                            "name": "run_agent",
                            "description": "Run an agent with structured payload",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "agent_id": {"type": "string"},
                                    "payload": {"type": "object"},
                                },
                                "required": ["agent_id", "payload"],
                            },
                        },
                        {
                            "name": "publish_event",
                            "description": "Publish a platform event",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "topic": {"type": "string"},
                                    "payload": {"type": "object"},
                                },
                                "required": ["topic", "payload"],
                            },
                        },
                    ]
                },
            )

        if request.method == "tools/call":
            name = request.params.get("name")
            arguments = request.params.get("arguments", {})

            if name == "list_agents":
                return MCPResponse(
                    id=request.id,
                    result={
                        "content": [
                            {
                                "type": "text",
                                "text": str([agent.model_dump() for agent in registry.agents.values()]),
                            }
                        ]
                    },
                )
            if name == "run_agent":
                run = await agent_service.run_agent(arguments["agent_id"], arguments.get("payload", {}), {})
                return MCPResponse(
                    id=request.id,
                    result={"content": [{"type": "text", "text": run.model_dump_json()}]},
                )
            if name == "publish_event":
                event = PlatformEvent(topic=arguments["topic"], payload=arguments.get("payload", {}), source="mcp")
                registry.events.append(event)
                await event_bus.publish(event)
                return MCPResponse(
                    id=request.id,
                    result={"content": [{"type": "text", "text": event.model_dump_json()}]},
                )
            return MCPResponse(id=request.id, error={"code": -32601, "message": f"Unknown tool: {name}"})

        return MCPResponse(id=request.id, error={"code": -32601, "message": "Method not found"})
    except Exception as exc:
        return MCPResponse(id=request.id, error={"code": -32000, "message": str(exc)})