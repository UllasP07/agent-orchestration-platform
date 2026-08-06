from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timezone

from sqlalchemy import desc, or_, select

from app.db.models import (
    APIKeyModel,
    AgentModel,
    AgentRunModel,
    AppModel,
    PlatformEventModel,
    WorkflowModel,
    WorkflowRunModel,
)
from app.db.session import SessionLocal
from app.models.schemas import (
    APIKeyRecord,
    APIKeyWithSecret,
    AgentDefinition,
    AgentRunRecord,
    AppRecord,
    PlatformEvent,
    WorkflowDefinition,
    WorkflowRunRecord,
    WorkflowStep,
)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _hash_api_key(api_key: str) -> str:
    return f"sha256:{hashlib.sha256(api_key.encode('utf-8')).hexdigest()}"


def _api_key_from_row(row: APIKeyModel) -> APIKeyRecord:
    return APIKeyRecord(
        key_id=row.key_id,
        app_id=row.app_id,
        key_prefix=row.key_prefix or "legacy",
        name=row.name,
        created_at=row.created_at,
        last_used_at=row.last_used_at,
        revoked_at=row.revoked_at,
    )


def _agent_from_row(row: AgentModel) -> AgentDefinition:
    return AgentDefinition(
        id=row.id,
        app_id=row.app_id,
        name=row.name,
        description=row.description,
        system_prompt=row.system_prompt,
        capabilities=row.capabilities_json or [],
        tools=row.tools_json or [],
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _run_from_row(row: AgentRunModel) -> AgentRunRecord:
    return AgentRunRecord(
        run_id=row.run_id,
        app_id=row.app_id or (row.context_json or {}).get("app_id", "legacy"),
        agent_id=row.agent_id,
        status=row.status,
        input=row.input_json or {},
        context=row.context_json or {},
        output=row.output_json or {},
        steps=row.steps_json or [],
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _workflow_from_row(row: WorkflowModel) -> WorkflowDefinition:
    return WorkflowDefinition(
        id=row.id,
        app_id=row.app_id,
        name=row.name,
        description=row.description,
        steps=[WorkflowStep.model_validate(step) for step in (row.steps_json or [])],
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _workflow_run_from_row(row: WorkflowRunModel) -> WorkflowRunRecord:
    return WorkflowRunRecord(
        run_id=row.run_id,
        workflow_id=row.workflow_id,
        app_id=row.app_id,
        status=row.status,
        input=row.input_json or {},
        context=row.context_json or {},
        output=row.output_json or {},
        steps=row.steps_json or [],
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def create_app_record(app: AppRecord) -> AppRecord:
    with SessionLocal() as session:
        session.add(
            AppModel(
                id=app.id,
                name=app.name,
                owner=app.owner,
                metadata_json=app.metadata,
                created_at=app.created_at,
            )
        )
        session.commit()
    return app


def get_app_record(app_id: str) -> AppRecord | None:
    with SessionLocal() as session:
        row = session.get(AppModel, app_id)
        if not row:
            return None
        return AppRecord(
            id=row.id,
            name=row.name,
            owner=row.owner,
            metadata=row.metadata_json or {},
            created_at=row.created_at,
        )


def create_api_key(app_id: str, name: str = "default") -> APIKeyWithSecret:
    secret = f"dp_{secrets.token_urlsafe(32)}"
    record = APIKeyRecord(app_id=app_id, key_prefix=secret[:10], name=name)
    with SessionLocal() as session:
        session.add(
            APIKeyModel(
                key_id=record.key_id,
                app_id=record.app_id,
                api_key=_hash_api_key(secret),
                key_prefix=record.key_prefix,
                name=record.name,
                created_at=record.created_at,
            )
        )
        session.commit()
    return APIKeyWithSecret(key=record, api_key=secret)


def get_api_key_record(api_key: str) -> APIKeyRecord | None:
    digest = _hash_api_key(api_key)
    with SessionLocal() as session:
        row = session.execute(
            select(APIKeyModel).where(or_(APIKeyModel.api_key == digest, APIKeyModel.api_key == api_key))
        ).scalar_one_or_none()
        if not row or row.revoked_at is not None:
            return None

        # Compatibility fallback for a legacy database not yet run through the
        # additive SQLite startup migration.
        if row.api_key == api_key:
            row.api_key = digest
            row.key_prefix = api_key[:10]
        row.last_used_at = _now_utc()
        session.commit()
        return _api_key_from_row(row)


def list_api_key_records(app_id: str) -> list[APIKeyRecord]:
    with SessionLocal() as session:
        rows = session.execute(
            select(APIKeyModel).where(APIKeyModel.app_id == app_id).order_by(desc(APIKeyModel.created_at))
        ).scalars().all()
        return [_api_key_from_row(row) for row in rows]


def revoke_api_key(key_id: str, app_id: str) -> bool:
    with SessionLocal() as session:
        row = session.execute(
            select(APIKeyModel).where(APIKeyModel.key_id == key_id, APIKeyModel.app_id == app_id)
        ).scalar_one_or_none()
        if not row:
            return False
        if row.revoked_at is None:
            row.revoked_at = _now_utc()
            session.commit()
        return True


def upsert_agent_record(agent: AgentDefinition) -> AgentDefinition:
    with SessionLocal() as session:
        row = session.get(AgentModel, agent.id)
        if row:
            row.app_id = agent.app_id
            row.name = agent.name
            row.description = agent.description
            row.system_prompt = agent.system_prompt
            row.capabilities_json = agent.capabilities
            row.tools_json = agent.tools
            row.updated_at = agent.updated_at
        else:
            session.add(
                AgentModel(
                    id=agent.id,
                    app_id=agent.app_id,
                    name=agent.name,
                    description=agent.description,
                    system_prompt=agent.system_prompt,
                    capabilities_json=agent.capabilities,
                    tools_json=agent.tools,
                    created_at=agent.created_at,
                    updated_at=agent.updated_at,
                )
            )
        session.commit()
    return agent


def list_agent_records(app_id: str) -> list[AgentDefinition]:
    with SessionLocal() as session:
        rows = session.execute(
            select(AgentModel)
            .where(or_(AgentModel.app_id.is_(None), AgentModel.app_id == app_id))
            .order_by(AgentModel.app_id.desc(), AgentModel.name)
        ).scalars().all()
        return [_agent_from_row(row) for row in rows]


def get_agent_record(agent_id: str, app_id: str) -> AgentDefinition | None:
    with SessionLocal() as session:
        row = session.execute(
            select(AgentModel).where(
                AgentModel.id == agent_id,
                or_(AgentModel.app_id.is_(None), AgentModel.app_id == app_id),
            )
        ).scalar_one_or_none()
        return _agent_from_row(row) if row else None


def delete_agent_record(agent_id: str, app_id: str) -> bool:
    with SessionLocal() as session:
        row = session.execute(
            select(AgentModel).where(AgentModel.id == agent_id, AgentModel.app_id == app_id)
        ).scalar_one_or_none()
        if not row:
            return False
        session.delete(row)
        session.commit()
        return True


def workflow_references_agent(agent_id: str, app_id: str) -> bool:
    return any(
        step.type == "agent" and step.target == agent_id
        for workflow in list_workflow_records(app_id)
        for step in workflow.steps
    )


def save_run_record(run: AgentRunRecord) -> AgentRunRecord:
    with SessionLocal() as session:
        row = session.get(AgentRunModel, run.run_id)
        if row:
            row.app_id = run.app_id
            row.agent_id = run.agent_id
            row.status = run.status
            row.input_json = run.input
            row.context_json = run.context
            row.output_json = run.output
            row.steps_json = run.steps
            row.updated_at = run.updated_at
        else:
            session.add(
                AgentRunModel(
                    run_id=run.run_id,
                    app_id=run.app_id,
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


def get_run_record(run_id: str, app_id: str) -> AgentRunRecord | None:
    with SessionLocal() as session:
        row = session.execute(
            select(AgentRunModel).where(AgentRunModel.run_id == run_id, AgentRunModel.app_id == app_id)
        ).scalar_one_or_none()
        return _run_from_row(row) if row else None


def list_run_records(app_id: str, limit: int = 100) -> list[AgentRunRecord]:
    with SessionLocal() as session:
        rows = session.execute(
            select(AgentRunModel)
            .where(AgentRunModel.app_id == app_id)
            .order_by(desc(AgentRunModel.created_at))
            .limit(limit)
        ).scalars().all()
        return [_run_from_row(row) for row in rows]


def create_workflow_record(workflow: WorkflowDefinition) -> WorkflowDefinition:
    with SessionLocal() as session:
        session.add(
            WorkflowModel(
                id=workflow.id,
                app_id=workflow.app_id,
                name=workflow.name,
                description=workflow.description,
                steps_json=[step.model_dump(mode="json") for step in workflow.steps],
                created_at=workflow.created_at,
                updated_at=workflow.updated_at,
            )
        )
        session.commit()
    return workflow


def update_workflow_record(workflow: WorkflowDefinition) -> WorkflowDefinition:
    with SessionLocal() as session:
        row = session.execute(
            select(WorkflowModel).where(
                WorkflowModel.id == workflow.id,
                WorkflowModel.app_id == workflow.app_id,
            )
        ).scalar_one_or_none()
        if not row:
            raise KeyError(f"Unknown workflow_id: {workflow.id}")
        row.name = workflow.name
        row.description = workflow.description
        row.steps_json = [step.model_dump(mode="json") for step in workflow.steps]
        row.updated_at = workflow.updated_at
        session.commit()
    return workflow


def list_workflow_records(app_id: str) -> list[WorkflowDefinition]:
    with SessionLocal() as session:
        rows = session.execute(
            select(WorkflowModel)
            .where(WorkflowModel.app_id == app_id)
            .order_by(desc(WorkflowModel.created_at))
        ).scalars().all()
        return [_workflow_from_row(row) for row in rows]


def get_workflow_record(workflow_id: str, app_id: str) -> WorkflowDefinition | None:
    with SessionLocal() as session:
        row = session.execute(
            select(WorkflowModel).where(WorkflowModel.id == workflow_id, WorkflowModel.app_id == app_id)
        ).scalar_one_or_none()
        return _workflow_from_row(row) if row else None


def delete_workflow_record(workflow_id: str, app_id: str) -> bool:
    with SessionLocal() as session:
        row = session.execute(
            select(WorkflowModel).where(WorkflowModel.id == workflow_id, WorkflowModel.app_id == app_id)
        ).scalar_one_or_none()
        if not row:
            return False
        session.delete(row)
        session.commit()
        return True


def save_workflow_run_record(run: WorkflowRunRecord) -> WorkflowRunRecord:
    with SessionLocal() as session:
        row = session.get(WorkflowRunModel, run.run_id)
        if row:
            row.status = run.status
            row.output_json = run.output
            row.steps_json = run.steps
            row.updated_at = run.updated_at
        else:
            session.add(
                WorkflowRunModel(
                    run_id=run.run_id,
                    workflow_id=run.workflow_id,
                    app_id=run.app_id,
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


def get_workflow_run_record(run_id: str, app_id: str) -> WorkflowRunRecord | None:
    with SessionLocal() as session:
        row = session.execute(
            select(WorkflowRunModel).where(
                WorkflowRunModel.run_id == run_id,
                WorkflowRunModel.app_id == app_id,
            )
        ).scalar_one_or_none()
        return _workflow_run_from_row(row) if row else None


def list_workflow_run_records(app_id: str, limit: int = 100) -> list[WorkflowRunRecord]:
    with SessionLocal() as session:
        rows = session.execute(
            select(WorkflowRunModel)
            .where(WorkflowRunModel.app_id == app_id)
            .order_by(desc(WorkflowRunModel.created_at))
            .limit(limit)
        ).scalars().all()
        return [_workflow_run_from_row(row) for row in rows]


def save_event_record(event: PlatformEvent) -> PlatformEvent:
    with SessionLocal() as session:
        session.add(
            PlatformEventModel(
                id=event.id,
                app_id=event.app_id,
                topic=event.topic,
                payload_json=event.payload,
                source=event.source,
                created_at=event.created_at,
            )
        )
        session.commit()
    return event


def list_event_records(app_id: str, limit: int = 100, topic: str | None = None) -> list[PlatformEvent]:
    with SessionLocal() as session:
        statement = select(PlatformEventModel).where(PlatformEventModel.app_id == app_id)
        if topic:
            statement = statement.where(PlatformEventModel.topic == topic)
        rows = session.execute(
            statement.order_by(desc(PlatformEventModel.created_at)).limit(limit)
        ).scalars().all()
        return [
            PlatformEvent(
                id=row.id,
                app_id=row.app_id or app_id,
                topic=row.topic,
                payload=row.payload_json or {},
                source=row.source,
                created_at=row.created_at,
            )
            for row in rows
        ]
