from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import inspect, text

from app.core.config import settings
from app.db.repository import (
    get_app_record,
    get_or_create_external_execution,
    get_workflow_run_record,
    list_external_execution_records,
)
from app.db.init_db import init_db
from app.db.session import engine
from app.execution.worker import RunWorker
from app.external.backends.base import ExternalBackendError
from app.external.backends.databricks import DatabricksJobsBackend
from app.external.backends.fake import FakeExternalBackend
from app.external.registry import external_backends
from app.external.service import external_execution_service
from app.models.schemas import (
    DeltaTableArtifact,
    ExternalExecutionRecord,
    RunStepRecord,
    UnityCatalogLineage,
)
from app.workflows.service import workflow_service


def _auth(api_key: str) -> dict[str, str]:
    return {"x-api-key": api_key}


def _create_app(client: TestClient, name: str = "Data Platform") -> tuple[str, str]:
    response = client.post(
        "/v1/apps",
        json={"name": name, "owner": f"{name.lower().replace(' ', '-')}@example.com"},
    )
    assert response.status_code == 201
    body = response.json()
    return body["app"]["id"], body["api_key"]


def _external_workflow_payload(
    *,
    outcome: str = "completed",
    polls: int = 1,
    max_attempts: int = 2,
) -> dict[str, Any]:
    return {
        "name": "Lakehouse run analytics",
        "steps": [
            {
                "name": "build governed tables",
                "type": "external_job",
                "target": "fake",
                "arguments": {
                    "polls_before_completion": polls,
                    "outcome": outcome,
                    "output": {"rows_written": 12},
                },
                "data_lineage": {
                    "inputs": [
                        {
                            "catalog": "agent_platform",
                            "schema": "bronze",
                            "table": "run_events",
                        }
                    ],
                    "outputs": [
                        {
                            "catalog": "agent_platform",
                            "schema": "gold",
                            "table": "daily_run_metrics",
                            "version": 7,
                        }
                    ],
                },
                "max_attempts": max_attempts,
                "timeout_seconds": 5,
            }
        ],
    }


def test_unity_catalog_references_use_safe_identifiers() -> None:
    artifact = DeltaTableArtifact(catalog="main", schema="analytics", table="daily_runs")
    assert artifact.full_name == "main.analytics.daily_runs"
    assert artifact.model_dump(by_alias=True)["schema"] == "analytics"

    with pytest.raises(ValidationError):
        DeltaTableArtifact(catalog="main", schema="analytics", table="runs; DROP TABLE users")


def test_v05_table_upgrade_is_additive(client: TestClient) -> None:
    app_id, _ = _create_app(client)
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE external_executions"))
    assert not inspect(engine).has_table("external_executions")

    init_db()

    assert inspect(engine).has_table("external_executions")
    assert get_app_record(app_id) is not None


def test_fake_external_workflow_persists_artifacts_and_is_app_scoped(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "external_job_poll_interval_seconds", 0.001)
    app_id, api_key = _create_app(client)
    _, other_key = _create_app(client, "Other App")

    backends = client.get("/v1/external-backends", headers=_auth(api_key))
    assert backends.status_code == 200
    assert backends.json() == [
        {"name": "databricks", "configured": False},
        {"name": "fake", "configured": True},
    ]

    created = client.post(
        "/v1/workflows",
        headers=_auth(api_key),
        json=_external_workflow_payload(),
    )
    assert created.status_code == 201
    workflow_id = created.json()["id"]

    response = client.post(
        f"/v1/workflows/{workflow_id}/runs",
        headers=_auth(api_key),
        params={"wait": "true"},
        json={"input": {"job_parameters": {"date": "2026-08-07"}}},
    )
    assert response.status_code == 200
    run = response.json()
    assert run["status"] == "completed"
    assert run["steps"][0]["type"] == "external_job"
    assert run["output"]["last_output"]["result"] == {"rows_written": 12}

    executions = client.get(
        f"/v1/workflow-runs/{run['run_id']}/external-executions",
        headers=_auth(api_key),
    )
    assert executions.status_code == 200
    execution = executions.json()[0]
    assert execution["app_id"] == app_id
    assert execution["status"] == "completed"
    assert execution["request"]["job_parameters"] == {"date": "2026-08-07"}
    assert execution["artifacts"][0]["schema"] == "gold"
    assert execution["lineage"]["inputs"][0]["table"] == "run_events"

    assert client.get(
        f"/v1/external-executions/{execution['execution_id']}",
        headers=_auth(api_key),
    ).status_code == 200
    assert client.get(
        f"/v1/external-executions/{execution['execution_id']}",
        headers=_auth(other_key),
    ).status_code == 404

    topics = {
        event["topic"]
        for event in client.get("/v1/events", headers=_auth(api_key)).json()
    }
    assert {"workflow.external.queued", "workflow.external.running", "workflow.external.completed"} <= topics


def test_external_failures_create_one_remote_execution_per_step_attempt(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "external_job_poll_interval_seconds", 0.001)
    app_id, api_key = _create_app(client)
    created = client.post(
        "/v1/workflows",
        headers=_auth(api_key),
        json=_external_workflow_payload(outcome="failed", polls=0, max_attempts=2),
    ).json()

    run = client.post(
        f"/v1/workflows/{created['id']}/runs",
        headers=_auth(api_key),
        params={"wait": "true"},
        json={"input": {}},
    ).json()
    assert run["status"] == "failed"
    executions = list_external_execution_records(run["run_id"], app_id)
    assert [execution.attempt for execution in executions] == [1, 2]
    assert all(execution.status == "failed" for execution in executions)


@pytest.mark.asyncio
async def test_recovery_resumes_active_external_run_without_resubmission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "external_job_poll_interval_seconds", 0.001)
    backend = external_backends.get("fake")
    assert isinstance(backend, FakeExternalBackend)
    specification = {"polls_before_completion": 0, "output": {"recovered": True}}
    submission = await backend.submit(specification, "existing-idempotency-key")
    step = RunStepRecord(
        run_id="workflow-run-recovery",
        run_type="workflow",
        app_id="app-recovery",
        step_index=0,
        name="resume remote run",
        type="workflow_external_job",
        target="fake",
        status="pending",
        attempt=1,
    )
    get_or_create_external_execution(
        ExternalExecutionRecord(
            step_id=step.step_id,
            run_id=step.run_id,
            app_id=step.app_id,
            provider="fake",
            attempt=1,
            status="running",
            external_run_id=submission.external_run_id,
            external_state="RUNNING",
            external_url=submission.external_url,
            idempotency_key="existing-idempotency-key",
            request=specification,
        )
    )

    output = await external_execution_service.execute(
        step,
        specification,
        UnityCatalogLineage(),
    )
    assert output["result"] == {"recovered": True}
    assert backend.submission_count == 1


@pytest.mark.asyncio
async def test_running_external_job_propagates_cancellation(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "external_job_poll_interval_seconds", 0.001)
    app_id, api_key = _create_app(client)
    workflow = client.post(
        "/v1/workflows",
        headers=_auth(api_key),
        json=_external_workflow_payload(polls=10_000, max_attempts=1),
    ).json()
    submitted = client.post(
        f"/v1/workflows/{workflow['id']}/runs",
        headers=_auth(api_key),
        json={"input": {}},
    ).json()

    task = asyncio.create_task(RunWorker("external-cancel-worker").run_once())
    for _ in range(100):
        executions = list_external_execution_records(submitted["run_id"], app_id)
        if executions and executions[0].status == "running":
            break
        await asyncio.sleep(0.002)
    else:
        pytest.fail("external execution did not start")

    await workflow_service.cancel_run(submitted["run_id"], app_id)
    assert await task is True
    run = get_workflow_run_record(submitted["run_id"], app_id)
    assert run and run.status == "cancelled"
    execution = list_external_execution_records(submitted["run_id"], app_id)[0]
    assert execution.status == "cancelling"


@pytest.mark.asyncio
async def test_databricks_jobs_adapter_maps_api_contract_and_reuses_oauth_token() -> None:
    calls: list[tuple[str, str, dict[str, Any] | None]] = []
    status_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal status_count
        body = (
            json.loads(request.content)
            if request.content and request.headers.get("content-type") == "application/json"
            else None
        )
        calls.append((request.method, request.url.path, body))
        if request.url.path == "/oidc/v1/token":
            assert request.headers["authorization"].startswith("Basic ")
            return httpx.Response(200, json={"access_token": "oauth-token", "expires_in": 3600})
        assert request.headers["authorization"] == "Bearer oauth-token"
        if request.url.path == "/api/2.2/jobs/run-now":
            assert body == {
                "job_id": 123,
                "idempotency_token": "i" * 64,
                "job_parameters": {"date": "2026-08-07"},
            }
            return httpx.Response(200, json={"run_id": 456, "run_page_url": "https://db/runs/456"})
        if request.url.path == "/api/2.2/jobs/runs/get":
            status_count += 1
            if status_count == 1:
                return httpx.Response(
                    200,
                    json={
                        "job_id": 123,
                        "run_name": "analytics",
                        "run_page_url": "https://db/runs/456",
                        "status": {"state": "RUNNING"},
                    },
                )
            return httpx.Response(
                200,
                json={
                    "job_id": 123,
                    "run_page_url": "https://db/runs/456",
                    "status": {"state": "TERMINATED", "termination_details": {"code": "SUCCESS"}},
                },
            )
        if request.url.path == "/api/2.2/jobs/runs/cancel":
            assert body == {"run_id": 456}
            return httpx.Response(200, json={})
        raise AssertionError(f"Unexpected Databricks request: {request.method} {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        backend = DatabricksJobsBackend(
            "https://workspace.cloud.databricks.com",
            client_id="client-id",
            client_secret="client-secret",
            client=http_client,
        )
        submission = await backend.submit(
            {"job_id": 123, "job_parameters": {"date": "2026-08-07"}},
            "i" * 80,
        )
        assert submission.external_run_id == "456"
        assert (await backend.get_status("456")).status == "running"
        completed = await backend.get_status("456")
        assert completed.status == "completed"
        assert completed.output["job_id"] == 123
        await backend.cancel("456")

    assert sum(path == "/oidc/v1/token" for _, path, _ in calls) == 1


@pytest.mark.asyncio
async def test_databricks_throttling_is_reported_as_retryable() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error_code": "REQUEST_LIMIT_EXCEEDED", "message": "slow down"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        backend = DatabricksJobsBackend(
            "https://workspace.cloud.databricks.com",
            token="test-token",
            client=http_client,
        )
        with pytest.raises(ExternalBackendError) as captured:
            await backend.submit({"job_id": 123}, "key")
    assert captured.value.retryable is True
    assert "429" in str(captured.value)


def test_databricks_workflow_definition_rejects_invalid_job_spec(client: TestClient) -> None:
    _, api_key = _create_app(client)
    response = client.post(
        "/v1/workflows",
        headers=_auth(api_key),
        json={
            "name": "Invalid Databricks job",
            "steps": [
                {
                    "name": "bad job",
                    "type": "external_job",
                    "target": "databricks",
                    "arguments": {"job_id": "not-an-integer"},
                }
            ],
        },
    )
    assert response.status_code == 422
    assert "positive integer job_id" in response.json()["detail"]

    secret_field = client.post(
        "/v1/workflows",
        headers=_auth(api_key),
        json={
            "name": "Persists a secret",
            "steps": [
                {
                    "name": "bad secret",
                    "type": "external_job",
                    "target": "databricks",
                    "arguments": {"job_id": 123, "token": "must-not-be-persisted"},
                }
            ],
        },
    )
    assert secret_field.status_code == 422
    assert "Unsupported Databricks job fields: token" in secret_field.json()["detail"]


def test_unconfigured_databricks_backend_fails_without_wasting_retry_budget(
    client: TestClient,
) -> None:
    app_id, api_key = _create_app(client)
    workflow = client.post(
        "/v1/workflows",
        headers=_auth(api_key),
        json={
            "name": "Requires Databricks",
            "steps": [
                {
                    "name": "remote job",
                    "type": "external_job",
                    "target": "databricks",
                    "arguments": {"job_id": 123},
                    "max_attempts": 3,
                }
            ],
        },
    ).json()
    run = client.post(
        f"/v1/workflows/{workflow['id']}/runs",
        headers=_auth(api_key),
        params={"wait": "true"},
        json={"input": {}},
    ).json()
    assert run["status"] == "failed"
    assert run["steps"][0]["attempt"] == 1
    execution = list_external_execution_records(run["run_id"], app_id)[0]
    assert execution.error["code"] == "external_backend_not_configured"
    assert execution.error["retryable"] is False
