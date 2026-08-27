"""Celery 任务入口：加载依赖并执行 Agent、Outbox 和运行监督任务。"""

import asyncio
import logging
import sys
from collections.abc import Coroutine
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import redis.asyncio as redis
from sqlalchemy import select

from etl_agent.config import get_settings
from etl_agent.harness.capability import RedisReplayGuard, load_public_key
from etl_agent.infrastructure.database import create_session_factory
from etl_agent.infrastructure.llm import create_llm_provider
from etl_agent.infrastructure.models import (
    AgentRun,
    AgentRunStatus,
    ExecutionRun,
    ExecutionRunStatus,
    GenerationAttempt,
    PipelineVersion,
)
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

logger = logging.getLogger(__name__)


def _run_async[AsyncResult](coroutine: Coroutine[Any, Any, AsyncResult]) -> AsyncResult:
    """运行 Worker 的异步任务，并在 Windows 强制使用 Selector 事件循环。

    LangGraph PostgreSQL Checkpoint 底层使用 psycopg 异步连接，Windows 的
    ProactorEventLoop 不受 psycopg 支持；FastAPI 启动入口已有同样的约束，
    Celery Worker 也必须在每个同步任务入口遵守该约束。
    """
    if sys.platform == "win32":
        # Celery 任务是同步入口，使用 Runner 可安全管理本次任务的事件循环生命周期。
        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
            return runner.run(coroutine)
    return asyncio.run(coroutine)


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
        logger.info("outbox_dispatch_started event_id=%s", event_id)
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
        logger.info(
            "outbox_dispatch_succeeded event_id=%s execution_run_id=%s status=%s",
            event_id,
            result.execution_run_id,
            result.status.value,
        )
        return result.engine_job_id or result.status.value
    except Exception as exc:
        # 只记录异常类型，避免第三方驱动把连接串或凭据写入 Worker 日志。
        logger.error(
            "outbox_dispatch_failed event_id=%s exception_type=%s",
            event_id,
            type(exc).__name__,
        )
        raise
    finally:
        await replay_client.aclose()


@celery_app.task(name="etl_agent.workers.dispatch_outbox_event")
def dispatch_outbox_event_task(event_id: str) -> str:
    """将字符串事件 ID 转换为 UUID，并在 Celery 同步入口运行异步 Broker。"""
    return _run_async(_dispatch_outbox_event(UUID(event_id)))


async def _run_generation_agent(agent_run_id: UUID) -> str:
    """加载 AgentRun 并执行 LangGraph，逐节点持久化真实进度。"""
    # Worker 不复用 FastAPI 进程中的对象，每次任务都创建短生命周期依赖，便于重启和水平扩展。
    settings = get_settings()
    session_factory = create_session_factory(settings.database_url)
    async with session_factory() as session:
        agent_run = await session.get(AgentRun, agent_run_id)
        if agent_run is None:
            logger.error("agent_generation_failed agent_run_id=%s reason=not_found", agent_run_id)
            raise ValueError("AgentRun 不存在")
        if agent_run.pipeline_version_id is None:
            agent_run.status = AgentRunStatus.FAILED.value
            agent_run.error_code = "VERSION_NOT_FOUND"
            agent_run.error_detail = "AgentRun 未绑定 PipelineVersion"
            await session.commit()
            raise ValueError("AgentRun 未绑定 PipelineVersion")
        version = await session.get(PipelineVersion, agent_run.pipeline_version_id)
        if version is None:
            agent_run.status = AgentRunStatus.FAILED.value
            agent_run.error_code = "VERSION_NOT_FOUND"
            agent_run.error_detail = "PipelineVersion 不存在"
            await session.commit()
            raise ValueError("PipelineVersion 不存在")

        try:
            provider = create_llm_provider(settings)
        except Exception as exc:
            # Provider 配置错误也必须落库为失败，避免控制台永久显示“运行中”。
            agent_run.status = AgentRunStatus.FAILED.value
            agent_run.error_code = "LLM_PROVIDER_UNAVAILABLE"
            agent_run.error_detail = "远端 LLM Provider 配置不可用"
            await session.commit()
            logger.error(
                "agent_generation_failed agent_run_id=%s exception_type=%s",
                agent_run_id,
                type(exc).__name__,
            )
            raise

        from etl_agent.api.generation import _persist_generation_result
        from etl_agent.domain.generation import GenerationRequest
        from etl_agent.workflows.checkpoint import postgres_checkpointer
        from etl_agent.workflows.graph import GenerationState, run_generation_workflow

        try:
            contexts = GenerationRequest.model_validate(agent_run.request_json)
        except ValueError:
            agent_run.status = AgentRunStatus.FAILED.value
            agent_run.error_code = "AGENT_REQUEST_INVALID"
            agent_run.error_detail = "AgentRun 请求快照不可恢复"
            await session.commit()
            logger.error(
                "agent_generation_failed agent_run_id=%s reason=request_invalid exception_type=%s",
                agent_run_id,
                "ValueError",
            )
            raise

        async def persist_progress(node_name: str, state: GenerationState) -> None:
            """将 LangGraph 单节点增量映射为可查询的 AgentRun 快照。"""
            trace = state.get("node_trace", [])
            agent_run.node_trace = [str(item) for item in trace] if isinstance(trace, list) else []
            status_value = state.get("status")
            if isinstance(status_value, str):
                try:
                    agent_run.status = AgentRunStatus(status_value).value
                except ValueError:
                    agent_run.status = AgentRunStatus.RUNNING.value
            repair_count = state.get("repair_count", agent_run.repair_count)
            agent_run.repair_count = int(repair_count) if isinstance(repair_count, int) else 0
            agent_run.provider = (
                str(state["provider"]) if state.get("provider") else agent_run.provider
            )
            agent_run.model = str(state["model"]) if state.get("model") else agent_run.model
            questions = state.get("clarification_questions", [])
            issues = state.get("validation_issues", [])
            agent_run.clarification_questions = (
                [
                    item.model_dump(mode="json") if hasattr(item, "model_dump") else dict(item)
                    for item in questions
                    if isinstance(item, dict) or hasattr(item, "model_dump")
                ]
                if isinstance(questions, list)
                else []
            )
            agent_run.validation_issues = (
                [
                    item.model_dump(mode="json") if hasattr(item, "model_dump") else dict(item)
                    for item in issues
                    if isinstance(item, dict) or hasattr(item, "model_dump")
                ]
                if isinstance(issues, list)
                else []
            )
            attempt_snapshot = state.get("attempts", [])
            if isinstance(attempt_snapshot, list):
                # 每个节点回调都同步尝试摘要，让前端在 LLM 尚未结束时也能看到调用证据。
                stored_attempts = list(
                    await session.scalars(
                        select(GenerationAttempt).where(
                            GenerationAttempt.agent_run_id == agent_run.id
                        )
                    )
                )
                stored_by_number = {attempt.attempt_number: attempt for attempt in stored_attempts}
                for evidence in attempt_snapshot:
                    if not isinstance(evidence, dict):
                        continue
                    raw_attempt_number = evidence.get("attempt_number")
                    if not isinstance(raw_attempt_number, (int, str)):
                        continue
                    try:
                        attempt_number = int(raw_attempt_number)
                    except (TypeError, ValueError):
                        continue
                    attempt = stored_by_number.get(attempt_number)
                    if attempt is None:
                        attempt = GenerationAttempt(
                            agent_run_id=agent_run.id,
                            attempt_number=attempt_number,
                            kind=str(evidence.get("kind", "candidate")),
                            output_digest=evidence.get("output_digest"),
                            status=str(evidence.get("status", "running")),
                            validation_errors=list(evidence.get("validation_errors", [])),
                        )
                        session.add(attempt)
                        stored_by_number[attempt_number] = attempt
                    else:
                        attempt.kind = str(evidence.get("kind", attempt.kind))
                        attempt.output_digest = evidence.get("output_digest")
                        attempt.status = str(evidence.get("status", attempt.status))
                        attempt.validation_errors = list(evidence.get("validation_errors", []))
            await session.commit()
            logger.info(
                "agent_generation_node agent_run_id=%s node=%s status=%s repair_count=%s",
                agent_run_id,
                node_name,
                agent_run.status,
                agent_run.repair_count,
            )

        logger.info(
            "agent_generation_started agent_run_id=%s thread_id=%s",
            agent_run_id,
            agent_run.thread_id,
        )
        try:
            async with postgres_checkpointer(
                settings.langgraph_checkpoint_database_url
            ) as checkpointer:
                result = await run_generation_workflow(
                    contexts,
                    provider,
                    thread_id=agent_run.thread_id,
                    checkpointer=checkpointer,
                    progress_callback=persist_progress,
                )
            response = await _persist_generation_result(
                result, contexts, version, agent_run, session
            )
            logger.info(
                "agent_generation_completed agent_run_id=%s status=%s node_count=%s",
                agent_run_id,
                agent_run.status,
                len(agent_run.node_trace),
            )
            return response.status.value
        except Exception as exc:
            agent_run.status = AgentRunStatus.FAILED.value
            agent_run.error_code = getattr(exc, "code", None) or "WORKFLOW_FAILED"
            agent_run.error_detail = "生成工作流异常"
            await session.commit()
            # 不把 LLM 或数据库异常文本写入日志，稳定错误已落库到 AgentRun。
            logger.error(
                "agent_generation_failed agent_run_id=%s exception_type=%s",
                agent_run_id,
                type(exc).__name__,
            )
            raise


@celery_app.task(name="etl_agent.workers.run_generation")
def generate_agent_run_task(agent_run_id: str) -> str:
    """将 AgentRun ID 转为 UUID，并执行异步生成任务。"""
    return _run_async(_run_generation_agent(UUID(agent_run_id)))


async def _run_agent_chat(agent_run_id: UUID) -> str:
    """加载已完成候选并异步回答审查问题，消息和状态全部持久化。"""
    settings = get_settings()
    session_factory = create_session_factory(settings.database_url)
    async with session_factory() as session:
        agent_run = await session.get(AgentRun, agent_run_id)
        if agent_run is None:
            raise ValueError("AgentRun 不存在")
        version = (
            await session.get(PipelineVersion, agent_run.pipeline_version_id)
            if agent_run.pipeline_version_id
            else None
        )
        if version is None or not version.etl_plan_json:
            agent_run.chat_status = "failed"
            agent_run.chat_error_code = "VERSION_NOT_READY"
            agent_run.chat_error_detail = "候选版本尚未准备好审查"
            await session.commit()
            raise ValueError("候选版本尚未准备好审查")
        messages = [item for item in (agent_run.chat_messages or []) if isinstance(item, dict)]
        question = next(
            (
                str(item.get("content", "")).strip()
                for item in reversed(messages)
                if item.get("role") == "user" and str(item.get("content", "")).strip()
            ),
            "",
        )
        if not question:
            agent_run.chat_status = "failed"
            agent_run.chat_error_code = "AGENT_CHAT_EMPTY"
            agent_run.chat_error_detail = "未找到待回答的审查问题"
            await session.commit()
            raise ValueError("未找到待回答的审查问题")
        agent_run.chat_status = "running"
        agent_run.chat_error_code = None
        agent_run.chat_error_detail = None
        await session.commit()
        logger.info("agent_chat_started agent_run_id=%s", agent_run_id)
        try:
            provider = create_llm_provider(settings)
            answer = await provider.answer_question(
                question,
                {
                    "plan": version.etl_plan_json,
                    "hocon": version.hocon,
                    "conversation": messages[-20:],
                },
            )
            messages.append(
                {
                    "role": "assistant",
                    "content": answer[:8_000],
                    "created_at": datetime.now(UTC).isoformat(),
                }
            )
            agent_run.chat_messages = messages[-100:]
            agent_run.chat_status = "completed"
            await session.commit()
            logger.info("agent_chat_completed agent_run_id=%s", agent_run_id)
            return "completed"
        except Exception as exc:
            agent_run.chat_status = "failed"
            agent_run.chat_error_code = getattr(exc, "code", None) or "AGENT_CHAT_FAILED"
            agent_run.chat_error_detail = "Agent 审查回答失败"
            await session.commit()
            logger.error(
                "agent_chat_failed agent_run_id=%s exception_type=%s",
                agent_run_id,
                type(exc).__name__,
            )
            raise


@celery_app.task(name="etl_agent.workers.run_agent_chat")
def run_agent_chat_task(agent_run_id: str) -> str:
    """将 AgentRun ID 转换为 UUID，并运行异步审查对话任务。"""
    return _run_async(_run_agent_chat(UUID(agent_run_id)))


async def _supervise_execution_run(execution_id: UUID) -> str:
    """查询一次引擎状态并落库质量、预算和监督快照。"""
    settings = get_settings()
    session_factory = create_session_factory(settings.database_url)
    async with session_factory() as session:
        logger.info("execution_supervision_started execution_run_id=%s", execution_id)
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
    logger.info(
        "execution_supervision_completed execution_run_id=%s status=%s actions=%s",
        execution_id,
        execution_row.status,
        len(action_event_ids),
    )
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
    state = _run_async(_supervise_execution_run(UUID(execution_id)))
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
        logger.info("outbox_batch_started pending_count=%s", len(event_ids))
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
            except Exception as exc:
                # 单个事件失败不能阻断同一批次的其他事件；详情已由 Broker 脱敏落库。
                logger.error(
                    "outbox_batch_event_failed event_id=%s exception_type=%s",
                    event_id,
                    type(exc).__name__,
                )
                continue
    finally:
        await replay_client.aclose()
    for execution_id in supervision_ids:
        supervise_execution_run_task.delay(str(execution_id))
    logger.info(
        "outbox_batch_completed pending_count=%s published_count=%s supervision_count=%s",
        len(event_ids),
        published,
        len(supervision_ids),
    )
    return published


@celery_app.task(name="etl_agent.workers.publish_pending_outbox")
def publish_pending_outbox_task() -> int:
    """供 Celery Beat 周期性投递 Transactional Outbox 事件。"""
    return _run_async(_publish_pending_outbox_events())
