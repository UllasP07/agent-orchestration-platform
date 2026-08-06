from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.registry import registry
from app.db.models import AgentRunModel
from app.db.repository import (
    claim_agent_run,
    get_or_create_run_step,
    get_run_record,
    list_run_step_records,
    recover_stale_runs,
    save_run_step,
)
from app.db.session import SessionLocal
from app.execution.worker import RunWorker
from app.models.schemas import RunStepRecord
from app.agents.service import agent_service
from app.main import app


def register_app(client: TestClient) -> tuple[str, str]:
    body = client.post(
        "/v1/apps",
        json={"name": "Execution Tests", "owner": "worker@example.com"},
    ).json()
    return body["app"]["id"], body["api_key"]


def auth(api_key: str) -> dict[str, str]:
    return {"x-api-key": api_key}


def test_fastapi_lifespan_worker_processes_queued_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "worker_enabled", True)
    monkeypatch.setattr(settings, "worker_poll_interval_seconds", 0.01)
    with TestClient(app) as automatic_client:
        _, api_key = register_app(automatic_client)
        submitted = automatic_client.post(
            "/v1/agents/run",
            headers=auth(api_key),
            json={"agent_id": "agent-ops", "input": {}},
        ).json()
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            run = automatic_client.get(
                f"/v1/runs/{submitted['run_id']}",
                headers=auth(api_key),
            ).json()
            if run["status"] == "completed":
                break
            time.sleep(0.01)
        assert run["status"] == "completed"


@pytest.mark.asyncio
async def test_submission_is_queued_then_worker_persists_steps(client: TestClient) -> None:
    _, api_key = register_app(client)
    submitted = client.post(
        "/v1/agents/run",
        headers=auth(api_key),
        json={"agent_id": "agent-ops", "input": {"question": "Check health"}},
    )
    assert submitted.status_code == 202
    run = submitted.json()
    assert run["status"] == "queued"
    assert run["attempt"] == 0
    assert submitted.headers["location"] == f"/v1/runs/{run['run_id']}"

    assert await RunWorker("worker-test").run_once() is True
    completed = client.get(f"/v1/runs/{run['run_id']}", headers=auth(api_key)).json()
    assert completed["status"] == "completed"
    assert completed["attempt"] == 1
    assert completed["worker_id"] == "worker-test"

    steps = client.get(f"/v1/runs/{run['run_id']}/steps", headers=auth(api_key)).json()
    assert len(steps) == 1
    assert steps[0]["status"] == "completed"
    assert steps[0]["target"] == "query_usage_metrics"


@pytest.mark.asyncio
async def test_concurrent_workers_only_claim_a_run_once(client: TestClient) -> None:
    _, api_key = register_app(client)
    client.post(
        "/v1/agents/run",
        headers=auth(api_key),
        json={"agent_id": "agent-ops", "input": {}},
    )

    results = await asyncio.gather(
        RunWorker("worker-a").run_once(),
        RunWorker("worker-b").run_once(),
    )
    assert sorted(results) == [False, True]
    runs = client.get("/v1/runs", headers=auth(api_key)).json()
    assert len(runs) == 1
    assert runs[0]["attempt"] == 1


@pytest.mark.asyncio
async def test_idempotency_key_returns_original_run(client: TestClient) -> None:
    _, api_key = register_app(client)
    payload = {
        "agent_id": "agent-ops",
        "input": {"question": "Only once"},
        "idempotency_key": "request-123",
    }
    first = client.post("/v1/agents/run", headers=auth(api_key), json=payload).json()
    second = client.post("/v1/agents/run", headers=auth(api_key), json=payload).json()

    assert second["run_id"] == first["run_id"]
    assert len(client.get("/v1/runs", headers=auth(api_key)).json()) == 1
    assert await RunWorker("idempotency-worker").run_once() is True
    assert await RunWorker("idle-worker").run_once() is False


@pytest.mark.asyncio
async def test_wait_path_follows_a_run_claimed_by_another_worker(client: TestClient) -> None:
    app_id, _ = register_app(client)
    queued = await agent_service.enqueue_agent(
        "agent-ops",
        {"question": "Follow the owner"},
        app_id,
        idempotency_key="shared-wait",
    )
    claimed = claim_agent_run(queued.run_id, "other-worker")
    assert claimed is not None

    executing = asyncio.create_task(agent_service.execute_claimed_run(queued.run_id, "other-worker"))
    waiting = asyncio.create_task(
        agent_service.run_agent(
            "agent-ops",
            {"question": "Follow the owner"},
            app_id,
            idempotency_key="shared-wait",
        )
    )
    executed, observed = await asyncio.gather(executing, waiting)
    assert executed.status == "completed"
    assert observed.run_id == executed.run_id
    assert observed.status == "completed"


@pytest.mark.asyncio
async def test_step_retries_are_durable(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _, api_key = register_app(client)
    attempts = 0

    async def flaky_tool(_: dict) -> dict:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise RuntimeError("temporary dependency failure")
        return {"recovered": True}

    registry.tools["flaky_tool"] = flaky_tool
    monkeypatch.setattr(settings, "retry_base_delay_seconds", 0.0)
    agent = client.post(
        "/v1/agents",
        headers=auth(api_key),
        json={"name": "Retry Agent", "system_prompt": "Retry safely.", "tools": ["flaky_tool"]},
    ).json()
    run = client.post(
        "/v1/agents/run",
        headers=auth(api_key),
        json={"agent_id": agent["id"], "input": {}},
    ).json()

    await RunWorker("retry-worker").run_once()
    completed = client.get(f"/v1/runs/{run['run_id']}", headers=auth(api_key)).json()
    step = client.get(f"/v1/runs/{run['run_id']}/steps", headers=auth(api_key)).json()[0]
    assert completed["status"] == "completed"
    assert step["attempt"] == 3
    assert step["output"] == {"recovered": True}


@pytest.mark.asyncio
async def test_step_timeout_exhausts_attempts(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _, api_key = register_app(client)

    async def slow_tool(_: dict) -> dict:
        await asyncio.sleep(1)
        return {"too_late": True}

    registry.tools["slow_tool"] = slow_tool
    monkeypatch.setattr(settings, "default_step_timeout_seconds", 0.01)
    monkeypatch.setattr(settings, "default_step_max_attempts", 2)
    monkeypatch.setattr(settings, "retry_base_delay_seconds", 0.0)
    agent = client.post(
        "/v1/agents",
        headers=auth(api_key),
        json={"name": "Timeout Agent", "system_prompt": "Stay bounded.", "tools": ["slow_tool"]},
    ).json()
    run = client.post(
        "/v1/agents/run",
        headers=auth(api_key),
        json={"agent_id": agent["id"], "input": {}},
    ).json()

    await RunWorker("timeout-worker").run_once()
    failed = client.get(f"/v1/runs/{run['run_id']}", headers=auth(api_key)).json()
    step = client.get(f"/v1/runs/{run['run_id']}/steps", headers=auth(api_key)).json()[0]
    assert failed["status"] == "failed"
    assert step["status"] == "failed"
    assert step["attempt"] == 2
    assert step["error"]["code"] == "step_timeout"


@pytest.mark.asyncio
async def test_worker_requeues_unexpected_run_failure_until_budget_is_exhausted(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_id, api_key = register_app(client)
    submitted = client.post(
        "/v1/agents/run",
        headers=auth(api_key),
        json={"agent_id": "agent-ops", "input": {}, "max_attempts": 2},
    ).json()

    async def crash(_: str, __: str):
        raise RuntimeError("worker process dependency failed")

    monkeypatch.setattr(agent_service, "execute_claimed_run", crash)
    monkeypatch.setattr(settings, "retry_base_delay_seconds", 0.0)
    worker = RunWorker("crashing-worker")
    assert await worker.run_once() is True
    retrying = get_run_record(submitted["run_id"], app_id)
    assert retrying and retrying.status == "retrying"
    assert retrying.attempt == 1

    assert await worker.run_once() is True
    failed = get_run_record(submitted["run_id"], app_id)
    assert failed and failed.status == "failed"
    assert failed.attempt == 2
    assert failed.error["code"] == "worker_execution_failed"


@pytest.mark.asyncio
async def test_queued_and_running_runs_can_be_cancelled(client: TestClient) -> None:
    _, api_key = register_app(client)
    queued = client.post(
        "/v1/agents/run",
        headers=auth(api_key),
        json={"agent_id": "agent-ops", "input": {}},
    ).json()
    cancelled = client.post(f"/v1/runs/{queued['run_id']}/cancel", headers=auth(api_key)).json()
    assert cancelled["status"] == "cancelled"
    assert await RunWorker("no-work").run_once() is False

    started = asyncio.Event()

    async def blocking_tool(_: dict) -> dict:
        started.set()
        await asyncio.sleep(10)
        return {"finished": True}

    registry.tools["blocking_tool"] = blocking_tool
    agent = client.post(
        "/v1/agents",
        headers=auth(api_key),
        json={"name": "Blocking Agent", "system_prompt": "Wait.", "tools": ["blocking_tool"]},
    ).json()
    running = client.post(
        "/v1/agents/run",
        headers=auth(api_key),
        json={"agent_id": agent["id"], "input": {}},
    ).json()
    worker_task = asyncio.create_task(RunWorker("cancel-worker").run_once())
    await asyncio.wait_for(started.wait(), timeout=0.5)
    requested = client.post(f"/v1/runs/{running['run_id']}/cancel", headers=auth(api_key)).json()
    assert requested["status"] == "cancelling"
    await asyncio.wait_for(worker_task, timeout=0.5)
    final = client.get(f"/v1/runs/{running['run_id']}", headers=auth(api_key)).json()
    assert final["status"] == "cancelled"


@pytest.mark.asyncio
async def test_worker_executes_workflow_and_nested_agent_durably(client: TestClient) -> None:
    _, api_key = register_app(client)
    workflow = client.post(
        "/v1/workflows",
        headers=auth(api_key),
        json={
            "name": "Durable investigation",
            "steps": [
                {
                    "name": "metrics",
                    "type": "tool",
                    "target": "query_usage_metrics",
                    "max_attempts": 2,
                    "timeout_seconds": 1,
                },
                {
                    "name": "analysis",
                    "type": "agent",
                    "target": "agent-ops",
                    "arguments": {"question": "Analyze metrics"},
                },
            ],
        },
    ).json()
    submitted = client.post(
        f"/v1/workflows/{workflow['id']}/runs",
        headers=auth(api_key),
        json={"input": {"service": "api"}},
    )
    assert submitted.status_code == 202

    assert await RunWorker("workflow-worker").run_once() is True
    run = client.get(
        f"/v1/workflow-runs/{submitted.json()['run_id']}",
        headers=auth(api_key),
    ).json()
    steps = client.get(
        f"/v1/workflow-runs/{run['run_id']}/steps",
        headers=auth(api_key),
    ).json()
    assert run["status"] == "completed"
    assert [step["status"] for step in steps] == ["completed", "completed"]
    assert steps[1]["nested_run_id"].startswith("run-")


def test_expired_lease_requeues_run_and_preserves_completed_steps(client: TestClient) -> None:
    app_id, api_key = register_app(client)
    submitted = client.post(
        "/v1/agents/run",
        headers=auth(api_key),
        json={"agent_id": "agent-ops", "input": {}},
    ).json()
    claimed = claim_agent_run(submitted["run_id"], "dead-worker")
    assert claimed is not None

    completed = get_or_create_run_step(
        RunStepRecord(
            run_id=claimed.run_id,
            run_type="agent",
            app_id=app_id,
            step_index=0,
            name="already-done",
            type="agent_tool",
            target="query_usage_metrics",
            status="completed",
            attempt=1,
            output={"saved": True},
        )
    )
    running = get_or_create_run_step(
        RunStepRecord(
            run_id=claimed.run_id,
            run_type="agent",
            app_id=app_id,
            step_index=1,
            name="interrupted",
            type="agent_tool",
            target="query_usage_metrics",
            status="running",
            attempt=1,
        )
    )
    save_run_step(completed)
    save_run_step(running)
    with SessionLocal() as session:
        row = session.get(AgentRunModel, claimed.run_id)
        row.heartbeat_at = datetime.now(timezone.utc) - timedelta(minutes=5)
        session.commit()

    assert recover_stale_runs(lease_timeout_seconds=1) == 1
    recovered = get_run_record(claimed.run_id, app_id)
    steps = list_run_step_records("agent", claimed.run_id, app_id)
    assert recovered and recovered.status == "queued"
    assert [step.status for step in steps] == ["completed", "pending"]
