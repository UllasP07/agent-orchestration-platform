from __future__ import annotations

from sqlalchemy import desc, select

from app.db.models import AgentRunModel, AppModel, PlatformEventModel
from app.db.session import SessionLocal
from app.models.schemas import AgentRunRecord, AppRecord, PlatformEvent


def create_app_record(app: AppRecord) -> AppRecord:
    with SessionLocal() as session:
        row = AppModel(
            id=app.id,
            name=app.name,
            owner=app.owner,
            metadata_json=app.metadata,
            created_at=app.created_at,
        )
        session.add(row)
        session.commit()
    return app


def list_app_records() -> list[AppRecord]:
    with SessionLocal() as session:
        rows = session.execute(select(AppModel).order_by(desc(AppModel.created_at))).scalars().all()
        return [
            AppRecord(
                id=row.id,
                name=row.name,
                owner=row.owner,
                metadata=row.metadata_json,
                created_at=row.created_at,
            )
            for row in rows
        ]


def save_run_record(run: AgentRunRecord) -> AgentRunRecord:
    with SessionLocal() as session:
        existing = session.get(AgentRunModel, run.run_id)
        if existing:
            existing.agent_id = run.agent_id
            existing.status = run.status
            existing.input_json = run.input
            existing.context_json = run.context
            existing.output_json = run.output
            existing.steps_json = run.steps
            existing.updated_at = run.updated_at
        else:
            session.add(
                AgentRunModel(
                    run_id=run.run_id,
                    agent_id=run.agent_id,
                    status=run.status,
                    input_json=run.input,
                    context_json=run.context,
                    output_json=run.output,
                    steps_json=run.steps,
                    created_at=run.created_at,
                    updated_at=run.updated_at,
                )
            )
        session.commit()
    return run


def get_run_record(run_id: str) -> AgentRunRecord | None:
    with SessionLocal() as session:
        row = session.get(AgentRunModel, run_id)
        if not row:
            return None
        return AgentRunRecord(
            run_id=row.run_id,
            agent_id=row.agent_id,
            status=row.status,
            input=row.input_json,
            context=row.context_json,
            output=row.output_json,
            steps=row.steps_json,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


def list_run_records() -> list[AgentRunRecord]:
    with SessionLocal() as session:
        rows = session.execute(select(AgentRunModel).order_by(desc(AgentRunModel.created_at))).scalars().all()
        return [
            AgentRunRecord(
                run_id=row.run_id,
                agent_id=row.agent_id,
                status=row.status,
                input=row.input_json,
                context=row.context_json,
                output=row.output_json,
                steps=row.steps_json,
                created_at=row.created_at,
                updated_at=row.updated_at,
            )
            for row in rows
        ]


def save_event_record(event: PlatformEvent) -> PlatformEvent:
    with SessionLocal() as session:
        session.add(
            PlatformEventModel(
                id=event.id,
                topic=event.topic,
                payload_json=event.payload,
                source=event.source,
                created_at=event.created_at,
            )
        )
        session.commit()
    return event


def list_event_records(limit: int = 100) -> list[PlatformEvent]:
    with SessionLocal() as session:
        rows = session.execute(
            select(PlatformEventModel).order_by(desc(PlatformEventModel.created_at)).limit(limit)
        ).scalars().all()
        return [
            PlatformEvent(
                id=row.id,
                topic=row.topic,
                payload=row.payload_json,
                source=row.source,
                created_at=row.created_at,
            )
            for row in rows
        ]