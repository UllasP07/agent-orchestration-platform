import hashlib

from sqlalchemy import inspect, text

from app.db.base import Base
from app.db.models import (
    APIKeyModel,
    AgentModel,
    AgentRunModel,
    AppModel,
    ExternalExecutionModel,
    PlatformEventModel,
    RunStepModel,
    WorkflowModel,
    WorkflowRunModel,
)
from app.db.session import engine


SQLITE_COLUMN_MIGRATIONS = {
    "api_keys": {
        "key_prefix": "VARCHAR",
        "last_used_at": "DATETIME",
        "revoked_at": "DATETIME",
    },
    "agent_runs": {
        "app_id": "VARCHAR",
        "error_json": "JSON",
        "idempotency_key": "VARCHAR",
        "attempt": "INTEGER",
        "max_attempts": "INTEGER",
        "available_at": "DATETIME",
        "worker_id": "VARCHAR",
        "claimed_at": "DATETIME",
        "heartbeat_at": "DATETIME",
        "cancel_requested_at": "DATETIME",
        "started_at": "DATETIME",
        "completed_at": "DATETIME",
    },
    "workflow_runs": {
        "error_json": "JSON",
        "idempotency_key": "VARCHAR",
        "attempt": "INTEGER",
        "max_attempts": "INTEGER",
        "available_at": "DATETIME",
        "worker_id": "VARCHAR",
        "claimed_at": "DATETIME",
        "heartbeat_at": "DATETIME",
        "cancel_requested_at": "DATETIME",
        "started_at": "DATETIME",
        "completed_at": "DATETIME",
    },
    "platform_events": {"app_id": "VARCHAR"},
}


def _migrate_sqlite_columns() -> None:
    """Apply additive SQLite migrations while preserving existing platform data."""
    if engine.dialect.name != "sqlite":
        return

    inspector = inspect(engine)
    with engine.begin() as connection:
        for table_name, columns in SQLITE_COLUMN_MIGRATIONS.items():
            if not inspector.has_table(table_name):
                continue
            existing = {column["name"] for column in inspector.get_columns(table_name)}
            for column_name, column_type in columns.items():
                if column_name not in existing:
                    connection.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}"))

        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_agent_runs_app_id ON agent_runs (app_id)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_platform_events_app_id ON platform_events (app_id)"))
        connection.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_runs_app_idempotency "
                "ON agent_runs (app_id, idempotency_key) WHERE idempotency_key IS NOT NULL"
            )
        )
        connection.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_workflow_runs_app_idempotency "
                "ON workflow_runs (app_id, idempotency_key) WHERE idempotency_key IS NOT NULL"
            )
        )
        connection.execute(
            text(
                "UPDATE api_keys SET key_prefix = substr(api_key, 1, 10) "
                "WHERE key_prefix IS NULL"
            )
        )
        for table_name in ("agent_runs", "workflow_runs"):
            connection.execute(
                text(
                    f"UPDATE {table_name} SET error_json = '{{}}' "
                    "WHERE error_json IS NULL"
                )
            )
            connection.execute(
                text(
                    f"UPDATE {table_name} SET attempt = 0, max_attempts = 3, "
                    "available_at = created_at WHERE attempt IS NULL"
                )
            )
            connection.execute(
                text(
                    f"UPDATE {table_name} SET started_at = created_at "
                    "WHERE started_at IS NULL AND status IN ('running', 'completed', 'failed')"
                )
            )
            connection.execute(
                text(
                    f"UPDATE {table_name} SET completed_at = updated_at "
                    "WHERE completed_at IS NULL AND status IN ('completed', 'failed')"
                )
            )
        legacy_keys = connection.execute(
            text("SELECT key_id, api_key FROM api_keys WHERE api_key NOT LIKE 'sha256:%'")
        ).all()
        for key_id, api_key in legacy_keys:
            digest = hashlib.sha256(api_key.encode("utf-8")).hexdigest()
            connection.execute(
                text("UPDATE api_keys SET api_key = :digest WHERE key_id = :key_id"),
                {"digest": f"sha256:{digest}", "key_id": key_id},
            )
        connection.execute(
            text(
                "UPDATE agent_runs SET app_id = json_extract(context_json, '$.app_id') "
                "WHERE app_id IS NULL AND json_valid(context_json)"
            )
        )
        connection.execute(
            text(
                "UPDATE platform_events SET app_id = json_extract(payload_json, '$.app_id') "
                "WHERE app_id IS NULL AND json_valid(payload_json)"
            )
        )
        connection.execute(
            text(
                "UPDATE platform_events SET app_id = ("
                "SELECT agent_runs.app_id FROM agent_runs "
                "WHERE agent_runs.run_id = json_extract(platform_events.payload_json, '$.run_id')"
                ") WHERE app_id IS NULL AND json_valid(payload_json)"
            )
        )


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    _migrate_sqlite_columns()
