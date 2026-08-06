from __future__ import annotations

import time
from typing import Any

import httpx


class DevPlatformClient:
    """Synchronous client for the Developer Platform HTTP API."""

    def __init__(self, base_url: str, api_key: str | None = None, timeout: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    def create_app(self, name: str, owner: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._request(
            "POST",
            "/v1/apps",
            json={"name": name, "owner": owner, "metadata": metadata or {}},
        )

    def list_apps(self, api_key: str | None = None) -> list[dict[str, Any]]:
        return self._request("GET", "/v1/apps", headers=self._auth_headers(api_key))

    def issue_api_key(self, name: str, api_key: str | None = None) -> dict[str, Any]:
        return self._request(
            "POST",
            "/v1/api-keys",
            json={"name": name},
            headers=self._auth_headers(api_key),
        )

    def list_api_keys(self, api_key: str | None = None) -> list[dict[str, Any]]:
        return self._request("GET", "/v1/api-keys", headers=self._auth_headers(api_key))

    def revoke_api_key(self, key_id: str, api_key: str | None = None) -> None:
        self._request("DELETE", f"/v1/api-keys/{key_id}", headers=self._auth_headers(api_key))

    def list_agents(self, api_key: str | None = None) -> list[dict[str, Any]]:
        return self._request("GET", "/v1/agents", headers=self._auth_headers(api_key))

    def get_agent(self, agent_id: str, api_key: str | None = None) -> dict[str, Any]:
        return self._request("GET", f"/v1/agents/{agent_id}", headers=self._auth_headers(api_key))

    def create_agent(
        self,
        name: str,
        system_prompt: str,
        *,
        description: str = "",
        capabilities: list[str] | None = None,
        tools: list[str] | None = None,
        api_key: str | None = None,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/v1/agents",
            json={
                "name": name,
                "description": description,
                "system_prompt": system_prompt,
                "capabilities": capabilities or [],
                "tools": tools or [],
            },
            headers=self._auth_headers(api_key),
        )

    def delete_agent(self, agent_id: str, api_key: str | None = None) -> None:
        self._request("DELETE", f"/v1/agents/{agent_id}", headers=self._auth_headers(api_key))

    def update_agent(
        self,
        agent_id: str,
        name: str,
        system_prompt: str,
        *,
        description: str = "",
        capabilities: list[str] | None = None,
        tools: list[str] | None = None,
        api_key: str | None = None,
    ) -> dict[str, Any]:
        return self._request(
            "PUT",
            f"/v1/agents/{agent_id}",
            json={
                "name": name,
                "description": description,
                "system_prompt": system_prompt,
                "capabilities": capabilities or [],
                "tools": tools or [],
            },
            headers=self._auth_headers(api_key),
        )

    def run_agent(
        self,
        agent_id: str,
        payload: dict[str, Any],
        context: dict[str, Any] | None = None,
        api_key: str | None = None,
        *,
        wait: bool = False,
        idempotency_key: str | None = None,
        max_attempts: int = 3,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/v1/agents/run",
            params={"wait": str(wait).lower()},
            json={
                "agent_id": agent_id,
                "input": payload,
                "context": context or {},
                "idempotency_key": idempotency_key,
                "max_attempts": max_attempts,
            },
            headers=self._auth_headers(api_key),
        )

    def list_runs(self, limit: int = 100, api_key: str | None = None) -> list[dict[str, Any]]:
        return self._request(
            "GET",
            "/v1/runs",
            params={"limit": limit},
            headers=self._auth_headers(api_key),
        )

    def get_run(self, run_id: str, api_key: str | None = None) -> dict[str, Any]:
        return self._request("GET", f"/v1/runs/{run_id}", headers=self._auth_headers(api_key))

    def get_run_steps(self, run_id: str, api_key: str | None = None) -> list[dict[str, Any]]:
        return self._request("GET", f"/v1/runs/{run_id}/steps", headers=self._auth_headers(api_key))

    def cancel_run(self, run_id: str, api_key: str | None = None) -> dict[str, Any]:
        return self._request("POST", f"/v1/runs/{run_id}/cancel", headers=self._auth_headers(api_key))

    def wait_for_run(
        self,
        run_id: str,
        *,
        timeout: float = 30.0,
        poll_interval: float = 0.25,
        api_key: str | None = None,
    ) -> dict[str, Any]:
        return self._wait_for_terminal(
            lambda: self.get_run(run_id, api_key),
            timeout,
            poll_interval,
        )

    def create_workflow(
        self,
        name: str,
        steps: list[dict[str, Any]],
        description: str = "",
        api_key: str | None = None,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/v1/workflows",
            json={"name": name, "description": description, "steps": steps},
            headers=self._auth_headers(api_key),
        )

    def list_workflows(self, api_key: str | None = None) -> list[dict[str, Any]]:
        return self._request("GET", "/v1/workflows", headers=self._auth_headers(api_key))

    def get_workflow(self, workflow_id: str, api_key: str | None = None) -> dict[str, Any]:
        return self._request("GET", f"/v1/workflows/{workflow_id}", headers=self._auth_headers(api_key))

    def delete_workflow(self, workflow_id: str, api_key: str | None = None) -> None:
        self._request("DELETE", f"/v1/workflows/{workflow_id}", headers=self._auth_headers(api_key))

    def update_workflow(
        self,
        workflow_id: str,
        name: str,
        steps: list[dict[str, Any]],
        description: str = "",
        api_key: str | None = None,
    ) -> dict[str, Any]:
        return self._request(
            "PUT",
            f"/v1/workflows/{workflow_id}",
            json={"name": name, "description": description, "steps": steps},
            headers=self._auth_headers(api_key),
        )

    def run_workflow(
        self,
        workflow_id: str,
        payload: dict[str, Any],
        context: dict[str, Any] | None = None,
        api_key: str | None = None,
        *,
        wait: bool = False,
        idempotency_key: str | None = None,
        max_attempts: int = 3,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/v1/workflows/{workflow_id}/runs",
            params={"wait": str(wait).lower()},
            json={
                "input": payload,
                "context": context or {},
                "idempotency_key": idempotency_key,
                "max_attempts": max_attempts,
            },
            headers=self._auth_headers(api_key),
        )

    def get_workflow_run(self, run_id: str, api_key: str | None = None) -> dict[str, Any]:
        return self._request("GET", f"/v1/workflow-runs/{run_id}", headers=self._auth_headers(api_key))

    def get_workflow_run_steps(self, run_id: str, api_key: str | None = None) -> list[dict[str, Any]]:
        return self._request(
            "GET",
            f"/v1/workflow-runs/{run_id}/steps",
            headers=self._auth_headers(api_key),
        )

    def cancel_workflow_run(self, run_id: str, api_key: str | None = None) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/v1/workflow-runs/{run_id}/cancel",
            headers=self._auth_headers(api_key),
        )

    def wait_for_workflow_run(
        self,
        run_id: str,
        *,
        timeout: float = 30.0,
        poll_interval: float = 0.25,
        api_key: str | None = None,
    ) -> dict[str, Any]:
        return self._wait_for_terminal(
            lambda: self.get_workflow_run(run_id, api_key),
            timeout,
            poll_interval,
        )

    def list_workflow_runs(self, limit: int = 100, api_key: str | None = None) -> list[dict[str, Any]]:
        return self._request(
            "GET",
            "/v1/workflow-runs",
            params={"limit": limit},
            headers=self._auth_headers(api_key),
        )

    def publish_event(
        self,
        topic: str,
        payload: dict[str, Any],
        api_key: str | None = None,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/v1/events/publish",
            json={"topic": topic, "payload": payload},
            headers=self._auth_headers(api_key),
        )

    def list_events(
        self,
        *,
        limit: int = 100,
        topic: str | None = None,
        api_key: str | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": limit}
        if topic:
            params["topic"] = topic
        return self._request(
            "GET",
            "/v1/events",
            params=params,
            headers=self._auth_headers(api_key),
        )

    def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
        api_key: str | None = None,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/v1/tools/call",
            json={"tool_name": tool_name, "arguments": arguments or {}},
            headers=self._auth_headers(api_key),
        )

    def graphql(
        self,
        query: str,
        variables: dict[str, Any] | None = None,
        api_key: str | None = None,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/graphql",
            json={"query": query, "variables": variables or {}},
            headers=self._auth_headers(api_key),
        )

    def mcp(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        request_id: int = 1,
        api_key: str | None = None,
    ) -> dict[str, Any]:
        headers = self._auth_headers(api_key)
        headers.update({"MCP-Protocol-Version": "2026-07-28", "Mcp-Method": method})
        if method == "tools/call" and params and params.get("name"):
            headers["Mcp-Name"] = str(params["name"])
        return self._request(
            "POST",
            "/mcp",
            json={"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}},
            headers=headers,
        )

    def _auth_headers(self, api_key: str | None = None) -> dict[str, str]:
        active_key = api_key or self.api_key
        return {"x-api-key": active_key} if active_key else {}

    @staticmethod
    def _wait_for_terminal(fetch, timeout: float, poll_interval: float) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while True:
            run = fetch()
            if run["status"] in {"cancelled", "completed", "failed"}:
                return run
            if time.monotonic() >= deadline:
                raise TimeoutError(f"Run did not finish within {timeout:g} seconds")
            time.sleep(poll_interval)

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        with httpx.Client(base_url=self.base_url, timeout=self.timeout) as client:
            response = client.request(method, path, **kwargs)
            response.raise_for_status()
            if response.status_code == 204:
                return None
            return response.json()
