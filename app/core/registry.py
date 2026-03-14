from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.models.schemas import AgentDefinition, AgentRunRecord, AppRecord, PlatformEvent


@dataclass
class Registry:
    apps: dict[str, AppRecord] = field(default_factory=dict)
    agents: dict[str, AgentDefinition] = field(default_factory=dict)
    runs: dict[str, AgentRunRecord] = field(default_factory=dict)
    events: list[PlatformEvent] = field(default_factory=list)
    tools: dict[str, Any] = field(default_factory=dict)


registry = Registry()