from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.models import APIKeyModel
from app.db.session import SessionLocal
from app.events.bus import event_bus
from app.models.schemas import PlatformEvent


def create_test_app_and_key(
    client: TestClient,
    name: str = "Demo",
    owner: str = "owner@example.com",
) -> tuple[str, str]:
    response = client.post("/v1/apps", json={"name": name, "owner": owner, "metadata": {}})
    assert response.status_code == 201
    body = response.json()
    return body["app"]["id"], body["api_key"]


def auth(api_key: str) -> dict[str, str]:
    return {"x-api-key": api_key}


def test_health_and_app_registration_hashes_api_key(client: TestClient) -> None:
    assert client.get("/v1/health").json() == {"status": "ok"}
    app_id, api_key = create_test_app_and_key(client)

    response = client.get("/v1/apps", headers=auth(api_key))
    assert response.status_code == 200
    assert [app["id"] for app in response.json()] == [app_id]

    with SessionLocal() as session:
        stored = session.execute(select(APIKeyModel.api_key)).scalar_one()
    assert api_key not in stored
    assert stored.startswith("sha256:")


def test_protected_routes_require_api_key(client: TestClient) -> None:
    assert client.get("/v1/apps").status_code == 401
    assert client.get("/v1/agents").status_code == 401
    assert client.get("/v1/runs").status_code == 401
    assert client.get("/v1/events").status_code == 401
    assert client.post("/graphql", json={"query": "query { agents { id } }"}).status_code == 401
    assert client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
    ).status_code == 401


def test_agent_catalog_hides_prompts_and_custom_agents_are_tenant_scoped(client: TestClient) -> None:
    _, key_a = create_test_app_and_key(client, "App A", "a@example.com")
    _, key_b = create_test_app_and_key(client, "App B", "b@example.com")

    built_ins = client.get("/v1/agents", headers=auth(key_a)).json()
    assert {agent["id"] for agent in built_ins} == {"agent-support", "agent-ops"}
    assert all("system_prompt" not in agent for agent in built_ins)

    created = client.post(
        "/v1/agents",
        headers=auth(key_a),
        json={
            "name": "Metrics Reader",
            "description": "Reads usage",
            "system_prompt": "Summarize metrics.",
            "capabilities": ["metrics"],
            "tools": ["query_usage_metrics"],
        },
    )
    assert created.status_code == 201
    agent_id = created.json()["id"]

    assert client.get(f"/v1/agents/{agent_id}", headers=auth(key_a)).status_code == 200
    assert client.get(f"/v1/agents/{agent_id}", headers=auth(key_b)).status_code == 404
    assert agent_id not in {agent["id"] for agent in client.get("/v1/agents", headers=auth(key_b)).json()}


def test_agent_runs_and_events_are_tenant_scoped(client: TestClient) -> None:
    app_a, key_a = create_test_app_and_key(client, "App A", "a@example.com")
    _, key_b = create_test_app_and_key(client, "App B", "b@example.com")

    response = client.post(
        "/v1/agents/run",
        headers=auth(key_a),
        json={"agent_id": "agent-ops", "input": {"question": "Check health"}},
    )
    assert response.status_code == 200
    run = response.json()
    assert run["status"] == "completed"
    assert run["app_id"] == app_a

    assert client.get(f"/v1/runs/{run['run_id']}", headers=auth(key_a)).status_code == 200
    assert client.get(f"/v1/runs/{run['run_id']}", headers=auth(key_b)).status_code == 404
    assert client.get("/v1/runs", headers=auth(key_b)).json() == []
    assert client.get("/v1/events", headers=auth(key_b)).json() == []
    topics = {event["topic"] for event in client.get("/v1/events", headers=auth(key_a)).json()}
    assert {"run.started", "run.step.completed", "run.completed"}.issubset(topics)


def test_api_keys_can_be_issued_and_revoked(client: TestClient) -> None:
    _, primary_key = create_test_app_and_key(client)
    issued = client.post(
        "/v1/api-keys",
        headers=auth(primary_key),
        json={"name": "automation"},
    )
    assert issued.status_code == 201
    secondary_key = issued.json()["api_key"]

    keys = client.get("/v1/api-keys", headers=auth(secondary_key)).json()
    primary_id = next(key["key_id"] for key in keys if key["key_prefix"] == primary_key[:10])
    assert all("api_key" not in key for key in keys)

    assert client.delete(f"/v1/api-keys/{primary_id}", headers=auth(primary_key)).status_code == 204
    assert client.get("/v1/apps", headers=auth(primary_key)).status_code == 401
    assert client.get("/v1/apps", headers=auth(secondary_key)).status_code == 200


def test_workflow_executes_tool_and_agent_steps(client: TestClient) -> None:
    _, api_key = create_test_app_and_key(client)
    created = client.post(
        "/v1/workflows",
        headers=auth(api_key),
        json={
            "name": "Health investigation",
            "description": "Collect metrics and have ops summarize them",
            "steps": [
                {"name": "metrics", "type": "tool", "target": "query_usage_metrics", "arguments": {"window": "1h"}},
                {"name": "analysis", "type": "agent", "target": "agent-ops", "arguments": {"question": "Summarize health"}},
            ],
        },
    )
    assert created.status_code == 201
    workflow_id = created.json()["id"]

    response = client.post(
        f"/v1/workflows/{workflow_id}/runs",
        headers=auth(api_key),
        json={"input": {"service": "api"}, "context": {"trace_id": "trace-1"}},
    )
    assert response.status_code == 200
    run = response.json()
    assert run["status"] == "completed"
    assert run["output"]["step_count"] == 2
    assert run["steps"][1]["agent_run_id"].startswith("run-")
    assert client.get(f"/v1/workflow-runs/{run['run_id']}", headers=auth(api_key)).json()["status"] == "completed"


def test_agent_referenced_by_workflow_cannot_be_deleted(client: TestClient) -> None:
    _, api_key = create_test_app_and_key(client)
    agent = client.post(
        "/v1/agents",
        headers=auth(api_key),
        json={
            "name": "Workflow Agent",
            "system_prompt": "Process the request.",
            "tools": [],
        },
    ).json()
    workflow = client.post(
        "/v1/workflows",
        headers=auth(api_key),
        json={
            "name": "Uses custom agent",
            "steps": [{"name": "process", "type": "agent", "target": agent["id"]}],
        },
    ).json()

    blocked = client.delete(f"/v1/agents/{agent['id']}", headers=auth(api_key))
    assert blocked.status_code == 409
    assert client.delete(f"/v1/workflows/{workflow['id']}", headers=auth(api_key)).status_code == 204
    assert client.delete(f"/v1/agents/{agent['id']}", headers=auth(api_key)).status_code == 204


def test_workflow_rejects_unknown_targets(client: TestClient) -> None:
    _, api_key = create_test_app_and_key(client)
    response = client.post(
        "/v1/workflows",
        headers=auth(api_key),
        json={
            "name": "Broken",
            "steps": [{"name": "missing", "type": "tool", "target": "does_not_exist"}],
        },
    )
    assert response.status_code == 422


def test_event_filter_and_mcp_persistence(client: TestClient) -> None:
    _, api_key = create_test_app_and_key(client)
    response = client.post(
        "/mcp",
        headers=auth(api_key),
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "publish_event", "arguments": {"topic": "deploy.ready", "payload": {"sha": "abc"}}},
        },
    )
    assert response.status_code == 200
    assert response.json()["result"]["structuredContent"]["topic"] == "deploy.ready"
    assert len(client.get("/v1/events", headers=auth(api_key), params={"topic": "deploy.ready"}).json()) == 1
    assert client.get("/v1/events", headers=auth(api_key), params={"topic": "other"}).json() == []


def test_graphql_and_current_mcp_discovery(client: TestClient) -> None:
    _, api_key = create_test_app_and_key(client)
    graphql = client.post(
        "/graphql",
        headers=auth(api_key),
        json={"query": "query { agents { id name builtIn } workflows { id } }"},
    )
    assert graphql.status_code == 200
    assert len(graphql.json()["data"]["agents"]) == 2

    discovery = client.post(
        "/mcp",
        headers={**auth(api_key), "MCP-Protocol-Version": "2026-07-28", "Mcp-Method": "server/discover"},
        json={"jsonrpc": "2.0", "id": 2, "method": "server/discover", "params": {}},
    ).json()
    assert "tools" in discovery["result"]["capabilities"]

    tools = client.post(
        "/mcp",
        headers=auth(api_key),
        json={"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {}},
    ).json()["result"]
    assert [tool["name"] for tool in tools["tools"]] == sorted(tool["name"] for tool in tools["tools"])
    assert tools["cacheScope"] == "private"


@pytest.mark.asyncio
async def test_event_bus_only_delivers_to_matching_app() -> None:
    subscriber_a = event_bus.subscribe("app-a")
    subscriber_b = event_bus.subscribe("app-b")
    pending_a = asyncio.create_task(anext(subscriber_a))
    pending_b = asyncio.create_task(anext(subscriber_b))
    await asyncio.sleep(0)

    await event_bus.publish(PlatformEvent(app_id="app-a", topic="private.event"))
    delivered = await asyncio.wait_for(pending_a, timeout=0.1)
    assert delivered.app_id == "app-a"
    assert not pending_b.done()

    pending_b.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending_b
    await subscriber_a.aclose()
