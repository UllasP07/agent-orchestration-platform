# Optional Airflow integration

This DAG makes Airflow the schedule owner and the Agent Orchestration Platform
the durable run owner. It triggers one platform workflow, waits without holding
a worker slot, and fails unless the platform run completes successfully.

Install Airflow and `apache-airflow-providers-http` in a separate Airflow
environment. Create an HTTP connection named `agent_platform` with:

- Host: the platform base URL, for example `http://platform-api:8000`
- Extra headers: `{"x-api-key": "dp_replace_with_an_active_key"}`

Copy or mount `agent_platform_workflow.py` into the Airflow DAG bundle. Set the
`workflow_id` DAG parameter to an existing platform workflow.

Airflow is intentionally not installed into the API service. Running both
schedulers over the same internal task graph would duplicate retry and state
ownership; this example keeps the boundary at one platform workflow run.
