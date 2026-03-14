from __future__ import annotations

from typing import Any

import httpx


class DevPlatformClient:
    def __init__(self, base_url: str, timeout: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def list_apps(self) -> list[dict[str, Any]]:
        return self._request("GET", "/v1/apps")

    def create_app(self, name: str, owner: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._request(
            "POST",
            "/v1/apps",
            json={"name": name, "owner": owner, "metadata": metadata or {}},
        )

    def list_agents(self) -> list[dict[str, Any]]:
        return self._request("GET", "/v1/agents")

    def run_agent(self, agent_id: str, payload: dict[str, Any], context: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._request(
            "POST",
            "/v1/agents/run",
            json={"agent_id": agent_id, "input": payload, "context": context or {}},
        )

    def publish_event(self, topic: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/v1/events/publish", json={"topic": topic, "payload": payload})

    def graphql(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._request("POST", "/graphql", json={"query": query, "variables": variables or {}})

    def mcp(self, method: str, params: dict[str, Any] | None = None, request_id: int = 1) -> dict[str, Any]:
        return self._request(
            "POST",
            "/mcp",
            json={"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}},
        )

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        with httpx.Client(base_url=self.base_url, timeout=self.timeout) as client:
            response = client.request(method, path, **kwargs)
            response.raise_for_status()
            return response.json()