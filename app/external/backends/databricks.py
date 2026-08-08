from __future__ import annotations

from time import monotonic
from typing import Any

import httpx

from app.external.backends.base import (
    ExternalBackendError,
    ExternalRunSnapshot,
    ExternalRunSubmission,
)


_QUEUED_STATES = {"BLOCKED", "PENDING", "QUEUED", "WAITING_FOR_RETRY"}
_RUNNING_STATES = {"RUNNING", "TERMINATING"}
_SUCCESS_STATES = {"SUCCESS", "SUCCEEDED"}
_CANCELLED_STATES = {"CANCELED", "CANCELLED", "TIMEDOUT", "TIMEOUT"}
_FAILED_STATES = {"FAILED", "INTERNAL_ERROR", "SKIPPED", "UPSTREAM_FAILED"}


class DatabricksJobsBackend:
    name = "databricks"

    def __init__(
        self,
        host: str | None,
        *,
        token: str | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
        timeout_seconds: float = 30.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.host = host.rstrip("/") if host else None
        self.token = token
        self.client_id = client_id
        self.client_secret = client_secret
        self.timeout_seconds = timeout_seconds
        self._client = client
        self._owns_client = client is None
        self._oauth_access_token: str | None = None
        self._oauth_expires_at = 0.0

    @property
    def configured(self) -> bool:
        return bool(
            self.host
            and (self.token or (self.client_id and self.client_secret))
        )

    def validate_spec(self, specification: dict[str, Any]) -> None:
        unexpected = sorted(set(specification) - {"job_id", "job_parameters"})
        if unexpected:
            raise ValueError(f"Unsupported Databricks job fields: {', '.join(unexpected)}")
        job_id = specification.get("job_id")
        if not isinstance(job_id, int) or isinstance(job_id, bool) or job_id <= 0:
            raise ValueError("Databricks external jobs require a positive integer job_id")
        parameters = specification.get("job_parameters", {})
        if not isinstance(parameters, dict) or not all(
            isinstance(key, str) and isinstance(value, str) for key, value in parameters.items()
        ):
            raise ValueError("job_parameters must be an object containing string keys and values")

    async def submit(
        self,
        specification: dict[str, Any],
        idempotency_key: str,
    ) -> ExternalRunSubmission:
        self.validate_spec(specification)
        payload: dict[str, Any] = {
            "job_id": specification["job_id"],
            "idempotency_token": idempotency_key[:64],
        }
        if specification.get("job_parameters"):
            payload["job_parameters"] = specification["job_parameters"]
        response = await self._request("POST", "/api/2.2/jobs/run-now", json=payload)
        external_run_id = str(response["run_id"])
        return ExternalRunSubmission(
            external_run_id=external_run_id,
            status="queued",
            external_state="QUEUED",
            external_url=response.get("run_page_url"),
            metadata={"number_in_job": response.get("number_in_job")},
        )

    async def get_status(self, external_run_id: str) -> ExternalRunSnapshot:
        response = await self._request(
            "GET",
            "/api/2.2/jobs/runs/get",
            params={"run_id": external_run_id},
        )
        state = response.get("status") or response.get("state") or {}
        lifecycle = str(state.get("state") or state.get("life_cycle_state") or "UNKNOWN").upper()
        termination = state.get("termination_details") or {}
        result = str(
            state.get("result_state")
            or termination.get("code")
            or termination.get("type")
            or ""
        ).upper()
        status = self._map_status(lifecycle, result)
        state_label = f"{lifecycle}:{result}" if result else lifecycle
        error: dict[str, Any] = {}
        if status in {"failed", "cancelled"}:
            error = {
                "code": "external_run_cancelled" if status == "cancelled" else "external_run_failed",
                "message": (
                    state.get("state_message")
                    or termination.get("message")
                    or f"Databricks run ended in {state_label}"
                ),
            }
        output = {
            "job_id": response.get("job_id"),
            "run_name": response.get("run_name"),
            "start_time": response.get("start_time"),
            "end_time": response.get("end_time"),
            "run_duration": response.get("run_duration"),
        }
        return ExternalRunSnapshot(
            external_run_id=external_run_id,
            status=status,
            external_state=state_label,
            external_url=response.get("run_page_url"),
            output={key: value for key, value in output.items() if value is not None},
            error=error,
        )

    async def cancel(self, external_run_id: str) -> None:
        await self._request(
            "POST",
            "/api/2.2/jobs/runs/cancel",
            json={"run_id": int(external_run_id)},
        )

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        if not self.host:
            raise ExternalBackendError(
                "Databricks is not configured: set DEVPLATFORM_DATABRICKS_HOST",
                code="external_backend_not_configured",
                retryable=False,
            )
        access_token = await self._get_access_token()
        try:
            response = await self._http_client().request(
                method,
                f"{self.host}{path}",
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=self.timeout_seconds,
                **kwargs,
            )
        except httpx.HTTPError as exc:
            raise ExternalBackendError(
                f"Databricks request failed: {exc}",
                code="databricks_transport_error",
                retryable=True,
            ) from exc
        if response.is_error:
            try:
                detail = response.json()
                message = detail.get("message") or detail.get("error_code") or response.text
            except ValueError:
                message = response.text
            raise ExternalBackendError(
                f"Databricks API returned {response.status_code}: {message}",
                code="databricks_api_error",
                retryable=response.status_code in {408, 409, 429} or response.status_code >= 500,
            )
        try:
            return response.json() if response.content else {}
        except ValueError as exc:
            raise ExternalBackendError(
                "Databricks API returned an invalid JSON response",
                code="databricks_invalid_response",
                retryable=True,
            ) from exc

    async def _get_access_token(self) -> str:
        if self.token:
            return self.token
        if not self.host or not self.client_id or not self.client_secret:
            raise ExternalBackendError(
                "Databricks is not configured: provide a token or OAuth service-principal credentials",
                code="external_backend_not_configured",
                retryable=False,
            )
        if self._oauth_access_token and monotonic() < self._oauth_expires_at:
            return self._oauth_access_token
        try:
            response = await self._http_client().post(
                f"{self.host}/oidc/v1/token",
                auth=httpx.BasicAuth(self.client_id, self.client_secret),
                data={"grant_type": "client_credentials", "scope": "all-apis"},
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
            token = str(payload["access_token"])
            expires_in = max(int(payload.get("expires_in", 3600)), 60)
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise ExternalBackendError(
                f"Databricks OAuth token request failed: {exc}",
                code="databricks_authentication_failed",
                retryable=True,
            ) from exc
        self._oauth_access_token = token
        self._oauth_expires_at = monotonic() + expires_in - 30
        return token

    def _http_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout_seconds)
        return self._client

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    @staticmethod
    def _map_status(lifecycle: str, result: str) -> str:
        if result in _SUCCESS_STATES:
            return "completed"
        if result in _CANCELLED_STATES or lifecycle in _CANCELLED_STATES:
            return "cancelled"
        if result in _FAILED_STATES or lifecycle in _FAILED_STATES:
            return "failed"
        if lifecycle == "TERMINATED":
            return "failed"
        if lifecycle in _QUEUED_STATES:
            return "queued"
        if lifecycle in _RUNNING_STATES:
            return "running"
        return "running"
