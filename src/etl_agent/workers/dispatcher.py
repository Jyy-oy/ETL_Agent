"""Transactional Outbox 的受管消费和 Tool Broker 边界。"""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.harness.capability import CapabilityError, ReplayGuard, verify_capability
from etl_agent.infrastructure.models import (
    ExecutionRun,
    ExecutionRunStatus,
    OutboxEvent,
    OutboxEventStatus,
    PipelineVersion,
)
from etl_agent.workers.engine import EngineError, ExecutionEngine


class DispatchError(RuntimeError):
    """Outbox 受管消费失败，错误内容不包含 Capability 或 Secret 原文。"""


@dataclass(frozen=True, slots=True)
class DispatchResult:
    """返回一次 Outbox 消费的稳定结果。"""

    event_id: UUID
    execution_run_id: UUID
    status: OutboxEventStatus
    engine_job_id: str | None = None


def _failure_detail(exc: Exception) -> str:
    """将外部异常压缩为可记录的短文本，避免意外写入敏感载荷。"""
    detail = str(exc).strip().replace("\n", " ")
    return detail[:512] or "受管执行失败"


async def dispatch_outbox_event(
    session: AsyncSession,
    event_id: UUID,
    *,
    engine: ExecutionEngine,
    replay_guard: ReplayGuard,
    public_key: Ed25519PublicKey,
    replay_ttl_seconds: int,
) -> DispatchResult:
    """锁定并消费一个 Outbox 事件，所有外部副作用只从此 Tool Broker 出口发起。"""
    event = await session.scalar(
        select(OutboxEvent).where(OutboxEvent.id == event_id).with_for_update()
    )
    if event is None:
        raise DispatchError("Outbox 事件不存在")
    execution = await session.scalar(
        select(ExecutionRun).where(ExecutionRun.id == event.aggregate_id).with_for_update()
    )
    if execution is None:
        event.status = OutboxEventStatus.FAILED.value
        event.last_error = "ExecutionRun 不存在"
        await session.commit()
        raise DispatchError("Outbox 缺少 ExecutionRun")
    if event.status != OutboxEventStatus.PENDING.value:
        return DispatchResult(
            event_id=event.id,
            execution_run_id=execution.id,
            status=OutboxEventStatus(event.status),
            engine_job_id=execution.engine_job_id,
        )

    event.attempts += 1
    try:
        claims = verify_capability(event.capability_token, public_key)
        if claims.preparation_id != execution.preparation_id:
            raise CapabilityError("Capability Preparation 绑定不一致")
        if claims.artifact_digest != execution.artifact_digest:
            raise CapabilityError("Capability 制品摘要绑定不一致")
        if not await replay_guard.consume_once(event.capability_token, replay_ttl_seconds):
            raise CapabilityError("Capability 已被消费或已重放")
        version = await session.get(PipelineVersion, execution.pipeline_version_id)
        if version is None or not version.hocon:
            raise DispatchError("PipelineVersion 缺少 SeaTunnel HOCON 制品")
        payload = dict(event.payload_json)
        payload["hocon"] = version.hocon
        payload["job_name"] = f"etl-agent-{execution.id}"
        job = await engine.submit(payload)
    except (CapabilityError, DispatchError, EngineError) as exc:
        detail = _failure_detail(exc)
        event.status = OutboxEventStatus.FAILED.value
        event.last_error = detail
        execution.status = ExecutionRunStatus.FAILED.value
        execution.error_code = "OUTBOX_DISPATCH_FAILED"
        execution.error_detail = detail
        await session.commit()
        raise DispatchError(detail) from exc

    now = datetime.now(UTC)
    event.status = OutboxEventStatus.PUBLISHED.value
    event.published_at = now
    execution.engine_job_id = job.job_id
    execution.status = ExecutionRunStatus.RUNNING.value
    execution.started_at = now
    await session.commit()
    return DispatchResult(
        event_id=event.id,
        execution_run_id=execution.id,
        status=OutboxEventStatus.PUBLISHED,
        engine_job_id=job.job_id,
    )
