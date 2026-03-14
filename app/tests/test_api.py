from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health() -> None:
    response = client.get("/v1/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_create_and_list_app() -> None:
    create = client.post("/v1/apps", json={"name": "Demo", "owner": "owner@example.com", "metadata": {}})
    assert create.status_code == 200
    data = create.json()
    assert data["name"] == "Demo"

    listed = client.get("/v1/apps")
    assert listed.status_code == 200
    assert any(app["id"] == data["id"] for app in listed.json())


def test_run_agent_and_fetch_run() -> None:
    response = client.post(
        "/v1/agents/run",
        json={"agent_id": "agent-support", "input": {"question": "Find anomalies", "customer_id": "cust-42"}},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert "tool_outputs" in body["output"]

    fetched = client.get(f"/v1/runs/{body['run_id']}")
    assert fetched.status_code == 200
    assert fetched.json()["run_id"] == body["run_id"]


def test_graphql_agents_query() -> None:
    response = client.post("/graphql", json={"query": "query { agents { id name } }"})
    assert response.status_code == 200
    assert len(response.json()["data"]["agents"]) >= 1


def test_mcp_tools_list() -> None:
    response = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
    )
    assert response.status_code == 200
    assert len(response.json()["result"]["tools"]) >= 1