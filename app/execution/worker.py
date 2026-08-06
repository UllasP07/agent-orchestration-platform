from __future__ import annotations

import asyncio
import os
import socket
from time import monotonic
from uuid import uuid4

from app.agents.service import agent_service
from app.core.config import settings
from app.db.repository import (
    claim_next_run,
    recover_stale_runs,
    requeue_or_fail_run,
    save_event_record,
)
from app.events.bus import event_bus
from app.execution.engine import WorkerLeaseLost
from app.models.schemas import PlatformEvent
from app.workflows.service import workflow_service


class RunWorker:
    def __init__(self, worker_id: str | None = None) -> None:
        self.worker_id = worker_id or self._default_worker_id()
        self._stop = asyncio.Event()

    async def run_once(self) -> bool:
        work = claim_next_run(self.worker_id)
        if not work:
            return False
        run_type, run = work
        try:
            if run_type == "agent":
                await agent_service.execute_claimed_run(run.run_id, self.worker_id)
            else:
                await workflow_service.execute_claimed_run(run.run_id, self.worker_id)
        except WorkerLeaseLost:
            # Another recovery path has already released or reassigned the run.
            return True
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover - infrastructure boundary
            delay = settings.retry_base_delay_seconds * (2 ** max(run.attempt - 1, 0))
            updated = requeue_or_fail_run(
                run_type,
                run.run_id,
                {
                    "code": "worker_execution_failed",
                    "message": str(exc),
                    "exception_type": type(exc).__name__,
                },
                delay,
            )
            if updated:
                topic_prefix = "run" if run_type == "agent" else "workflow.run"
                event = PlatformEvent(
                    app_id=updated.app_id,
                    topic=f"{topic_prefix}.{updated.status}",
                    payload={
                        "run_id": updated.run_id,
                        "attempt": updated.attempt,
                        "error": updated.error,
                    },
                    source="run-worker",
                )
                save_event_record(event)
                await event_bus.publish(event)
        return True

    async def run_forever(self) -> None:
        recover_stale_runs(settings.worker_lease_timeout_seconds)
        last_recovery = monotonic()
        while not self._stop.is_set():
            if monotonic() - last_recovery >= settings.worker_recovery_interval_seconds:
                recover_stale_runs(settings.worker_lease_timeout_seconds)
                last_recovery = monotonic()
            worked = await self.run_once()
            if worked:
                continue
            try:
                await asyncio.wait_for(
                    self._stop.wait(),
                    timeout=settings.worker_poll_interval_seconds,
                )
            except TimeoutError:
                pass

    def request_stop(self) -> None:
        self._stop.set()

    @staticmethod
    def _default_worker_id() -> str:
        return f"{socket.gethostname()}-{os.getpid()}-{uuid4().hex[:8]}"
