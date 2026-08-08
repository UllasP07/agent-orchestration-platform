from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from datetime import datetime, timezone
from time import monotonic

from app.core.config import settings
from app.db.repository import heartbeat_run, run_cancel_requested, save_run_step
from app.models.schemas import RunStepRecord

StepOperation = Callable[[], Awaitable[dict]]
StepTransition = Callable[[RunStepRecord], Awaitable[None]]


class RunCancelled(Exception):
    """Raised when the owning run receives a cancellation request."""


class StepExecutionFailed(Exception):
    def __init__(self, step: RunStepRecord):
        self.step = step
        super().__init__(step.error.get("message", f"Step {step.name} failed"))


class WorkerLeaseLost(Exception):
    """Raised when a worker no longer owns the run it is executing."""


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


async def _transition(step: RunStepRecord, callback: StepTransition | None) -> None:
    step.updated_at = _now_utc()
    save_run_step(step)
    if callback:
        await callback(step)


async def _cancel_task(task: asyncio.Task[dict]) -> None:
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task


async def execute_step(
    step: RunStepRecord,
    operation: StepOperation,
    worker_id: str,
    on_transition: StepTransition | None = None,
) -> RunStepRecord:
    """Execute one durable step with retry, timeout, lease, and cancel control."""
    if step.status == "completed":
        return step

    while step.attempt < step.max_attempts:
        if run_cancel_requested(step.run_type, step.run_id):
            step.status = "cancelled"
            step.completed_at = _now_utc()
            step.error = {"code": "run_cancelled", "message": "Run cancellation requested"}
            await _transition(step, on_transition)
            raise RunCancelled(step.error["message"])
        if not heartbeat_run(step.run_type, step.run_id, worker_id):
            raise WorkerLeaseLost(f"Worker {worker_id} lost the run lease")

        step.attempt += 1
        step.status = "running"
        step.started_at = step.started_at or _now_utc()
        step.completed_at = None
        step.error = {}
        await _transition(step, on_transition)

        operation_task = asyncio.create_task(operation())
        attempt_started = monotonic()
        last_heartbeat = attempt_started
        try:
            while True:
                elapsed = monotonic() - attempt_started
                remaining = step.timeout_seconds - elapsed
                if remaining <= 0:
                    await _cancel_task(operation_task)
                    raise TimeoutError(f"Step exceeded {step.timeout_seconds:g} seconds")

                done, _ = await asyncio.wait(
                    {operation_task},
                    timeout=min(0.1, remaining),
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if operation_task in done:
                    result = operation_task.result()
                    if not isinstance(result, dict):
                        raise TypeError("Step operations must return an object")
                    step.status = "completed"
                    step.output = result
                    step.error = {}
                    step.completed_at = _now_utc()
                    await _transition(step, on_transition)
                    return step

                if run_cancel_requested(step.run_type, step.run_id):
                    await _cancel_task(operation_task)
                    step.status = "cancelled"
                    step.completed_at = _now_utc()
                    step.error = {"code": "run_cancelled", "message": "Run cancellation requested"}
                    await _transition(step, on_transition)
                    raise RunCancelled(step.error["message"])

                if monotonic() - last_heartbeat >= settings.worker_heartbeat_interval_seconds:
                    if not heartbeat_run(step.run_type, step.run_id, worker_id):
                        await _cancel_task(operation_task)
                        raise WorkerLeaseLost(f"Worker {worker_id} lost the run lease")
                    last_heartbeat = monotonic()
        except asyncio.CancelledError:
            await _cancel_task(operation_task)
            raise
        except (RunCancelled, WorkerLeaseLost):
            raise
        except Exception as exc:
            step.error = {
                "code": "step_timeout" if isinstance(exc, TimeoutError) else "step_execution_failed",
                "message": str(exc),
                "exception_type": type(exc).__name__,
                "retryable": getattr(exc, "retryable", True),
            }
            if step.attempt >= step.max_attempts or not step.error["retryable"]:
                step.status = "failed"
                step.completed_at = _now_utc()
                await _transition(step, on_transition)
                raise StepExecutionFailed(step) from exc

            step.status = "retrying"
            await _transition(step, on_transition)
            delay = settings.retry_base_delay_seconds * (2 ** (step.attempt - 1))
            delay_started = monotonic()
            last_retry_heartbeat = delay_started
            while monotonic() - delay_started < delay:
                if run_cancel_requested(step.run_type, step.run_id):
                    step.status = "cancelled"
                    step.completed_at = _now_utc()
                    step.error = {"code": "run_cancelled", "message": "Run cancellation requested"}
                    await _transition(step, on_transition)
                    raise RunCancelled(step.error["message"])
                if monotonic() - last_retry_heartbeat >= settings.worker_heartbeat_interval_seconds:
                    if not heartbeat_run(step.run_type, step.run_id, worker_id):
                        raise WorkerLeaseLost(f"Worker {worker_id} lost the run lease")
                    last_retry_heartbeat = monotonic()
                await asyncio.sleep(min(0.05, delay))

    raise StepExecutionFailed(step)
