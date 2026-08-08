from app.core.config import settings
from app.external.backends.databricks import DatabricksJobsBackend
from app.external.backends.fake import FakeExternalBackend
from app.external.registry import external_backends


def register_default_external_backends() -> None:
    external_backends.register(FakeExternalBackend())
    external_backends.register(
        DatabricksJobsBackend(
            settings.databricks_host,
            token=settings.databricks_token.get_secret_value() if settings.databricks_token else None,
            client_id=settings.databricks_client_id,
            client_secret=(
                settings.databricks_client_secret.get_secret_value()
                if settings.databricks_client_secret
                else None
            ),
            timeout_seconds=settings.databricks_request_timeout_seconds,
        )
    )
