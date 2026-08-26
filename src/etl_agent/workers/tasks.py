"""Celery 任务入口：只负责加载依赖并调用 Outbox Tool Broker。"""

import asyncio
from uuid import UUID

import redis.asyncio as redis

from etl_agent.config import get_settings
from etl_agent.harness.capability import RedisReplayGuard, load_public_key
from etl_agent.infrastructure.database import create_session_factory
from etl_agent.workers.celery_app import celery_app
from etl_agent.workers.dispatcher import dispatch_outbox_event
from etl_agent.workers.engine import SeaTunnelAdapter


async def _dispatch_outbox_event(event_id: UUID) -> str:
    """创建一次短生命周期适配器并消费指定 Outbox 事件。"""
    settings = get_settings()
    session_factory = create_session_factory(settings.database_url)
    replay_client = redis.from_url(settings.replay_guard_redis_url, decode_responses=True)
    try:
        public_key = load_public_key(settings.capability_public_key_path)
        engine = SeaTunnelAdapter(
            settings.seatunnel_zeta_endpoint,
            submit_path=settings.seatunnel_submit_path,
            status_path=settings.seatunnel_status_path,
            cancel_path=settings.seatunnel_cancel_path,
            timeout_seconds=settings.health_check_timeout_seconds,
        )
        async with session_factory() as session:
            result = await dispatch_outbox_event(
                session,
                event_id,
                engine=engine,
                replay_guard=RedisReplayGuard(replay_client),
                public_key=public_key,
                replay_ttl_seconds=settings.replay_guard_ttl_seconds,
            )
        return result.engine_job_id or result.status.value
    finally:
        await replay_client.aclose()


@celery_app.task(name="etl_agent.workers.dispatch_outbox_event")
def dispatch_outbox_event_task(event_id: str) -> str:
    """将字符串事件 ID 转换为 UUID，并在 Celery 同步入口运行异步 Broker。"""
    return asyncio.run(_dispatch_outbox_event(UUID(event_id)))
