# Developer Platform & AI Agents

A production-style Python project that demonstrates a developer platform for external developers and AI agents.

## Included

- REST API for agents, workflows, tool execution, applications, and event publishing
- GraphQL API for flexible querying and mutations
- MCP-compatible JSON-RPC endpoint for AI agent tooling
- Realtime event delivery over Server-Sent Events (SSE)
- Simple Python SDK and REST-first client wrappers
- In-memory registry for apps, agents, runs, and event subscriptions
- Tests covering the core API surface

## Architecture

- **FastAPI** hosts REST endpoints, SSE, and the MCP endpoint
- **Strawberry GraphQL** exposes typed query/mutation access
- **EventBus** fans out platform events to many subscribers
- **AgentService** runs deterministic AI-agent style plans using registered tools
- **MCP server** exposes tools such as listing agents, running an agent, and publishing an event

## Quickstart

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
uvicorn app.main:app --reload