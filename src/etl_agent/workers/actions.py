"""Worker 侧自动发布、清理和取消动作的 Outbox 创建辅助。"""

from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.harness.actions import issue_execution_action_capability
from etl_agent.harness.ledger import append_evidence_event
from etl_agent.infrastructure.models import (
    ExecutionRun,
    OutboxEvent,
    OutboxEventStatus,
    Preparation,
)


async def queue_execution_action(
    session: AsyncSession,
    execution: ExecutionRun,
    *,
    settings,
    event_type: str,
    tool: str,
    payload: dict[str, object],
) -> OutboxEvent | None:
    """为系统自动动作创建单次 Capability Outbox，重复调用保持幂等。"""
    existing = await session.scalar(
        select(OutboxEvent)
        .where(
            OutboxEvent.aggregate_id == execution.id,
            OutboxEvent.event_type == event_type,
            OutboxEvent.status.in_(
                [
                    OutboxEventStatus.PENDING.value,
                    OutboxEventStatus.PUBLISHED.value,
                ]
            ),
        )
        .order_by(OutboxEvent.created_at.desc())
    )
    if existing is not None:
        return None
    failed_existing = await session.scalar(
        select(OutboxEvent)
        .where(
            OutboxEvent.aggregate_id == execution.id,
            OutboxEvent.event_type == event_type,
            OutboxEvent.status == OutboxEventStatus.FAILED.value,
        )
        .limit(1)
    )
    preparation = await session.get(Preparation, execution.preparation_id)
    if preparation is None:
        raise ValueError("Preparation 不存在")
    token = issue_execution_action_capability(
        private_key_path=settings.capability_private_key_path,
        subject=execution.created_by,
        tool=tool,
        environment=str(preparation.facts_json.get("environment", "development")),
        preparation_id=execution.preparation_id,
        artifact_digest=execution.artifact_digest,
        ttl_seconds=settings.capability_ttl_seconds,
    )
    event_id = uuid4()
    event = OutboxEvent(
        id=event_id,
        project_id=execution.project_id,
        aggregate_type="execution_run",
        aggregate_id=execution.id,
        event_type=event_type,
        deduplication_key=(
            f"{event_type}:{execution.id}:{uuid4()}"
            if failed_existing is not None
            else f"{event_type}:{execution.id}"
        ),
        status=OutboxEventStatus.PENDING.value,
        payload_json={
            "schema_version": f"{event_type}.v1",
            "execution_run_id": str(execution.id),
            "preparation_id": str(execution.preparation_id),
            "engine_job_id": execution.engine_job_id,
            **payload,
        },
        capability_token=token,
    )
    session.add(event)
    await append_evidence_event(
        session,
        project_id=execution.project_id,
        event_type=event_type,
        resource_type="execution_run",
        resource_id=execution.id,
        actor_id=execution.created_by,
        correlation_id=execution.correlation_id,
        payload={"event_id": str(event_id), "reason": payload.get("reason", "system")},
    )
    await session.flush()
    return event
