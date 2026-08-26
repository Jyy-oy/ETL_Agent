"""Celery 任务入口：只负责加载依赖并调用 Outbox Tool Broker。"""

import asyncio
from uuid import UUID

import redis.asyncio as redis

from etl_agent.config import get_settings
from etl_agent.harness.capability import RedisReplayGuard, load_public_key
from etl_agent.infrastructure.database import create_session_factory
from etl_agent.infrastructure.models import ExecutionRun, ExecutionRunStatus
from etl_agent.infrastructure.secrets import create_secret_provider
from etl_agent.workers.actions import queue_execution_action
from etl_agent.workers.celery_app import celery_app
from etl_agent.workers.dispatcher import dispatch_outbox_event
from etl_agent.workers.engine import SeaTunnelAdapter
from etl_agent.workers.real_data_plane import (
    DorisTargetAdapter,
    SeaTunnelDorisEngine,
    compile_runtime_job,
)
from etl_agent.workers.supervision import supervise_execution_run


def _engine_for_settings(settings):
    """根据配置创建一次性数据面适配器，避免 Worker 保存连接状态。"""
    seatunnel = SeaTunnelAdapter(
        settings.seatunnel_zeta_endpoint,
        submit_path=settings.seatunnel_submit_path,
        submit_format=settings.seatunnel_submit_format,
        status_path=settings.seatunnel_status_path,
        cancel_path=settings.seatunnel_cancel_path,
        cleanup_path=settings.seatunnel_cleanup_path,
        swap_path=settings.seatunnel_swap_path,
        rollback_path=settings.seatunnel_rollback_path,
        timeout_seconds=settings.health_check_timeout_seconds,
    )
    if not settings.real_data_plane_enabled:
        return seatunnel
    provider = create_secret_provider(settings)
    return SeaTunnelDorisEngine(seatunnel, DorisTargetAdapter(provider, settings))


def _runtime_compiler_for_settings(settings):
    """创建真实数据面运行时编译器，只在 Worker 提交前读取 Vault Secret。"""
    if not settings.real_data_plane_enabled:
        return None
    provider = create_secret_provider(settings)

    async def compiler(session, execution, version):
        return await compile_runtime_job(
            session,
            execution,
            version,
            settings=settings,
            provider=provider,
        )

    return compiler


async def _dispatch_outbox_event(event_id: UUID) -> str:
    """创建一次短生命周期适配器并消费指定 Outbox 事件。"""
    settings = get_settings()
    session_factory = create_session_factory(settings.database_url)
    replay_client = redis.from_url(settings.replay_guard_redis_url, decode_responses=True)
    try:
        public_key = load_public_key(settings.capability_public_key_path)
        engine = _engine_for_settings(settings)
        async with session_factory() as session:
            result = await dispatch_outbox_event(
                session,
                event_id,
                engine=engine,
                replay_guard=RedisReplayGuard(replay_client),
                public_key=public_key,
                replay_ttl_seconds=settings.replay_guard_ttl_seconds,
                runtime_compiler=_runtime_compiler_for_settings(settings),
            )
        if result.engine_job_id:
            # 提交成功后由独立任务轮询状态，避免把长轮询阻塞在 Outbox 事务内。
            supervise_execution_run_task.delay(str(result.execution_run_id))
        return result.engine_job_id or result.status.value
    finally:
        await replay_client.aclose()


@celery_app.task(name="etl_agent.workers.dispatch_outbox_event")
def dispatch_outbox_event_task(event_id: str) -> str:
    """将字符串事件 ID 转换为 UUID，并在 Celery 同步入口运行异步 Broker。"""
    return asyncio.run(_dispatch_outbox_event(UUID(event_id)))


async def _supervise_execution_run(execution_id: UUID) -> str:
    """查询一次引擎状态并落库质量、预算和监督快照。"""
    settings = get_settings()
    session_factory = create_session_factory(settings.database_url)
    async with session_factory() as session:
        await supervise_execution_run(
            session,
            execution_id,
            engine=_engine_for_settings(settings),
        )
    action_event_ids: list[UUID] = []
    async with session_factory() as session:
        execution_row = await session.get(ExecutionRun, execution_id, with_for_update=True)
        if execution_row is None:
            raise ValueError("ExecutionRun 不存在")
        action: tuple[str, str, dict[str, object]] | None = None
        if execution_row.status == ExecutionRunStatus.CANCEL_REQUESTED.value:
            action = ("execution.cancel", "seatunnel.cancel", {"reason": "运行预算超限"})
        elif execution_row.publish_status == "swap_requested":
            action = (
                "execution.swap",
                "seatunnel.swap",
                {
                    "shadow_table": execution_row.shadow_table,
                    "error_table": execution_row.error_table,
                    **_runtime_target_metadata(execution_row),
                },
            )
        elif (
            execution_row.status == ExecutionRunStatus.FAILED.value and execution_row.engine_job_id
        ):
            action = (
                "execution.cleanup",
                "seatunnel.cleanup",
                {"reason": "失败后清理", **_runtime_target_metadata(execution_row)},
            )
        if action is not None:
            event = await queue_execution_action(
                session,
                execution_row,
                settings=settings,
                event_type=action[0],
                tool=action[1],
                payload=action[2],
            )
            await session.commit()
            if event is not None:
                action_event_ids.append(event.id)
    for event_id in action_event_ids:
        dispatch_outbox_event_task.delay(str(event_id))
    return execution_row.status


def _runtime_target_metadata(execution: ExecutionRun) -> dict[str, object]:
    """从执行事实中提取可安全写入动作 Outbox 的 Doris 元数据。"""
    allowed = {
        "target_connection_id",
        "target_host",
        "target_port",
        "target_secret_ref",
        "target_database",
        "target_table",
        "shadow_table",
        "error_table",
    }
    return {key: value for key, value in execution.metrics_json.items() if key in allowed}


@celery_app.task(
    bind=True,
    name="etl_agent.workers.supervise_execution_run",
    max_retries=120,
    default_retry_delay=5,
)
def supervise_execution_run_task(self, execution_id: str) -> str:
    """将执行 ID 转为 UUID，运行一次可重复的状态监督任务。"""
    state = asyncio.run(_supervise_execution_run(UUID(execution_id)))
    if state in {ExecutionRunStatus.QUEUED.value, ExecutionRunStatus.RUNNING.value}:
        raise self.retry(countdown=5)
    return state


async def _publish_pending_outbox_events() -> int:
    """批量读取待投递事件并交给单事件 Broker，返回本轮成功数。"""
    from datetime import UTC, datetime

    from sqlalchemy import select

    from etl_agent.infrastructure.models import OutboxEvent, OutboxEventStatus

    settings = get_settings()
    session_factory = create_session_factory(settings.database_url)
    replay_client = redis.from_url(settings.replay_guard_redis_url, decode_responses=True)
    published = 0
    supervision_ids: list[UUID] = []
    try:
        public_key = load_public_key(settings.capability_public_key_path)
        async with session_factory() as session:
            event_ids = list(
                await session.scalars(
                    select(OutboxEvent.id)
                    .where(
                        OutboxEvent.status == OutboxEventStatus.PENDING.value,
                        OutboxEvent.next_attempt_at <= datetime.now(UTC),
                    )
                    .order_by(OutboxEvent.created_at)
                    .limit(100)
                )
            )
        for event_id in event_ids:
            try:
                async with session_factory() as session:
                    result = await dispatch_outbox_event(
                        session,
                        event_id,
                        engine=_engine_for_settings(settings),
                        replay_guard=RedisReplayGuard(replay_client),
                        public_key=public_key,
                        replay_ttl_seconds=settings.replay_guard_ttl_seconds,
                        runtime_compiler=_runtime_compiler_for_settings(settings),
                    )
                    published += int(result.status is OutboxEventStatus.PUBLISHED)
                    if result.engine_job_id:
                        # Beat 直接消费 Outbox 时也必须安排独立监督，避免执行永久停在 running。
                        supervision_ids.append(result.execution_run_id)
            except Exception:
                # 单个事件失败不能阻断同一批次的其他事件；详情已由 Broker 脱敏落库。
                continue
    finally:
        await replay_client.aclose()
    for execution_id in supervision_ids:
        supervise_execution_run_task.delay(str(execution_id))
    return published


@celery_app.task(name="etl_agent.workers.publish_pending_outbox")
def publish_pending_outbox_task() -> int:
    """供 Celery Beat 周期性投递 Transactional Outbox 事件。"""
    return asyncio.run(_publish_pending_outbox_events())
