from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def prefixed_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:12]}"


class AppCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    owner: str = Field(min_length=1, max_length=320)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AppRecord(AppCreate):
    id: str = Field(default_factory=lambda: prefixed_id("app"))
    created_at: datetime = Field(default_factory=now_utc)


class AppWithAPIKey(BaseModel):
    app: AppRecord
    api_key: str


class APIKeyCreate(BaseModel):
    name: str = Field(default="default", min_length=1, max_length=120)


class APIKeyRecord(BaseModel):
    key_id: str = Field(default_factory=lambda: prefixed_id("key"))
    app_id: str
    key_prefix: str
    name: str = "default"
    created_at: datetime = Field(default_factory=now_utc)
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None


class APIKeyWithSecret(BaseModel):
    key: APIKeyRecord
    api_key: str


class AgentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    system_prompt: str = Field(min_length=1, max_length=20_000)
    capabilities: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)


class AgentDefinition(AgentCreate):
    id: str = Field(default_factory=lambda: prefixed_id("agent"))
    app_id: str | None = None
    created_at: datetime = Field(default_factory=now_utc)
    updated_at: datetime = Field(default_factory=now_utc)


class AgentPublic(BaseModel):
    id: str
    name: str
    description: str
    capabilities: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    built_in: bool = False
    created_at: datetime
    updated_at: datetime


class AgentRunRequest(BaseModel):
    agent_id: str
    input: dict[str, Any] = Field(default_factory=dict)
    context: dict[str, Any] = Field(default_factory=dict)


class AgentRunRecord(BaseModel):
    run_id: str = Field(default_factory=lambda: prefixed_id("run"))
    app_id: str
    agent_id: str
    status: Literal["queued", "running", "completed", "failed"] = "queued"
    input: dict[str, Any] = Field(default_factory=dict)
    context: dict[str, Any] = Field(default_factory=dict)
    output: dict[str, Any] = Field(default_factory=dict)
    steps: list[dict[str, Any]] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=now_utc)
    updated_at: datetime = Field(default_factory=now_utc)


class WorkflowStep(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    type: Literal["agent", "tool"]
    target: str = Field(min_length=1, max_length=200)
    arguments: dict[str, Any] = Field(default_factory=dict)


class WorkflowCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    steps: list[WorkflowStep] = Field(min_length=1, max_length=50)


class WorkflowDefinition(WorkflowCreate):
    id: str = Field(default_factory=lambda: prefixed_id("workflow"))
    app_id: str
    created_at: datetime = Field(default_factory=now_utc)
    updated_at: datetime = Field(default_factory=now_utc)


class WorkflowRunRequest(BaseModel):
    input: dict[str, Any] = Field(default_factory=dict)
    context: dict[str, Any] = Field(default_factory=dict)


class WorkflowRunRecord(BaseModel):
    run_id: str = Field(default_factory=lambda: prefixed_id("workflow-run"))
    workflow_id: str
    app_id: str
    status: Literal["queued", "running", "completed", "failed"] = "queued"
    input: dict[str, Any] = Field(default_factory=dict)
    context: dict[str, Any] = Field(default_factory=dict)
    output: dict[str, Any] = Field(default_factory=dict)
    steps: list[dict[str, Any]] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=now_utc)
    updated_at: datetime = Field(default_factory=now_utc)


class EventPublishRequest(BaseModel):
    topic: str = Field(min_length=1, max_length=200)
    payload: dict[str, Any] = Field(default_factory=dict)
    source: str = Field(default="api", min_length=1, max_length=120)


class PlatformEvent(BaseModel):
    id: str = Field(default_factory=lambda: prefixed_id("evt"))
    app_id: str
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
    jsonrpc: Literal["2.0"] = "2.0"
    id: int | str | None = None
    method: str
    params: dict[str, Any] = Field(default_factory=dict)


class MCPResponse(BaseModel):
    jsonrpc: Literal["2.0"] = "2.0"
    id: int | str | None = None
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
