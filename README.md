# Agent Orchestration Platform

A local-first backend platform for registering applications, defining deterministic agents, composing agent/tool/data workflows, and consuming runs and events through REST, GraphQL, MCP, SSE, or a Python SDK.

This repository is a runnable backend MVP with a durable execution engine and pluggable external compute. Agent and workflow submissions are persisted before execution, claimed by workers, and recorded step by step. External jobs can be submitted to a deterministic local backend or Databricks Jobs 2.2 and resumed safely after a worker restart. No model provider, Databricks workspace, or paid API is required for local development.

## What is included

- App registration with one-time API-key issuance
- SHA-256 storage for new high-entropy API keys, plus key rotation and revocation
- Strict app scoping for agents, runs, workflows, events, GraphQL, MCP, and SSE
- Two shared built-in agents and authenticated app-owned agent definitions
- Ordered workflows containing tool, agent, and/or external job steps
- Durable queued agent and workflow execution
- Independently persisted run steps, attempts, errors, and outputs
- Worker leases, heartbeats, stale-run recovery, retries, and timeouts
- Idempotent submission and cooperative cancellation
- In-process development worker and standalone `devplatform-worker` command
- Pluggable external execution backend contract and registry
- Deterministic fake backend for local development and testing
- Databricks Jobs 2.2 submission, polling, cancellation, PAT, and OAuth M2M support
- Persisted external attempts that resume without duplicate remote submission
- Governed Delta table artifacts and Unity Catalog input/output lineage
- PySpark, Spark SQL, Delta Lake, Databricks bundle, and optional Airflow examples
- Persisted run and lifecycle events
- REST and GraphQL APIs
- MCP `2026-07-28` discovery/tool responses with legacy `2025-11-25` initialization support
- Realtime app-scoped event delivery over Server-Sent Events
- Synchronous Python SDK
- Isolated integration tests

## Architecture

```text
REST / Python SDK                    GraphQL / MCP
        |                                 |
   queue + 202                     synchronous wait mode
        |                                 |
        +------------ SQL database -------+
                          |
                   worker claims run
                          |
                 durable step executor
                / retry / timeout / cancel
                    /             \
          local tools/agents    external backend registry
                                      /          \
                                  fake        Databricks Jobs
                                                   |
                                      PySpark / Spark SQL / Delta
                                                   |
                                      Unity Catalog governance
                          |
                 persisted events + SSE
```

The API commits a run in `queued` state before acknowledging it. A worker atomically changes that run to `running`, renews its lease while work is active, and stores each step independently. `AgentService` executes an agent's configured tools. `WorkflowService` executes ordered tool, agent, or external job steps and passes each result to the next step as `previous_output`. Every transition is persisted and published as an app-scoped event.

If a worker disappears, another worker releases its expired lease and resumes from the first incomplete step. Completed steps are not repeated. An active external execution is polled again using its persisted provider run ID; it is not resubmitted. The side-effecting demo `create_case` tool remains available for explicit calls and workflows but is intentionally absent from built-in agents' automatic tool chains.

## Quickstart

Python 3.11 or newer is required.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
uvicorn app.main:app --reload
```

The API starts an in-process worker by default for local development. To operate the API and worker separately:

```bash
export DEVPLATFORM_WORKER_ENABLED=false
uvicorn app.main:app --reload

# In a second terminal, using the same database URL:
devplatform-worker
```

Open `http://127.0.0.1:8000/docs` for the interactive REST documentation.

Register an app to receive its initial API key. The secret is returned only in this response.

```bash
curl -X POST http://127.0.0.1:8000/v1/apps \
  -H 'content-type: application/json' \
  -d '{"name":"Support Copilot","owner":"you@example.com"}'
```

Use the returned key on protected operations:

```bash
curl http://127.0.0.1:8000/v1/agents \
  -H 'x-api-key: dp_replace_with_your_key'
```

## Python SDK example

```python
from devplatform import DevPlatformClient

client = DevPlatformClient("http://127.0.0.1:8000")
registration = client.create_app("Support Copilot", "you@example.com")
client.api_key = registration["api_key"]

submitted = client.run_agent(
    "agent-support",
    {
        "question": "Investigate this customer's health",
        "customer_id": "customer-42",
        "title": "Customer health review",
    },
)
run = client.wait_for_run(submitted["run_id"])
print(run["output"]["summary"])

workflow = client.create_workflow(
    "Health investigation",
    [
        {
            "name": "collect metrics",
            "type": "tool",
            "target": "query_usage_metrics",
            "arguments": {"window": "1h"},
            "max_attempts": 3,
            "timeout_seconds": 10,
        },
        {
            "name": "analyze",
            "type": "agent",
            "target": "agent-ops",
            "arguments": {"question": "Summarize system health"},
        },
    ],
)
workflow_submission = client.run_workflow(workflow["id"], {"service": "api"})
workflow_run = client.wait_for_workflow_run(workflow_submission["run_id"])
print(workflow_run["status"])
```

External executions can be inspected separately from their owning step:

```python
backends = client.list_external_backends()
executions = client.get_workflow_external_executions(workflow_run["run_id"])
if executions:
    execution = client.get_external_execution(executions[0]["execution_id"])
    print(execution["external_url"], execution["artifacts"])
```

For scripts that deliberately want request-bound execution, both SDK submission methods accept `wait=True`. That compatibility mode still creates, claims, and records a durable run; it does not use the old in-memory execution path.

## Main REST endpoints

| Area | Endpoints |
|---|---|
| Bootstrap | `POST /v1/apps`, `GET /v1/health` |
| App and keys | `GET /v1/apps`, `POST/GET /v1/api-keys`, `DELETE /v1/api-keys/{id}` |
| Agents | `POST/GET /v1/agents`, `GET/PUT/DELETE /v1/agents/{id}`, `POST /v1/agents/run` |
| Agent runs | `GET /v1/runs`, `GET /v1/runs/{id}`, `GET /v1/runs/{id}/steps`, `POST /v1/runs/{id}/cancel` |
| Workflows | `POST/GET /v1/workflows`, `GET/PUT/DELETE /v1/workflows/{id}` |
| Workflow runs | `POST /v1/workflows/{id}/runs`, `GET /v1/workflow-runs`, `GET /v1/workflow-runs/{id}`, `GET /v1/workflow-runs/{id}/steps`, `POST /v1/workflow-runs/{id}/cancel` |
| External compute | `GET /v1/external-backends`, `GET /v1/external-executions/{id}`, `GET /v1/workflow-runs/{id}/external-executions` |
| Events | `POST /v1/events/publish`, `GET /v1/events`, `GET /v1/events/stream` |
| Tools | `POST /v1/tools/call` |
| Other protocols | `POST /graphql`, `POST /mcp` |

All endpoints except health and initial app registration require `x-api-key`. Cross-app resources deliberately return `404` instead of revealing that another tenant owns the identifier.

## Durable run lifecycle

`POST /v1/agents/run` and `POST /v1/workflows/{id}/runs` return `202 Accepted` by default. The response is the queued run and includes a `Location` header for polling.

```text
queued → running → completed
   │         ├──→ retrying → running
   │         ├──→ failed
   │         └──→ cancelling → cancelled
   └────────────────────────→ cancelled
```

An optional `idempotency_key` in the request prevents duplicate submissions within the same app and run type. Sending the same key again returns the original run. `max_attempts` controls recovery from unexpected run-level worker failures; individual step retry policies control ordinary tool failures.

Use `?wait=true` to wait for a terminal response. The synchronous wait has a configurable upper bound and returns `504` if that limit is exceeded.

## Defining agents and workflows

An app-owned agent supplies a private system prompt plus a list of registered tools. The API validates tool names when the agent is created. Agent responses never expose the system prompt.

```json
{
  "name": "Metrics Reader",
  "description": "Summarizes service telemetry",
  "system_prompt": "Summarize the available metrics.",
  "capabilities": ["operations"],
  "tools": ["query_usage_metrics"]
}
```

A workflow contains 1–50 ordered steps. A `tool` step targets a registered tool. An `agent` step targets a built-in agent or an agent owned by the same app. An `external_job` step targets a registered execution backend such as `fake` or `databricks`. Runtime input is merged with local tool and agent arguments; an external backend receives its explicit provider specification. The prior step result is supplied to subsequent local steps as `previous_output`. Each workflow step may define `max_attempts` (1–10) and `timeout_seconds` (up to one hour).

## External data execution

An external execution is persisted independently from the workflow step. It records the provider, local step attempt, idempotency key, external run ID/state/URL, poll timestamps, errors, result metadata, Delta artifacts, and Unity Catalog lineage. If polling fails transiently, the step retries against the same remote run. If the remote job itself fails and attempts remain, the next step attempt receives a new deterministic idempotency key and may launch a new remote run.

```json
{
  "name": "Build lakehouse metrics",
  "type": "external_job",
  "target": "databricks",
  "arguments": {
    "job_id": 12345,
    "job_parameters": {"catalog": "agent_platform"}
  },
  "data_lineage": {
    "inputs": [
      {"type": "delta_table", "catalog": "agent_platform", "schema": "bronze", "table": "run_events"}
    ],
    "outputs": [
      {"type": "delta_table", "catalog": "agent_platform", "schema": "gold", "table": "daily_run_metrics"}
    ]
  },
  "max_attempts": 2,
  "timeout_seconds": 3600
}
```

Unity Catalog identifiers use the conservative unquoted SQL form `[A-Za-z_][A-Za-z0-9_]*`. This prevents an artifact reference from becoming an unsafe generated SQL identifier. The platform validates and records the declared lineage; Databricks and Unity Catalog still enforce actual privileges, auditing, and engine-derived lineage.

The Databricks backend calls Jobs API 2.2 `run-now`, `runs/get`, and `runs/cancel`. Configure either a development PAT or OAuth M2M credentials for a service principal. Credentials are read only from settings and are never persisted with a workflow or execution.

To override job parameters at run time, send them under `input.job_parameters`. Static parameters in the workflow definition take precedence, preventing a caller from replacing deployment-controlled values:

```json
{"input": {"job_parameters": {"logical_date": "2026-08-07"}}}
```

The complete lakehouse example is in [`examples/databricks`](examples/databricks/README.md). It includes a Declarative Automation Bundle and a Spark Python task that performs a PySpark bronze merge followed by Spark SQL silver/gold materialization over Delta. The optional [`examples/airflow`](examples/airflow/README.md) DAG demonstrates Airflow scheduling one whole platform workflow through REST.

## MCP

The HTTP endpoint requires the same `x-api-key` header as REST. It supports `server/discover`, `tools/list`, and `tools/call`, along with the legacy `initialize` handshake. The tool catalog includes agent/workflow listing and execution plus persisted event publication.

For stdio, install the package and provide an active key:

```bash
export DEVPLATFORM_API_KEY=dp_replace_with_your_key
devplatform-mcp
```

The current database URL must point to the database containing that key.

## GraphQL

GraphQL is available at `/graphql` and requires `x-api-key`. Queries expose app-scoped agents, agent runs, and workflows. Mutations execute agents and workflows. Strawberry's GraphiQL interface is available through a browser request that supplies an API-key header.

## Configuration

Settings use the `DEVPLATFORM_` environment prefix.

| Variable | Default | Purpose |
|---|---|---|
| `DEVPLATFORM_DATABASE_URL` | `sqlite:///./dev_platform.db` | SQLAlchemy database URL |
| `DEVPLATFORM_ENVIRONMENT` | `dev` | Environment label |
| `DEVPLATFORM_API_KEY` | unset | Authentication for the stdio MCP server |
| `DEVPLATFORM_WORKER_ENABLED` | `true` | Run a worker inside the FastAPI process |
| `DEVPLATFORM_WORKER_POLL_INTERVAL_SECONDS` | `0.25` | Idle queue polling interval |
| `DEVPLATFORM_WORKER_LEASE_TIMEOUT_SECONDS` | `30` | Time before a missing heartbeat is considered stale |
| `DEVPLATFORM_WORKER_HEARTBEAT_INTERVAL_SECONDS` | `1` | Active run heartbeat interval |
| `DEVPLATFORM_WORKER_RECOVERY_INTERVAL_SECONDS` | `5` | Stale-run recovery scan interval |
| `DEVPLATFORM_SYNCHRONOUS_WAIT_TIMEOUT_SECONDS` | `60` | Maximum `wait=true` duration |
| `DEVPLATFORM_DEFAULT_STEP_TIMEOUT_SECONDS` | `30` | Default timeout for agent tool steps |
| `DEVPLATFORM_DEFAULT_STEP_MAX_ATTEMPTS` | `3` | Default attempts for agent tool steps |
| `DEVPLATFORM_RETRY_BASE_DELAY_SECONDS` | `0.1` | Base exponential retry delay |
| `DEVPLATFORM_EXTERNAL_JOB_POLL_INTERVAL_SECONDS` | `1` | Delay between external provider status checks |
| `DEVPLATFORM_DATABRICKS_HOST` | unset | Databricks workspace URL; unset leaves the backend unavailable |
| `DEVPLATFORM_DATABRICKS_TOKEN` | unset | Development PAT bearer token |
| `DEVPLATFORM_DATABRICKS_CLIENT_ID` | unset | OAuth M2M service-principal client ID |
| `DEVPLATFORM_DATABRICKS_CLIENT_SECRET` | unset | OAuth M2M service-principal secret |
| `DEVPLATFORM_DATABRICKS_REQUEST_TIMEOUT_SECONDS` | `30` | Timeout for Databricks API and OAuth requests |

On startup, the app applies additive SQLite upgrades for repositories created with v0.1–v0.4. Existing plaintext API keys are replaced with digests while the original client-side secrets continue to work. Existing completed runs receive compatible lifecycle timestamps, while v0.5 adds the external execution table without rewriting existing runs. For a multi-instance production deployment, replace this lightweight upgrade path with managed migrations and use a production database.

## Development

```bash
source .venv/bin/activate
pytest -q
```

Tests create and remove their own temporary SQLite database; they do not modify `dev_platform.db`.

## Intentional MVP boundaries

- Agent execution is deterministic and invokes configured Python tools in order; it is not yet an LLM planner.
- Durable execution coordinates through the database, but the included live event bus remains process-local. Persisted history survives restarts; SSE fan-out does not cross processes.
- Workflow control flow is currently ordered and sequential. DAG branches, parallel fan-out, and human approval states are not implemented yet.
- The Databricks integration triggers existing Lakeflow Jobs; the platform does not create or mutate remote job definitions. Use the included bundle or Databricks infrastructure-as-code for deployment.
- Delta artifacts and Unity Catalog lineage are declared control-plane metadata. Physical existence, privileges, and authoritative engine lineage are verified by Databricks, not by the local fake backend.
- PySpark and Airflow are example-environment dependencies, not API-service dependencies. Airflow should schedule whole platform workflows rather than duplicate their internal retry/state ownership.
- Database access is synchronous. A high-throughput deployment should move to async sessions or isolate blocking work.
- Initial app registration is open by design for local onboarding. A hosted service should place signup behind an identity and abuse-prevention layer.
- API keys are suitable for this local backend, while an internet-facing MCP deployment should add the OAuth-based authorization flow required by its deployment environment.
