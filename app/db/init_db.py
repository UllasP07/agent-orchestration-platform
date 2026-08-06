import hashlib

from sqlalchemy import inspect, text

from app.db.base import Base
from app.db.models import (
    APIKeyModel,
    AgentModel,
    AgentRunModel,
    AppModel,
    PlatformEventModel,
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
    "agent_runs": {"app_id": "VARCHAR"},
    "platform_events": {"app_id": "VARCHAR"},
}


def _migrate_sqlite_columns() -> None:
    """Apply the additive v0.3 migration while preserving existing demo data."""
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
                "UPDATE api_keys SET key_prefix = substr(api_key, 1, 10) "
                "WHERE key_prefix IS NULL"
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
