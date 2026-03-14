from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


class AppCreate(BaseModel):
    name: str
    owner: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class AppRecord(AppCreate):
    id: str = Field(default_factory=lambda: f"app-{uuid4().hex[:12]}")
    created_at: datetime = Field(default_factory=now_utc)


class AppWithAPIKey(BaseModel):
    app: AppRecord
    api_key: str


class APIKeyRecord(BaseModel):
    key_id: str = Field(default_factory=lambda: f"key-{uuid4().hex[:12]}")
    app_id: str
    api_key: str
    name: str = "default"
    created_at: datetime = Field(default_factory=now_utc)


class AgentDefinition(BaseModel):
    id: str
    name: str
    description: str
    system_prompt: str
    capabilities: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)


class AgentRunRequest(BaseModel):
    agent_id: str
    input: dict[str, Any] = Field(default_factory=dict)
    context: dict[str, Any] = Field(default_factory=dict)


class AgentRunRecord(BaseModel):
    run_id: str = Field(default_factory=lambda: f"run-{uuid4().hex[:12]}")
    agent_id: str
    status: Literal["queued", "running", "completed", "failed"] = "queued"
    input: dict[str, Any] = Field(default_factory=dict)
    context: dict[str, Any] = Field(default_factory=dict)
    output: dict[str, Any] = Field(default_factory=dict)
    steps: list[dict[str, Any]] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=now_utc)
    updated_at: datetime = Field(default_factory=now_utc)


class EventPublishRequest(BaseModel):
    topic: str
    payload: dict[str, Any] = Field(default_factory=dict)
    source: str = "api"


class PlatformEvent(BaseModel):
    id: str = Field(default_factory=lambda: f"evt-{uuid4().hex[:12]}")
    topic: str
    payload: dict[str, Any] = Field(default_factory=dict)
    source: str = "system"
    created_at: datetime = Field(default_factory=now_utc)


class ToolCallRequest(BaseModel):
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolResult(BaseModel):
    tool_name: str
    success: bool
    data: dict[str, Any] = Field(default_factory=dict)


class MCPRequest(BaseModel):
    jsonrpc: str = "2.0"
    id: int | str | None = None
    method: str
    params: dict[str, Any] = Field(default_factory=dict)


class MCPResponse(BaseModel):
    jsonrpc: str = "2.0"
    id: int | str | None = None
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None