from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Literal

from sqlalchemy import asc, desc, func, or_, select, update
from sqlalchemy.exc import IntegrityError

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
from app.db.session import SessionLocal
from app.models.schemas import (
    APIKeyRecord,
    APIKeyWithSecret,
    AgentDefinition,
    AgentRunRecord,
    AppRecord,
    ExternalExecutionRecord,
    PlatformEvent,
    RunStepRecord,
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
        error=row.error_json or {},
        idempotency_key=row.idempotency_key,
        attempt=row.attempt or 0,
        max_attempts=row.max_attempts or 3,
        available_at=row.available_at or row.created_at,
        worker_id=row.worker_id,
        claimed_at=row.claimed_at,
        heartbeat_at=row.heartbeat_at,
        cancel_requested_at=row.cancel_requested_at,
        started_at=row.started_at,
        completed_at=row.completed_at,
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
        error=row.error_json or {},
        idempotency_key=row.idempotency_key,
        attempt=row.attempt or 0,
        max_attempts=row.max_attempts or 3,
        available_at=row.available_at or row.created_at,
        worker_id=row.worker_id,
        claimed_at=row.claimed_at,
        heartbeat_at=row.heartbeat_at,
        cancel_requested_at=row.cancel_requested_at,
        started_at=row.started_at,
        completed_at=row.completed_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _step_from_row(row: RunStepModel) -> RunStepRecord:
    return RunStepRecord(
        step_id=row.step_id,
        run_id=row.run_id,
        run_type=row.run_type,
        app_id=row.app_id,
        step_index=row.step_index,
        name=row.name,
        type=row.type,
        target=row.target,
        status=row.status,
        attempt=row.attempt,
        max_attempts=row.max_attempts,
        timeout_seconds=row.timeout_seconds,
        input=row.input_json or {},
        output=row.output_json or {},
        error=row.error_json or {},
        nested_run_id=row.nested_run_id,
        started_at=row.started_at,
        completed_at=row.completed_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _external_execution_from_row(row: ExternalExecutionModel) -> ExternalExecutionRecord:
    return ExternalExecutionRecord(
        execution_id=row.execution_id,
        step_id=row.step_id,
        run_id=row.run_id,
        app_id=row.app_id,
        provider=row.provider,
        attempt=row.attempt,
        status=row.status,
        external_run_id=row.external_run_id,
        external_state=row.external_state,
        external_url=row.external_url,
        idempotency_key=row.idempotency_key,
        request=row.request_json or {},
        output=row.output_json or {},
        error=row.error_json or {},
        artifacts=row.artifacts_json or [],
        lineage=row.lineage_json or {},
        submitted_at=row.submitted_at,
        last_polled_at=row.last_polled_at,
        completed_at=row.completed_at,
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


def _new_agent_run_row(run: AgentRunRecord) -> AgentRunModel:
    return AgentRunModel(
        run_id=run.run_id,
        app_id=run.app_id,
        agent_id=run.agent_id,
        status=run.status,
        input_json=run.input,
        context_json=run.context,
        output_json=run.output,
        steps_json=run.steps,
        error_json=run.error,
        idempotency_key=run.idempotency_key,
        attempt=run.attempt,
        max_attempts=run.max_attempts,
        available_at=run.available_at,
        worker_id=run.worker_id,
        claimed_at=run.claimed_at,
        heartbeat_at=run.heartbeat_at,
        cancel_requested_at=run.cancel_requested_at,
        started_at=run.started_at,
        completed_at=run.completed_at,
        created_at=run.created_at,
        updated_at=run.updated_at,
    )


def enqueue_run_record(run: AgentRunRecord) -> tuple[AgentRunRecord, bool]:
    """Insert a queued run, returning an existing idempotent run on conflict."""
    with SessionLocal() as session:
        session.add(_new_agent_run_row(run))
        try:
            session.commit()
            return run, True
        except IntegrityError:
            session.rollback()
            if not run.idempotency_key:
                raise
            row = session.execute(
                select(AgentRunModel).where(
                    AgentRunModel.app_id == run.app_id,
                    AgentRunModel.idempotency_key == run.idempotency_key,
                )
            ).scalar_one()
            return _run_from_row(row), False


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
            row.error_json = run.error
            row.idempotency_key = run.idempotency_key
            row.attempt = run.attempt
            row.max_attempts = run.max_attempts
            row.available_at = run.available_at
            row.worker_id = run.worker_id
            row.claimed_at = run.claimed_at
            row.heartbeat_at = run.heartbeat_at
            row.cancel_requested_at = run.cancel_requested_at
            row.started_at = run.started_at
            row.completed_at = run.completed_at
            row.updated_at = run.updated_at
        else:
            session.add(_new_agent_run_row(run))
        session.commit()
    return run


def get_run_record(run_id: str, app_id: str) -> AgentRunRecord | None:
    with SessionLocal() as session:
        row = session.execute(
            select(AgentRunModel).where(AgentRunModel.run_id == run_id, AgentRunModel.app_id == app_id)
        ).scalar_one_or_none()
        return _run_from_row(row) if row else None


def get_run_record_unscoped(run_id: str) -> AgentRunRecord | None:
    with SessionLocal() as session:
        row = session.get(AgentRunModel, run_id)
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


def _new_workflow_run_row(run: WorkflowRunRecord) -> WorkflowRunModel:
    return WorkflowRunModel(
        run_id=run.run_id,
        workflow_id=run.workflow_id,
        app_id=run.app_id,
        status=run.status,
        input_json=run.input,
        context_json=run.context,
        output_json=run.output,
        steps_json=run.steps,
        error_json=run.error,
        idempotency_key=run.idempotency_key,
        attempt=run.attempt,
        max_attempts=run.max_attempts,
        available_at=run.available_at,
        worker_id=run.worker_id,
        claimed_at=run.claimed_at,
        heartbeat_at=run.heartbeat_at,
        cancel_requested_at=run.cancel_requested_at,
        started_at=run.started_at,
        completed_at=run.completed_at,
        created_at=run.created_at,
        updated_at=run.updated_at,
    )


def enqueue_workflow_run_record(run: WorkflowRunRecord) -> tuple[WorkflowRunRecord, bool]:
    with SessionLocal() as session:
        session.add(_new_workflow_run_row(run))
        try:
            session.commit()
            return run, True
        except IntegrityError:
            session.rollback()
            if not run.idempotency_key:
                raise
            row = session.execute(
                select(WorkflowRunModel).where(
                    WorkflowRunModel.app_id == run.app_id,
                    WorkflowRunModel.idempotency_key == run.idempotency_key,
                )
            ).scalar_one()
            return _workflow_run_from_row(row), False


def save_workflow_run_record(run: WorkflowRunRecord) -> WorkflowRunRecord:
    with SessionLocal() as session:
        row = session.get(WorkflowRunModel, run.run_id)
        if row:
            row.status = run.status
            row.output_json = run.output
            row.steps_json = run.steps
            row.error_json = run.error
            row.idempotency_key = run.idempotency_key
            row.attempt = run.attempt
            row.max_attempts = run.max_attempts
            row.available_at = run.available_at
            row.worker_id = run.worker_id
            row.claimed_at = run.claimed_at
            row.heartbeat_at = run.heartbeat_at
            row.cancel_requested_at = run.cancel_requested_at
            row.started_at = run.started_at
            row.completed_at = run.completed_at
            row.updated_at = run.updated_at
        else:
            session.add(_new_workflow_run_row(run))
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


def get_workflow_run_record_unscoped(run_id: str) -> WorkflowRunRecord | None:
    with SessionLocal() as session:
        row = session.get(WorkflowRunModel, run_id)
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


WorkKind = Literal["agent", "workflow"]
TERMINAL_RUN_STATUSES = {"cancelled", "completed", "failed"}
RUNNABLE_STATUSES = {"queued", "retrying"}


def _claim_agent_in_session(session, run_id: str, worker_id: str, now: datetime) -> AgentRunRecord | None:
    result = session.execute(
        update(AgentRunModel)
        .where(
            AgentRunModel.run_id == run_id,
            AgentRunModel.status.in_(RUNNABLE_STATUSES),
            or_(AgentRunModel.available_at.is_(None), AgentRunModel.available_at <= now),
        )
        .values(
            status="running",
            worker_id=worker_id,
            claimed_at=now,
            heartbeat_at=now,
            started_at=func.coalesce(AgentRunModel.started_at, now),
            attempt=func.coalesce(AgentRunModel.attempt, 0) + 1,
            updated_at=now,
        )
    )
    if result.rowcount != 1:
        session.rollback()
        return None
    session.commit()
    return _run_from_row(session.get(AgentRunModel, run_id))


def _claim_workflow_in_session(session, run_id: str, worker_id: str, now: datetime) -> WorkflowRunRecord | None:
    result = session.execute(
        update(WorkflowRunModel)
        .where(
            WorkflowRunModel.run_id == run_id,
            WorkflowRunModel.status.in_(RUNNABLE_STATUSES),
            or_(WorkflowRunModel.available_at.is_(None), WorkflowRunModel.available_at <= now),
        )
        .values(
            status="running",
            worker_id=worker_id,
            claimed_at=now,
            heartbeat_at=now,
            started_at=func.coalesce(WorkflowRunModel.started_at, now),
            attempt=func.coalesce(WorkflowRunModel.attempt, 0) + 1,
            updated_at=now,
        )
    )
    if result.rowcount != 1:
        session.rollback()
        return None
    session.commit()
    return _workflow_run_from_row(session.get(WorkflowRunModel, run_id))


def claim_agent_run(run_id: str, worker_id: str) -> AgentRunRecord | None:
    with SessionLocal() as session:
        return _claim_agent_in_session(session, run_id, worker_id, _now_utc())


def claim_workflow_run(run_id: str, worker_id: str) -> WorkflowRunRecord | None:
    with SessionLocal() as session:
        return _claim_workflow_in_session(session, run_id, worker_id, _now_utc())


def claim_next_run(worker_id: str) -> tuple[WorkKind, AgentRunRecord | WorkflowRunRecord] | None:
    """Claim the oldest available run with a compare-and-set status update."""
    now = _now_utc()
    with SessionLocal() as session:
        agent_candidate = session.execute(
            select(AgentRunModel.run_id, AgentRunModel.created_at)
            .where(
                AgentRunModel.status.in_(RUNNABLE_STATUSES),
                or_(AgentRunModel.available_at.is_(None), AgentRunModel.available_at <= now),
            )
            .order_by(asc(AgentRunModel.created_at))
            .limit(1)
        ).first()
        workflow_candidate = session.execute(
            select(WorkflowRunModel.run_id, WorkflowRunModel.created_at)
            .where(
                WorkflowRunModel.status.in_(RUNNABLE_STATUSES),
                or_(WorkflowRunModel.available_at.is_(None), WorkflowRunModel.available_at <= now),
            )
            .order_by(asc(WorkflowRunModel.created_at))
            .limit(1)
        ).first()

        if not agent_candidate and not workflow_candidate:
            return None
        if agent_candidate and (
            not workflow_candidate or agent_candidate.created_at <= workflow_candidate.created_at
        ):
            claimed = _claim_agent_in_session(session, agent_candidate.run_id, worker_id, now)
            return ("agent", claimed) if claimed else None
        claimed = _claim_workflow_in_session(session, workflow_candidate.run_id, worker_id, now)
        return ("workflow", claimed) if claimed else None


def heartbeat_run(run_type: WorkKind, run_id: str, worker_id: str) -> bool:
    model = AgentRunModel if run_type == "agent" else WorkflowRunModel
    now = _now_utc()
    with SessionLocal() as session:
        result = session.execute(
            update(model)
            .where(
                model.run_id == run_id,
                model.worker_id == worker_id,
                model.status.in_({"running", "cancelling"}),
            )
            .values(heartbeat_at=now, updated_at=now)
        )
        session.commit()
        return result.rowcount == 1


def run_cancel_requested(run_type: WorkKind, run_id: str) -> bool:
    model = AgentRunModel if run_type == "agent" else WorkflowRunModel
    with SessionLocal() as session:
        row = session.get(model, run_id)
        return bool(row and (row.cancel_requested_at is not None or row.status in {"cancelling", "cancelled"}))


def _request_cancel(model, converter, run_id: str, app_id: str):
    now = _now_utc()
    with SessionLocal() as session:
        row = session.execute(
            select(model).where(model.run_id == run_id, model.app_id == app_id)
        ).scalar_one_or_none()
        if not row:
            return None
        if row.status not in TERMINAL_RUN_STATUSES:
            row.cancel_requested_at = now
            row.updated_at = now
            if row.status in RUNNABLE_STATUSES:
                row.status = "cancelled"
                row.completed_at = now
            else:
                row.status = "cancelling"
            session.commit()
        return converter(row)


def request_agent_run_cancel(run_id: str, app_id: str) -> AgentRunRecord | None:
    return _request_cancel(AgentRunModel, _run_from_row, run_id, app_id)


def request_workflow_run_cancel(run_id: str, app_id: str) -> WorkflowRunRecord | None:
    return _request_cancel(WorkflowRunModel, _workflow_run_from_row, run_id, app_id)


def requeue_or_fail_run(
    run_type: WorkKind,
    run_id: str,
    error: dict,
    retry_delay_seconds: float,
) -> AgentRunRecord | WorkflowRunRecord | None:
    model = AgentRunModel if run_type == "agent" else WorkflowRunModel
    converter = _run_from_row if run_type == "agent" else _workflow_run_from_row
    now = _now_utc()
    with SessionLocal() as session:
        row = session.get(model, run_id)
        if not row:
            return None
        row.error_json = error
        row.worker_id = None
        row.claimed_at = None
        row.heartbeat_at = None
        row.updated_at = now
        if row.cancel_requested_at is not None:
            row.status = "cancelled"
            row.completed_at = now
        elif (row.attempt or 0) < (row.max_attempts or 1):
            row.status = "retrying"
            row.available_at = now + timedelta(seconds=retry_delay_seconds)
        else:
            row.status = "failed"
            row.completed_at = now
        session.commit()
        return converter(row)


def recover_stale_runs(lease_timeout_seconds: float) -> int:
    """Release expired worker leases and make interrupted steps resumable."""
    now = _now_utc()
    cutoff = now - timedelta(seconds=lease_timeout_seconds)
    recovered_ids: list[tuple[WorkKind, str, bool]] = []
    with SessionLocal() as session:
        for run_type, model in (("agent", AgentRunModel), ("workflow", WorkflowRunModel)):
            rows = session.execute(
                select(model).where(
                    model.status.in_({"running", "cancelling"}),
                    or_(model.heartbeat_at.is_(None), model.heartbeat_at < cutoff),
                )
            ).scalars().all()
            for row in rows:
                was_cancelled = row.cancel_requested_at is not None or row.status == "cancelling"
                if was_cancelled:
                    row.status = "cancelled"
                    row.completed_at = now
                else:
                    row.status = "queued"
                    row.available_at = now
                row.worker_id = None
                row.claimed_at = None
                row.heartbeat_at = None
                row.updated_at = now
                recovered_ids.append((run_type, row.run_id, was_cancelled))

        for run_type, run_id, was_cancelled in recovered_ids:
            steps = session.execute(
                select(RunStepModel).where(
                    RunStepModel.run_type == run_type,
                    RunStepModel.run_id == run_id,
                    RunStepModel.status.in_({"running", "retrying"}),
                )
            ).scalars().all()
            for step in steps:
                step.status = "cancelled" if was_cancelled else "pending"
                step.completed_at = now if was_cancelled else None
                step.error_json = {
                    "code": "run_cancelled" if was_cancelled else "worker_lease_expired",
                    "message": "Run cancellation requested" if was_cancelled else "Worker lease expired",
                }
                step.updated_at = now
        session.commit()
    return len(recovered_ids)


def get_or_create_run_step(step: RunStepRecord) -> RunStepRecord:
    with SessionLocal() as session:
        existing = session.execute(
            select(RunStepModel).where(
                RunStepModel.run_type == step.run_type,
                RunStepModel.run_id == step.run_id,
                RunStepModel.step_index == step.step_index,
            )
        ).scalar_one_or_none()
        if existing:
            return _step_from_row(existing)
        session.add(
            RunStepModel(
                step_id=step.step_id,
                run_id=step.run_id,
                run_type=step.run_type,
                app_id=step.app_id,
                step_index=step.step_index,
                name=step.name,
                type=step.type,
                target=step.target,
                status=step.status,
                attempt=step.attempt,
                max_attempts=step.max_attempts,
                timeout_seconds=step.timeout_seconds,
                input_json=step.input,
                output_json=step.output,
                error_json=step.error,
                nested_run_id=step.nested_run_id,
                started_at=step.started_at,
                completed_at=step.completed_at,
                created_at=step.created_at,
                updated_at=step.updated_at,
            )
        )
        try:
            session.commit()
            return step
        except IntegrityError:
            session.rollback()
            row = session.execute(
                select(RunStepModel).where(
                    RunStepModel.run_type == step.run_type,
                    RunStepModel.run_id == step.run_id,
                    RunStepModel.step_index == step.step_index,
                )
            ).scalar_one()
            return _step_from_row(row)


def save_run_step(step: RunStepRecord) -> RunStepRecord:
    with SessionLocal() as session:
        row = session.get(RunStepModel, step.step_id)
        if not row:
            raise KeyError(f"Unknown step_id: {step.step_id}")
        row.status = step.status
        row.attempt = step.attempt
        row.max_attempts = step.max_attempts
        row.timeout_seconds = step.timeout_seconds
        row.input_json = step.input
        row.output_json = step.output
        row.error_json = step.error
        row.nested_run_id = step.nested_run_id
        row.started_at = step.started_at
        row.completed_at = step.completed_at
        row.updated_at = step.updated_at
        session.commit()
    return step


def list_run_step_records(
    run_type: WorkKind,
    run_id: str,
    app_id: str | None = None,
) -> list[RunStepRecord]:
    with SessionLocal() as session:
        statement = select(RunStepModel).where(
            RunStepModel.run_type == run_type,
            RunStepModel.run_id == run_id,
        )
        if app_id:
            statement = statement.where(RunStepModel.app_id == app_id)
        rows = session.execute(statement.order_by(asc(RunStepModel.step_index))).scalars().all()
        return [_step_from_row(row) for row in rows]


def get_or_create_external_execution(
    execution: ExternalExecutionRecord,
) -> ExternalExecutionRecord:
    with SessionLocal() as session:
        existing = session.execute(
            select(ExternalExecutionModel).where(
                ExternalExecutionModel.step_id == execution.step_id,
                ExternalExecutionModel.attempt == execution.attempt,
            )
        ).scalar_one_or_none()
        if existing:
            return _external_execution_from_row(existing)

        session.add(
            ExternalExecutionModel(
                execution_id=execution.execution_id,
                step_id=execution.step_id,
                run_id=execution.run_id,
                app_id=execution.app_id,
                provider=execution.provider,
                attempt=execution.attempt,
                status=execution.status,
                external_run_id=execution.external_run_id,
                external_state=execution.external_state,
                external_url=execution.external_url,
                idempotency_key=execution.idempotency_key,
                request_json=execution.request,
                output_json=execution.output,
                error_json=execution.error,
                artifacts_json=[artifact.model_dump(mode="json", by_alias=True) for artifact in execution.artifacts],
                lineage_json=execution.lineage.model_dump(mode="json", by_alias=True),
                submitted_at=execution.submitted_at,
                last_polled_at=execution.last_polled_at,
                completed_at=execution.completed_at,
                created_at=execution.created_at,
                updated_at=execution.updated_at,
            )
        )
        try:
            session.commit()
            return execution
        except IntegrityError:
            session.rollback()
            row = session.execute(
                select(ExternalExecutionModel).where(
                    ExternalExecutionModel.step_id == execution.step_id,
                    ExternalExecutionModel.attempt == execution.attempt,
                )
            ).scalar_one()
            return _external_execution_from_row(row)


def save_external_execution(execution: ExternalExecutionRecord) -> ExternalExecutionRecord:
    with SessionLocal() as session:
        row = session.get(ExternalExecutionModel, execution.execution_id)
        if not row:
            raise KeyError(f"Unknown external execution_id: {execution.execution_id}")
        row.status = execution.status
        row.external_run_id = execution.external_run_id
        row.external_state = execution.external_state
        row.external_url = execution.external_url
        row.request_json = execution.request
        row.output_json = execution.output
        row.error_json = execution.error
        row.artifacts_json = [
            artifact.model_dump(mode="json", by_alias=True) for artifact in execution.artifacts
        ]
        row.lineage_json = execution.lineage.model_dump(mode="json", by_alias=True)
        row.submitted_at = execution.submitted_at
        row.last_polled_at = execution.last_polled_at
        row.completed_at = execution.completed_at
        row.updated_at = execution.updated_at
        session.commit()
    return execution


def get_external_execution_record(
    execution_id: str,
    app_id: str | None = None,
) -> ExternalExecutionRecord | None:
    with SessionLocal() as session:
        statement = select(ExternalExecutionModel).where(
            ExternalExecutionModel.execution_id == execution_id
        )
        if app_id:
            statement = statement.where(ExternalExecutionModel.app_id == app_id)
        row = session.execute(statement).scalar_one_or_none()
        return _external_execution_from_row(row) if row else None


def get_latest_external_execution_for_step(step_id: str) -> ExternalExecutionRecord | None:
    with SessionLocal() as session:
        row = session.execute(
            select(ExternalExecutionModel)
            .where(ExternalExecutionModel.step_id == step_id)
            .order_by(desc(ExternalExecutionModel.attempt), desc(ExternalExecutionModel.created_at))
            .limit(1)
        ).scalar_one_or_none()
        return _external_execution_from_row(row) if row else None


def list_external_execution_records(
    run_id: str,
    app_id: str,
) -> list[ExternalExecutionRecord]:
    with SessionLocal() as session:
        rows = session.execute(
            select(ExternalExecutionModel)
            .where(
                ExternalExecutionModel.run_id == run_id,
                ExternalExecutionModel.app_id == app_id,
            )
            .order_by(asc(ExternalExecutionModel.created_at), asc(ExternalExecutionModel.attempt))
        ).scalars().all()
        return [_external_execution_from_row(row) for row in rows]


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
