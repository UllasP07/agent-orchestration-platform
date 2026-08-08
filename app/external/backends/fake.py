from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal
from uuid import uuid4

from app.external.backends.base import (
    ExternalBackendError,
    ExternalRunSnapshot,
    ExternalRunSubmission,
)


@dataclass
class _FakeRun:
    external_run_id: str
    remaining_polls: int
    outcome: Literal["completed", "failed"]
    output: dict[str, Any]
    cancelled: bool = False


class FakeExternalBackend:
    """Deterministic backend used by local development and reliability tests."""

    name = "fake"

    def __init__(self) -> None:
        self._runs: dict[str, _FakeRun] = {}
        self._idempotency: dict[str, str] = {}
        self.submission_count = 0

    def validate_spec(self, specification: dict[str, Any]) -> None:
        polls = specification.get("polls_before_completion", 1)
        if not isinstance(polls, int) or isinstance(polls, bool) or not 0 <= polls <= 10_000:
            raise ValueError("polls_before_completion must be an integer between 0 and 10000")
        outcome = specification.get("outcome", "completed")
        if outcome not in {"completed", "failed"}:
            raise ValueError("outcome must be 'completed' or 'failed'")
        if not isinstance(specification.get("output", {}), dict):
            raise ValueError("output must be an object")

    async def submit(
        self,
        specification: dict[str, Any],
        idempotency_key: str,
    ) -> ExternalRunSubmission:
        self.validate_spec(specification)
        existing_id = self._idempotency.get(idempotency_key)
        if existing_id:
            return ExternalRunSubmission(
                external_run_id=existing_id,
                status="queued",
                external_state="QUEUED",
                external_url=f"fake://runs/{existing_id}",
            )

        external_run_id = f"fake-run-{uuid4().hex[:12]}"
        self._runs[external_run_id] = _FakeRun(
            external_run_id=external_run_id,
            remaining_polls=specification.get("polls_before_completion", 1),
            outcome=specification.get("outcome", "completed"),
            output=specification.get("output", {}),
        )
        self._idempotency[idempotency_key] = external_run_id
        self.submission_count += 1
        return ExternalRunSubmission(
            external_run_id=external_run_id,
            status="queued",
            external_state="QUEUED",
            external_url=f"fake://runs/{external_run_id}",
        )

    async def get_status(self, external_run_id: str) -> ExternalRunSnapshot:
        run = self._runs.get(external_run_id)
        if not run:
            raise ExternalBackendError(
                f"Unknown fake external run: {external_run_id}",
                code="external_run_not_found",
                retryable=False,
            )
        if run.cancelled:
            return ExternalRunSnapshot(
                external_run_id=external_run_id,
                status="cancelled",
                external_state="CANCELLED",
                external_url=f"fake://runs/{external_run_id}",
                error={"code": "external_run_cancelled", "message": "Fake run was cancelled"},
            )
        if run.remaining_polls > 0:
            run.remaining_polls -= 1
            return ExternalRunSnapshot(
                external_run_id=external_run_id,
                status="running",
                external_state="RUNNING",
                external_url=f"fake://runs/{external_run_id}",
            )
        if run.outcome == "completed":
            return ExternalRunSnapshot(
                external_run_id=external_run_id,
                status="completed",
                external_state="TERMINATED:SUCCESS",
                external_url=f"fake://runs/{external_run_id}",
                output=run.output,
            )
        return ExternalRunSnapshot(
            external_run_id=external_run_id,
            status="failed",
            external_state="TERMINATED:FAILED",
            external_url=f"fake://runs/{external_run_id}",
            error={"code": "external_run_failed", "message": "Fake run failed as requested"},
        )

    async def cancel(self, external_run_id: str) -> None:
        run = self._runs.get(external_run_id)
        if not run:
            raise ExternalBackendError(
                f"Unknown fake external run: {external_run_id}",
                code="external_run_not_found",
                retryable=False,
            )
        run.cancelled = True
