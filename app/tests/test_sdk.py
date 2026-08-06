from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from devplatform import DevPlatformClient


def test_sdk_complete_flow(client: TestClient) -> None:
    class BoundClient(DevPlatformClient):
        def _request(self, method: str, path: str, **kwargs: Any) -> Any:
            response = client.request(method, path, **kwargs)
            response.raise_for_status()
            return None if response.status_code == 204 else response.json()

    sdk = BoundClient(base_url="http://testserver")
    created = sdk.create_app("SDK Demo", "sdk@example.com")
    sdk.api_key = created["api_key"]

    assert sdk.list_apps()[0]["name"] == "SDK Demo"
    assert len(sdk.list_agents()) == 2
    assert sdk.call_tool("query_usage_metrics", {"window": "1h"})["success"] is True

    agent = sdk.create_agent(
        "SDK Agent",
        "Summarize the available data.",
        tools=["query_usage_metrics"],
    )
    agent = sdk.update_agent(
        agent["id"],
        "Updated SDK Agent",
        "Summarize the available data concisely.",
        tools=["query_usage_metrics"],
    )
    assert agent["name"] == "Updated SDK Agent"
    run = sdk.run_agent(agent["id"], {"question": "Summarize system health"}, wait=True)
    assert sdk.get_run(run["run_id"])["status"] == "completed"
    assert sdk.list_runs()[0]["run_id"] == run["run_id"]

    workflow = sdk.create_workflow(
        "SDK Workflow",
        [{"name": "metrics", "type": "tool", "target": "query_usage_metrics", "arguments": {}}],
    )
    workflow = sdk.update_workflow(
        workflow["id"],
        "Updated SDK Workflow",
        [{"name": "metrics", "type": "tool", "target": "query_usage_metrics", "arguments": {"window": "6h"}}],
    )
    assert workflow["name"] == "Updated SDK Workflow"
    workflow_run = sdk.run_workflow(workflow["id"], {"window": "6h"}, wait=True)
    assert sdk.get_workflow_run(workflow_run["run_id"])["status"] == "completed"
    assert sdk.list_workflow_runs()[0]["run_id"] == workflow_run["run_id"]

    sdk.publish_event("sdk.completed", {"run_id": run["run_id"]})
    assert sdk.list_events(topic="sdk.completed")[0]["topic"] == "sdk.completed"
    assert sdk.graphql("query { agents { id } }")["data"]["agents"]
    assert sdk.mcp("tools/list")["result"]["tools"]
