from app.core.registry import registry
from app.core.tools import register_default_tools
from app.models.schemas import AgentDefinition


DEFAULT_AGENTS = [
    AgentDefinition(
        id="agent-support",
        name="Support Analyst",
        description="Investigates customer issues using profile and product telemetry.",
        system_prompt="You are a support operations agent.",
        capabilities=["support", "triage", "summarization"],
        tools=["get_customer_profile", "query_usage_metrics", "create_case"],
    ),
    AgentDefinition(
        id="agent-ops",
        name="Ops Investigator",
        description="Analyzes service health and summarizes operational anomalies.",
        system_prompt="You are a site reliability operations agent.",
        capabilities=["operations", "incident-analysis"],
        tools=["query_usage_metrics"],
    ),
]


def bootstrap() -> None:
    register_default_tools()
    for agent in DEFAULT_AGENTS:
        registry.agents.setdefault(agent.id, agent)