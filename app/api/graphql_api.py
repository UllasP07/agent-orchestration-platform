from __future__ import annotations

from typing import Any

import strawberry
from fastapi import Depends
from strawberry.fastapi import GraphQLRouter
from strawberry.scalars import JSON
from strawberry.types import Info

from app.agents.service import agent_service
from app.api.deps import require_api_key
from app.db.repository import list_agent_records, list_run_records, list_workflow_records
from app.models.schemas import APIKeyRecord
from app.workflows.service import workflow_service


@strawberry.type
class AgentType:
    id: str
    name: str
    description: str
    capabilities: list[str]
    tools: list[str]
    built_in: bool


@strawberry.type
class AgentRunType:
    run_id: str
    agent_id: str
    status: str
    output: JSON


@strawberry.type
class WorkflowType:
    id: str
    name: str
    description: str
    steps: JSON


@strawberry.type
class WorkflowRunType:
    run_id: str
    workflow_id: str
    status: str
    output: JSON


@strawberry.input
class RunAgentInput:
    agent_id: str
    payload: JSON
    context: JSON | None = None


@strawberry.input
class RunWorkflowInput:
    workflow_id: str
    payload: JSON
    context: JSON | None = None


def _api_key(info: Info) -> APIKeyRecord:
    return info.context["api_key"]


@strawberry.type
class Query:
    @strawberry.field
    def agents(self, info: Info) -> list[AgentType]:
        api_key = _api_key(info)
        return [
            AgentType(
                id=agent.id,
                name=agent.name,
                description=agent.description,
                capabilities=agent.capabilities,
                tools=agent.tools,
                built_in=agent.app_id is None,
            )
            for agent in list_agent_records(api_key.app_id)
        ]

    @strawberry.field
    def runs(self, info: Info, limit: int = 100) -> list[AgentRunType]:
        api_key = _api_key(info)
        safe_limit = max(1, min(limit, 500))
        return [
            AgentRunType(
                run_id=run.run_id,
                agent_id=run.agent_id,
                status=run.status,
                output=run.output,
            )
            for run in list_run_records(api_key.app_id, safe_limit)
        ]

    @strawberry.field
    def workflows(self, info: Info) -> list[WorkflowType]:
        api_key = _api_key(info)
        return [
            WorkflowType(
                id=workflow.id,
                name=workflow.name,
                description=workflow.description,
                steps=[step.model_dump(mode="json") for step in workflow.steps],
            )
            for workflow in list_workflow_records(api_key.app_id)
        ]


@strawberry.type
class Mutation:
    @strawberry.mutation
    async def run_agent(self, info: Info, input: RunAgentInput) -> AgentRunType:
        api_key = _api_key(info)
        run = await agent_service.run_agent(
            input.agent_id,
            dict(input.payload),
            api_key.app_id,
            dict(input.context or {}),
        )
        return AgentRunType(
            run_id=run.run_id,
            agent_id=run.agent_id,
            status=run.status,
            output=run.output,
        )

    @strawberry.mutation
    async def run_workflow(self, info: Info, input: RunWorkflowInput) -> WorkflowRunType:
        api_key = _api_key(info)
        run = await workflow_service.run_workflow(
            input.workflow_id,
            dict(input.payload),
            api_key.app_id,
            dict(input.context or {}),
        )
        return WorkflowRunType(
            run_id=run.run_id,
            workflow_id=run.workflow_id,
            status=run.status,
            output=run.output,
        )


async def get_graphql_context(
    api_key: APIKeyRecord = Depends(require_api_key),
) -> dict[str, Any]:
    return {"api_key": api_key}


schema = strawberry.Schema(query=Query, mutation=Mutation)
graphql_router = GraphQLRouter(schema, context_getter=get_graphql_context)
