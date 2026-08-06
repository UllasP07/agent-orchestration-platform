# Agent Orchestration Platform

A local-first backend platform for registering applications, defining deterministic agents, composing agent/tool workflows, and consuming runs and events through REST, GraphQL, MCP, SSE, or a Python SDK.

This repository is a runnable backend MVP. Its built-in tools return deterministic demo data, so no model provider or paid API is required. The persistence and orchestration boundaries are designed so real tools or an LLM-backed planner can be added later.

## What is included

- App registration with one-time API-key issuance
- SHA-256 storage for new high-entropy API keys, plus key rotation and revocation
- Strict app scoping for agents, runs, workflows, events, GraphQL, MCP, and SSE
- Two shared built-in agents and authenticated app-owned agent definitions
- Ordered workflows containing tool and/or agent steps
- Persisted agent runs, workflow runs, and lifecycle events
- REST and GraphQL APIs
- MCP `2026-07-28` discovery/tool responses with legacy `2025-11-25` initialization support
- Realtime app-scoped event delivery over Server-Sent Events
- Synchronous Python SDK
- Isolated integration tests

## Architecture

```text
REST / GraphQL / MCP / Python SDK
               |
        API-key boundary
               |
      +--------+---------+
      |                  |
 AgentService      WorkflowService
      |                  |
      +---- Tool registry+
               |
       SQLAlchemy / SQLite ---- EventBus ---- SSE subscribers
```

`AgentService` executes the tools configured on an accessible agent. `WorkflowService` executes ordered tool or agent steps and passes each result to the next step as `previous_output`. Every run transition is persisted and published as an app-scoped event. The side-effecting demo `create_case` tool is available for explicit calls and workflows but is intentionally not part of a built-in agent's automatic tool chain.

## Quickstart

Python 3.11 or newer is required.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
uvicorn app.main:app --reload
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

run = client.run_agent(
    "agent-support",
    {
        "question": "Investigate this customer's health",
        "customer_id": "customer-42",
        "title": "Customer health review",
    },
)
print(run["output"]["summary"])

workflow = client.create_workflow(
    "Health investigation",
    [
        {
            "name": "collect metrics",
            "type": "tool",
            "target": "query_usage_metrics",
            "arguments": {"window": "1h"},
        },
        {
            "name": "analyze",
            "type": "agent",
            "target": "agent-ops",
            "arguments": {"question": "Summarize system health"},
        },
    ],
)
workflow_run = client.run_workflow(workflow["id"], {"service": "api"})
print(workflow_run["status"])
```

## Main REST endpoints

| Area | Endpoints |
|---|---|
| Bootstrap | `POST /v1/apps`, `GET /v1/health` |
| App and keys | `GET /v1/apps`, `POST/GET /v1/api-keys`, `DELETE /v1/api-keys/{id}` |
| Agents | `POST/GET /v1/agents`, `GET/PUT/DELETE /v1/agents/{id}`, `POST /v1/agents/run` |
| Agent runs | `GET /v1/runs`, `GET /v1/runs/{id}` |
| Workflows | `POST/GET /v1/workflows`, `GET/PUT/DELETE /v1/workflows/{id}` |
| Workflow runs | `POST /v1/workflows/{id}/runs`, `GET /v1/workflow-runs`, `GET /v1/workflow-runs/{id}` |
| Events | `POST /v1/events/publish`, `GET /v1/events`, `GET /v1/events/stream` |
| Tools | `POST /v1/tools/call` |
| Other protocols | `POST /graphql`, `POST /mcp` |

All endpoints except health and initial app registration require `x-api-key`. Cross-app resources deliberately return `404` instead of revealing that another tenant owns the identifier.

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

A workflow contains 1–50 ordered steps. A `tool` step targets a registered tool. An `agent` step targets a built-in agent or an agent owned by the same app. Runtime input is merged with each step's static `arguments`; static arguments take precedence. The prior step result is supplied as `previous_output`.

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

On startup, the app applies additive SQLite upgrades for repositories created with v0.1/v0.2. Existing plaintext API keys are replaced with digests while the original client-side secrets continue to work. For a multi-instance production deployment, replace this lightweight upgrade path with managed migrations and use a production database.

## Development

```bash
source .venv/bin/activate
pytest -q
```

Tests create and remove their own temporary SQLite database; they do not modify `dev_platform.db`.

## Intentional MVP boundaries

- Agent execution is deterministic and invokes configured Python tools in order; it is not yet an LLM planner.
- The included event bus is process-local. Persisted history survives restarts, but live SSE fan-out does not cross processes.
- Database access is synchronous. A high-throughput deployment should move to async sessions or isolate blocking work.
- Initial app registration is open by design for local onboarding. A hosted service should place signup behind an identity and abuse-prevention layer.
- API keys are suitable for this local backend, while an internet-facing MCP deployment should add the OAuth-based authorization flow required by its deployment environment.
