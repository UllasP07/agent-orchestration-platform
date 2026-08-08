from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol


BackendRunStatus = Literal["queued", "running", "cancelled", "completed", "failed"]


class ExternalBackendError(RuntimeError):
    def __init__(self, message: str, *, code: str = "external_backend_error", retryable: bool = True):
        self.code = code
        self.retryable = retryable
        super().__init__(message)


@dataclass(slots=True)
class ExternalRunSubmission:
    external_run_id: str
    status: BackendRunStatus = "queued"
    external_state: str | None = None
    external_url: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ExternalRunSnapshot:
    external_run_id: str
    status: BackendRunStatus
    external_state: str | None = None
    external_url: str | None = None
    output: dict[str, Any] = field(default_factory=dict)
    error: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


class ExternalExecutionBackend(Protocol):
    name: str

    def validate_spec(self, specification: dict[str, Any]) -> None: ...

    async def submit(
        self,
        specification: dict[str, Any],
        idempotency_key: str,
    ) -> ExternalRunSubmission: ...

    async def get_status(self, external_run_id: str) -> ExternalRunSnapshot: ...

    async def cancel(self, external_run_id: str) -> None: ...
