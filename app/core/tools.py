from __future__ import annotations

from typing import Any

from app.core.registry import registry


async def get_customer_profile(arguments: dict[str, Any]) -> dict[str, Any]:
    customer_id = arguments.get("customer_id", "unknown")
    return {
        "customer_id": customer_id,
        "segment": "enterprise",
        "health": "at_risk" if customer_id.endswith("42") else "healthy",
    }


async def query_usage_metrics(arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        "window": arguments.get("window", "24h"),
        "active_users": 1821,
        "error_rate": 0.014,
        "latency_p95_ms": 221,
    }


async def create_case(arguments: dict[str, Any]) -> dict[str, Any]:
    title = arguments.get("title", "Untitled case")
    return {"case_id": f"case-{abs(hash(title)) % 100000}", "title": title, "status": "open"}


DEFAULT_TOOLS = {
    "get_customer_profile": get_customer_profile,
    "query_usage_metrics": query_usage_metrics,
    "create_case": create_case,
}


def register_default_tools() -> None:
    registry.tools.update(DEFAULT_TOOLS)