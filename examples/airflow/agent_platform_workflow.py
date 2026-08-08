"""Optional Airflow 3 DAG that schedules a platform workflow through REST.

Install the HTTP provider in the Airflow environment and configure an HTTP
connection named ``agent_platform``. Put the platform base URL in Host and the
API key in the connection Extras as an ``x-api-key`` header. No Airflow package
is required by the Agent Orchestration Platform itself.
"""

from __future__ import annotations

from datetime import datetime

from airflow.providers.http.operators.http import HttpOperator
from airflow.providers.http.sensors.http import HttpSensor
from airflow.sdk import DAG, Param


TERMINAL_STATES = {"cancelled", "completed", "failed"}


def is_terminal(response) -> bool:
    return response.json().get("status") in TERMINAL_STATES


def is_successful(response) -> bool:
    return response.json().get("status") == "completed"


with DAG(
    dag_id="agent_platform_lakehouse_workflow",
    start_date=datetime(2026, 1, 1),
    schedule="@daily",
    catchup=False,
    params={"workflow_id": Param("workflow-replace-me", type="string")},
    tags=["agent-platform", "databricks", "lakehouse"],
) as dag:
    trigger = HttpOperator(
        task_id="trigger_platform_workflow",
        http_conn_id="agent_platform",
        endpoint="/v1/workflows/{{ params.workflow_id }}/runs",
        method="POST",
        headers={"content-type": "application/json"},
        data=(
            '{"input":{"job_parameters":{"logical_date":"{{ ds }}"}},'
            '"idempotency_key":"airflow-{{ dag_run.run_id }}"}'
        ),
        response_filter=lambda response: response.json()["run_id"],
        log_response=True,
    )

    wait = HttpSensor(
        task_id="wait_for_platform_workflow",
        http_conn_id="agent_platform",
        endpoint=(
            "/v1/workflow-runs/"
            "{{ task_instance.xcom_pull(task_ids='trigger_platform_workflow') }}"
        ),
        method="GET",
        response_check=is_terminal,
        poke_interval=15,
        timeout=7200,
        mode="reschedule",
    )

    verify = HttpOperator(
        task_id="require_successful_platform_workflow",
        http_conn_id="agent_platform",
        endpoint=(
            "/v1/workflow-runs/"
            "{{ task_instance.xcom_pull(task_ids='trigger_platform_workflow') }}"
        ),
        method="GET",
        response_check=is_successful,
        log_response=True,
    )

    trigger >> wait >> verify
