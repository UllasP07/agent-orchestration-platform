from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

from app.core.config import settings
from app.db.repository import (
    get_latest_external_execution_for_step,
    get_or_create_external_execution,
    save_external_execution,
)
from app.external.backends.base import ExternalBackendError
from app.external.registry import external_backends
from app.models.schemas import ExternalExecutionRecord, RunStepRecord, UnityCatalogLineage


ExternalTransition = Callable[[ExternalExecutionRecord], Awaitable[None]]
_ACTIVE_STATUSES = {"pending", "submitting", "queued", "running", "cancelling"}


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


class ExternalExecutionService:
    async def execute(
        self,
        step: RunStepRecord,
        specification: dict[str, Any],
        lineage: UnityCatalogLineage,
        on_transition: ExternalTransition | None = None,
    ) -> dict[str, Any]:
        backend = external_backends.get(step.target)
        if not backend:
            raise ExternalBackendError(
                f"Unknown external execution backend: {step.target}",
                code="external_backend_not_found",
                retryable=False,
            )
        try:
            backend.validate_spec(specification)
        except ValueError as exc:
            raise ExternalBackendError(
                str(exc),
                code="invalid_external_job_specification",
                retryable=False,
            ) from exc

        latest = get_latest_external_execution_for_step(step.step_id)
        if latest and latest.status == "completed":
            return self._step_output(latest)
        if latest and latest.status in _ACTIVE_STATUSES:
            execution = latest
        elif latest and latest.attempt == step.attempt:
            raise ExternalBackendError(
                latest.error.get("message", "External execution failed"),
                code=latest.error.get("code", "external_run_failed"),
                retryable=False,
            )
        else:
            idempotency_key = hashlib.sha256(
                f"{step.run_id}:{step.step_id}:{step.attempt}".encode("utf-8")
            ).hexdigest()
            execution = get_or_create_external_execution(
                ExternalExecutionRecord(
                    step_id=step.step_id,
                    run_id=step.run_id,
                    app_id=step.app_id,
                    provider=step.target,
                    attempt=step.attempt,
                    idempotency_key=idempotency_key,
                    request=specification,
                    lineage=lineage,
                )
            )
            await self._notify(execution, on_transition)

        try:
            if not execution.external_run_id:
                execution.status = "submitting"
                execution.error = {}
                await self._persist(execution, on_transition)
                try:
                    submission = await backend.submit(
                        execution.request,
                        execution.idempotency_key,
                    )
                except Exception as exc:
                    execution.status = "failed"
                    execution.error = self._error(exc, "external_submission_failed")
                    execution.completed_at = _now_utc()
                    await self._persist(execution, on_transition)
                    raise
                execution.external_run_id = submission.external_run_id
                execution.external_state = submission.external_state
                execution.external_url = submission.external_url
                execution.status = submission.status
                execution.submitted_at = _now_utc()
                execution.output = submission.metadata
                await self._persist(execution, on_transition)

            while True:
                try:
                    snapshot = await backend.get_status(execution.external_run_id)
                except Exception as exc:
                    execution.error = self._error(exc, "external_status_failed")
                    await self._persist(execution, on_transition)
                    raise

                previous_status = execution.status
                previous_state = execution.external_state
                execution.status = snapshot.status
                execution.external_state = snapshot.external_state
                execution.external_url = snapshot.external_url or execution.external_url
                execution.output = snapshot.output
                execution.error = snapshot.error
                execution.last_polled_at = _now_utc()

                if snapshot.status == "completed":
                    execution.artifacts = list(lineage.outputs)
                    execution.completed_at = _now_utc()
                    await self._persist(execution, on_transition)
                    return self._step_output(execution)
                if snapshot.status in {"failed", "cancelled"}:
                    execution.completed_at = _now_utc()
                    if not execution.error:
                        execution.error = {
                            "code": f"external_run_{snapshot.status}",
                            "message": f"External run ended with status {snapshot.status}",
                        }
                    await self._persist(execution, on_transition)
                    raise ExternalBackendError(
                        execution.error["message"],
                        code=execution.error.get("code", "external_run_failed"),
                        retryable=snapshot.status == "failed",
                    )

                if execution.status != previous_status or execution.external_state != previous_state:
                    await self._persist(execution, on_transition)
                else:
                    execution.updated_at = _now_utc()
                    save_external_execution(execution)
                await asyncio.sleep(settings.external_job_poll_interval_seconds)
        except asyncio.CancelledError:
            if execution.external_run_id and execution.status in _ACTIVE_STATUSES:
                try:
                    await backend.cancel(execution.external_run_id)
                    execution.status = "cancelling"
                    execution.error = {
                        "code": "external_cancellation_requested",
                        "message": "Cancellation was forwarded to the external backend",
                    }
                except Exception as exc:  # cancellation must not hide local task cancellation
                    execution.error = self._error(exc, "external_cancellation_failed")
                await self._persist(execution, on_transition)
            raise

    async def _persist(
        self,
        execution: ExternalExecutionRecord,
        callback: ExternalTransition | None,
    ) -> None:
        execution.updated_at = _now_utc()
        save_external_execution(execution)
        await self._notify(execution, callback)

    @staticmethod
    async def _notify(
        execution: ExternalExecutionRecord,
        callback: ExternalTransition | None,
    ) -> None:
        if callback:
            await callback(execution)

    @staticmethod
    def _error(exc: Exception, fallback_code: str) -> dict[str, Any]:
        return {
            "code": getattr(exc, "code", fallback_code),
            "message": str(exc),
            "exception_type": type(exc).__name__,
            "retryable": getattr(exc, "retryable", True),
        }

    @staticmethod
    def _step_output(execution: ExternalExecutionRecord) -> dict[str, Any]:
        return {
            "external_execution_id": execution.execution_id,
            "provider": execution.provider,
            "external_run_id": execution.external_run_id,
            "external_state": execution.external_state,
            "external_url": execution.external_url,
            "result": execution.output,
            "artifacts": [
                artifact.model_dump(mode="json", by_alias=True) for artifact in execution.artifacts
            ],
            "lineage": execution.lineage.model_dump(mode="json", by_alias=True),
        }


external_execution_service = ExternalExecutionService()
