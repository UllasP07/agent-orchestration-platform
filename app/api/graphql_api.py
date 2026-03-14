from __future__ import annotations

import strawberry
from strawberry.fastapi import GraphQLRouter
from strawberry.scalars import JSON

from app.agents.service import agent_service
from app.core.registry import registry
from app.db.repository import list_run_records


@strawberry.type
class AgentType:
    id: str
    name: str
    description: str
    capabilities: list[str]
    tools: list[str]


@strawberry.type
class AgentRunType:
    run_id: str
    status: str
    output: JSON


@strawberry.input
class RunAgentInput:
    agent_id: str
    payload: JSON


@strawberry.type
class Query:
    @strawberry.field
    def agents(self) -> list[AgentType]:
        return [AgentType(**agent.model_dump(exclude={"system_prompt"})) for agent in registry.agents.values()]

    @strawberry.field
    def runs(self) -> list[AgentRunType]:
        return [
            AgentRunType(run_id=run.run_id, status=run.status, output=run.output)
            for run in list_run_records()
        ]


@strawberry.type
class Mutation:
    @strawberry.mutation
    async def run_agent(self, input: RunAgentInput) -> AgentRunType:
        run = await agent_service.run_agent(input.agent_id, dict(input.payload), {})
        return AgentRunType(run_id=run.run_id, status=run.status, output=run.output)


schema = strawberry.Schema(query=Query, mutation=Mutation)
graphql_router = GraphQLRouter(schema)