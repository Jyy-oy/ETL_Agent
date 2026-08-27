"""Transactional Outbox 的受管消费和 Tool Broker 边界。"""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.harness.capability import CapabilityError, ReplayGuard, verify_capability
from etl_agent.harness.ledger import append_evidence_event
from etl_agent.infrastructure.models import (
    ExecutionRun,
    ExecutionRunStatus,
    OutboxEvent,
    OutboxEventStatus,
    PipelineVersion,
    PublishStatus,
    RollbackStatus,
)
from etl_agent.workers.engine import EngineError, ExecutionEngine
from etl_agent.workers.real_data_plane import RuntimeCompilationError

logger = logging.getLogger(__name__)


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
    runtime_compiler: Callable[
        [AsyncSession, ExecutionRun, PipelineVersion], Awaitable[dict[str, Any]]
    ]
    | None = None,
) -> DispatchResult:
    """锁定并消费一个 Outbox 事件，所有外部副作用只从此 Tool Broker 出口发起。"""
    logger.info("outbox_event_processing event_id=%s", event_id)
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
    if event.event_type == "execution.submit" and execution.status in {
        ExecutionRunStatus.CANCEL_REQUESTED.value,
        ExecutionRunStatus.CANCELLED.value,
    }:
        # 取消先于提交时，不得让迟到的 submit Outbox 重新产生外部作业。
        event.status = OutboxEventStatus.FAILED.value
        event.last_error = "执行已在提交前取消"
        await session.commit()
        return DispatchResult(
            event_id=event.id,
            execution_run_id=execution.id,
            status=OutboxEventStatus.FAILED,
            engine_job_id=None,
        )

    event.attempts += 1
    try:
        claims = verify_capability(event.capability_token, public_key)
        if claims.preparation_id != execution.preparation_id:
            raise CapabilityError("Capability Preparation 绑定不一致")
        if claims.subject != execution.created_by:
            raise CapabilityError("Capability 主体绑定不一致")
        if claims.artifact_digest != execution.artifact_digest:
            raise CapabilityError("Capability 制品摘要绑定不一致")
        expected_tool = {
            "execution.submit": "seatunnel.submit",
            "execution.cancel": "seatunnel.cancel",
            "execution.cleanup": "seatunnel.cleanup",
            "execution.swap": "seatunnel.swap",
            "execution.rollback": "seatunnel.rollback",
        }.get(event.event_type)
        if expected_tool is None or claims.tool != expected_tool:
            raise CapabilityError("Capability 工具绑定不一致")
        if not await replay_guard.consume_once(event.capability_token, replay_ttl_seconds):
            raise CapabilityError("Capability 已被消费或已重放")
        payload = dict(event.payload_json)
        if event.event_type == "execution.submit":
            version = await session.get(PipelineVersion, execution.pipeline_version_id)
            if version is None or not version.hocon:
                raise DispatchError("PipelineVersion 缺少 SeaTunnel HOCON 制品")
            if runtime_compiler is not None:
                payload.update(await runtime_compiler(session, execution, version))
            else:
                payload["hocon"] = version.hocon
            payload["job_name"] = f"etl-agent-{execution.id}"
            payload["idempotency_key"] = execution.idempotency_key
            job = await engine.submit(payload)
        else:
            job_id = execution.engine_job_id or str(payload.get("engine_job_id", ""))
            if not job_id and event.event_type == "execution.cancel":
                # 尚未创建引擎作业时，取消请求本身已经阻断迟到的 submit。
                job = None
            elif not job_id:
                raise DispatchError("执行事实缺少引擎作业 ID")
            elif event.event_type == "execution.cancel":
                if not await engine.cancel(job_id):
                    raise DispatchError("SeaTunnel 作业取消未确认")
            elif event.event_type == "execution.cleanup":
                if not await engine.cleanup(job_id, payload):
                    raise DispatchError("SeaTunnel 中间产物清理未确认")
            elif event.event_type == "execution.swap":
                if not await engine.atomic_swap(job_id, payload):
                    raise DispatchError("目标表原子切换未确认")
            elif event.event_type == "execution.rollback":
                if not await engine.rollback(job_id, payload):
                    raise DispatchError("目标表回滚未确认")
            job = None
    except (CapabilityError, DispatchError, EngineError, RuntimeCompilationError) as exc:
        detail = _failure_detail(exc)
        event.status = OutboxEventStatus.FAILED.value
        event.last_error = detail
        execution.status = ExecutionRunStatus.FAILED.value
        execution.error_code = "OUTBOX_DISPATCH_FAILED"
        execution.error_detail = detail
        await session.commit()
        logger.warning(
            "outbox_event_failed event_id=%s execution_run_id=%s error_code=OUTBOX_DISPATCH_FAILED",
            event.id,
            execution.id,
        )
        raise DispatchError(detail) from exc

    now = datetime.now(UTC)
    event.status = OutboxEventStatus.PUBLISHED.value
    event.published_at = now
    if event.event_type == "execution.submit" and job is not None:
        execution.engine_job_id = job.job_id
        execution.status = ExecutionRunStatus.RUNNING.value
        execution.started_at = now
        safe_runtime_fields = {
            key: payload[key]
            for key in (
                "source_host",
                "source_port",
                "source_secret_ref",
                "source_database",
                "source_table",
                "target_connection_id",
                "target_host",
                "target_port",
                "target_secret_ref",
                "target_database",
                "target_table",
                "shadow_table",
                "error_table",
                "error_query",
                "error_columns",
            )
            if key in payload
        }
        if safe_runtime_fields:
            execution.metrics_json = {**execution.metrics_json, **safe_runtime_fields}
            execution.shadow_table = str(safe_runtime_fields.get("shadow_table", "")) or None
            execution.error_table = str(safe_runtime_fields.get("error_table", "")) or None
    elif event.event_type == "execution.cancel":
        execution.status = ExecutionRunStatus.CANCELLED.value
        execution.completed_at = now
    elif event.event_type == "execution.cleanup":
        execution.publish_status = PublishStatus.CLEANED.value
    elif event.event_type == "execution.swap":
        execution.publish_status = PublishStatus.PUBLISHED.value
        execution.status = ExecutionRunStatus.SUCCEEDED.value
        execution.completed_at = now
    elif event.event_type == "execution.rollback":
        execution.rollback_status = RollbackStatus.COMPLETED.value
        execution.publish_status = PublishStatus.CLEANED.value
        execution.completed_at = execution.completed_at or now
    await append_evidence_event(
        session,
        project_id=execution.project_id,
        event_type=f"{event.event_type}.published",
        resource_type="execution_run",
        resource_id=execution.id,
        actor_id=None,
        correlation_id=execution.correlation_id,
        payload={"event_id": str(event.id), "engine_job_id": execution.engine_job_id},
    )
    await session.commit()
    logger.info(
        "outbox_event_published event_id=%s execution_run_id=%s event_type=%s engine_job_id=%s",
        event.id,
        execution.id,
        event.event_type,
        execution.engine_job_id or "-",
    )
    return DispatchResult(
        event_id=event.id,
        execution_run_id=execution.id,
        status=OutboxEventStatus.PUBLISHED,
        engine_job_id=job.job_id if job is not None else execution.engine_job_id,
    )
