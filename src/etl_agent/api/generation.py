"""Pipeline 创建、Agent 生成和设计查询 API。"""

import hashlib
import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import APIRouter, Request, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from etl_agent.api.auth_dependencies import CurrentUser, DbSession, require_project_role
from etl_agent.api.errors import ApiError
from etl_agent.api.generation_models import (
    AgentChatRequest,
    AgentRunResponse,
    GenerationAnswerRequest,
    GenerationStartRequest,
    PipelineCreate,
    PipelineDesignResponse,
    PipelineResponse,
    PipelineVersionCreate,
    PipelineVersionResponse,
)
from etl_agent.domain.generation import (
    ClarificationQuestion,
    EtlPlan,
    GenerationRequest,
    GenerationResult,
    ProfileContext,
    RuntimeBudget,
    ValidationIssue,
    cap_runtime_budget,
)
from etl_agent.infrastructure.models import (
    AgentRun,
    AgentRunStatus,
    Connection,
    GenerationAttempt,
    MetadataProfile,
    Pipeline,
    PipelineVersion,
    PipelineVersionStatus,
    ProjectRole,
    User,
)
from etl_agent.workflows.checkpoint import postgres_checkpointer
from etl_agent.workflows.graph import run_generation_workflow

router = APIRouter(prefix="/api/v1", tags=["generation"])


def _profile_context(profile: MetadataProfile, connection: Connection) -> ProfileContext:
    """将数据库 Profile 转换为只包含字段和脱敏样本的模型上下文。"""
    # Profile 按表保存 columns；这里展平为字段摘要，兼容旧版直接保存 columns 的快照。
    tables = profile.schema_snapshot.get("tables", [])
    if isinstance(tables, list):
        columns = [
            column
            for table in tables
            if isinstance(table, dict)
            for column in table.get("columns", [])
            if isinstance(column, dict)
        ]
    else:
        columns = profile.schema_snapshot.get("columns", [])
    fields = [str(column.get("name")) for column in columns if column.get("name")]
    return ProfileContext(
        profile_id=profile.id,
        connection_id=connection.id,
        fingerprint=profile.fingerprint,
        fields=fields,
        redacted_sample=profile.redacted_sample,
    )


def _artifact_digest(plan_dump: dict[str, object], hocon: str) -> str:
    """对规范化 EtlPlan 和 HOCON 计算不可变制品 SHA-256 摘要。"""
    payload = json.dumps(
        {"etl_plan": plan_dump, "hocon": hocon},
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


async def _get_pipeline_for_user(
    pipeline_id: UUID,
    current_user: CurrentUser,
    session: DbSession,
    allowed_roles: set[ProjectRole] | None = None,
) -> Pipeline:
    """按项目成员边界读取 Pipeline，写操作默认仍限制 Maker/Operator。"""
    pipeline = await session.get(Pipeline, pipeline_id)
    if pipeline is None:
        raise ApiError("PIPELINE_NOT_FOUND", "Pipeline 不存在", status_code=404)
    await require_project_role(
        pipeline.project_id,
        current_user,
        session,
        allowed_roles or {ProjectRole.MAKER, ProjectRole.OPERATOR},
    )
    return pipeline


async def _get_version(version_id: UUID, session: DbSession) -> PipelineVersion:
    """读取 PipelineVersion 并统一处理不存在错误。"""
    version = await session.get(PipelineVersion, version_id)
    if version is None:
        raise ApiError("VERSION_NOT_FOUND", "PipelineVersion 不存在", status_code=404)
    return version


def _agent_run_response(agent_run: AgentRun, plan: EtlPlan | None = None) -> AgentRunResponse:
    """将 AgentRun 当前快照转换为前端可轮询的稳定响应。"""
    try:
        run_status = AgentRunStatus(agent_run.status)
    except ValueError:
        run_status = AgentRunStatus.FAILED
    questions = [
        ClarificationQuestion.model_validate(item)
        for item in agent_run.clarification_questions
        if isinstance(item, dict)
    ]
    issues = [
        ValidationIssue.model_validate(item)
        for item in agent_run.validation_issues
        if isinstance(item, dict)
    ]
    if agent_run.pipeline_version_id is None:
        raise ApiError("VERSION_NOT_FOUND", "AgentRun 未绑定 PipelineVersion", status_code=409)
    chat_messages: list[dict[str, object]] = [
        {
            "role": str(item.get("role", "")),
            "content": str(item.get("content", ""))[:8_000],
            "created_at": str(item.get("created_at", "")),
        }
        for item in (agent_run.chat_messages or [])[-100:]
        if isinstance(item, dict) and item.get("role") in {"user", "assistant"}
    ]
    return AgentRunResponse(
        id=agent_run.id,
        thread_id=agent_run.thread_id,
        status=run_status,
        pipeline_version_id=agent_run.pipeline_version_id,
        repair_count=agent_run.repair_count,
        node_trace=list(agent_run.node_trace),
        attempts=[],
        provider=agent_run.provider,
        model=agent_run.model,
        error_code=agent_run.error_code,
        error_detail=agent_run.error_detail,
        clarification_questions=questions,
        validation_issues=issues,
        chat_status=agent_run.chat_status,
        chat_messages=chat_messages,
        chat_error_code=agent_run.chat_error_code,
        chat_error_detail=agent_run.chat_error_detail,
        plan=plan,
    )


async def _prepare_generation_context(
    version_id: UUID,
    payload: GenerationStartRequest,
    current_user: User,
    session: DbSession,
) -> tuple[PipelineVersion, Pipeline, GenerationRequest, str, str]:
    """校验版本和 Profile，并构造可持久化的生成请求上下文。"""
    version = await _get_version(version_id, session)
    pipeline = await session.get(Pipeline, version.pipeline_id)
    if pipeline is None:
        raise ApiError("PIPELINE_NOT_FOUND", "Pipeline 不存在", status_code=404)
    await require_project_role(pipeline.project_id, current_user, session, {ProjectRole.MAKER})
    if version.immutable:
        raise ApiError("VERSION_IMMUTABLE", "已冻结版本不能重新生成", status_code=409)
    source_result = await session.execute(
        select(MetadataProfile, Connection)
        .join(Connection, Connection.id == MetadataProfile.connection_id)
        .where(
            MetadataProfile.id.in_(payload.source_profile_ids),
            Connection.project_id == pipeline.project_id,
        )
    )
    target_result = await session.execute(
        select(MetadataProfile, Connection)
        .join(Connection, Connection.id == MetadataProfile.connection_id)
        .where(
            MetadataProfile.id.in_(payload.target_profile_ids),
            Connection.project_id == pipeline.project_id,
        )
    )
    source_profiles = list(source_result.all())
    target_profiles = list(target_result.all())
    if len(source_profiles) != len(set(payload.source_profile_ids)) or not target_profiles:
        raise ApiError("PROFILE_NOT_FOUND", "Profile 不存在或不属于当前项目", status_code=404)
    if len(target_profiles) != len(set(payload.target_profile_ids)):
        raise ApiError("PROFILE_NOT_FOUND", "Profile 不存在或不属于当前项目", status_code=404)
    effective_budget = cap_runtime_budget(payload.max_runtime_budget, RuntimeBudget())
    contexts = GenerationRequest(
        business_request=payload.business_request,
        source_profiles=[
            _profile_context(profile, connection) for profile, connection in source_profiles
        ],
        target_profiles=[
            _profile_context(profile, connection) for profile, connection in target_profiles
        ],
        max_runtime_budget=effective_budget,
        prompt_version=payload.prompt_version,
    )
    thread_id = payload.thread_id or str(uuid4())
    prompt_digest = hashlib.sha256(
        json.dumps(contexts.model_dump(mode="json"), sort_keys=True, ensure_ascii=False).encode(
            "utf-8"
        )
    ).hexdigest()
    return version, pipeline, contexts, thread_id, prompt_digest


async def _persist_generation_result(
    result: object,
    contexts: GenerationRequest,
    version: PipelineVersion,
    agent_run: AgentRun,
    session: DbSession,
) -> AgentRunResponse:
    """持久化生成结果、每次尝试证据并在成功时冻结版本。"""
    if not isinstance(result, GenerationResult):
        raise ApiError("WORKFLOW_RESULT_INVALID", "生成工作流返回结果无效", status_code=503)
    agent_run.status = result.status
    agent_run.provider = result.provider
    agent_run.model = result.model
    agent_run.repair_count = result.repair_count
    agent_run.node_trace = result.node_trace
    agent_run.error_code = result.error_code
    agent_run.clarification_questions = [
        question.model_dump(mode="json") for question in result.clarification_questions
    ]
    agent_run.validation_issues = [
        issue.model_dump(mode="json") for issue in result.validation_issues
    ]
    if result.plan is not None:
        plan_dump = result.plan.model_dump(mode="json")
        digest = _artifact_digest(plan_dump, result.plan.hocon)
        version.status = PipelineVersionStatus.READY
        version.immutable = True
        version.artifact_digest = digest
        version.etl_plan_json = plan_dump
        version.hocon = result.plan.hocon
        version.source_profile_ids = [
            str(profile.profile_id) for profile in contexts.source_profiles
        ]
        version.target_profile_ids = [
            str(profile.profile_id) for profile in contexts.target_profiles
        ]
        agent_run.result_digest = digest
    previous_attempt_number = await session.scalar(
        select(func.max(GenerationAttempt.attempt_number)).where(
            GenerationAttempt.agent_run_id == agent_run.id
        )
    )
    previous_attempt_number = previous_attempt_number or 0
    for evidence in result.attempts:
        if int(evidence["attempt_number"]) <= previous_attempt_number:
            continue
        session.add(
            GenerationAttempt(
                agent_run_id=agent_run.id,
                attempt_number=int(evidence["attempt_number"]),
                kind=str(evidence["kind"]),
                output_digest=evidence.get("output_digest"),
                status=str(evidence["status"]),
                validation_errors=list(evidence.get("validation_errors", [])),
            )
        )
    await session.commit()
    response = _agent_run_response(agent_run, result.plan)
    response.attempts = result.attempts
    return response


@router.post("/pipelines", response_model=PipelineResponse, status_code=status.HTTP_201_CREATED)
async def create_pipeline(
    payload: PipelineCreate,
    current_user: CurrentUser,
    session: DbSession,
) -> Pipeline:
    """创建项目 Pipeline，必须由 Maker 或 Operator 发起。"""
    await require_project_role(
        payload.project_id, current_user, session, {ProjectRole.MAKER, ProjectRole.OPERATOR}
    )
    pipeline = Pipeline(project_id=payload.project_id, code=payload.code, name=payload.name)
    session.add(pipeline)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ApiError(
            "PIPELINE_CODE_EXISTS", "项目内 Pipeline 编码已存在", status_code=409
        ) from exc
    await session.refresh(pipeline)
    return pipeline


@router.get("/projects/{project_id}/pipelines", response_model=list[PipelineResponse])
async def list_pipelines(
    project_id: UUID,
    current_user: CurrentUser,
    session: DbSession,
) -> list[Pipeline]:
    """查询项目 Pipeline，供控制台总览和 Studio 选择器使用。"""
    await require_project_role(
        project_id,
        current_user,
        session,
        {
            ProjectRole.MAKER,
            ProjectRole.CHECKER_1,
            ProjectRole.CHECKER_2,
            ProjectRole.OPERATOR,
            ProjectRole.AUDITOR,
        },
    )
    result = await session.scalars(
        select(Pipeline).where(Pipeline.project_id == project_id).order_by(Pipeline.created_at)
    )
    return list(result.all())


@router.post(
    "/pipelines/{pipeline_id}/versions",
    response_model=PipelineVersionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_pipeline_version(
    pipeline_id: UUID,
    payload: PipelineVersionCreate,
    current_user: CurrentUser,
    session: DbSession,
) -> PipelineVersion:
    """创建新的可生成草稿；已冻结版本永远不会被覆盖。"""
    pipeline = await _get_pipeline_for_user(pipeline_id, current_user, session)
    created_by = payload.created_by or current_user.id
    if created_by != current_user.id:
        raise ApiError("VERSION_ACTOR_INVALID", "草稿创建人必须是当前用户", status_code=403)
    latest = await session.scalar(
        select(func.max(PipelineVersion.version_number)).where(
            PipelineVersion.pipeline_id == pipeline.id
        )
    )
    version = PipelineVersion(
        pipeline_id=pipeline.id,
        version_number=(latest or 0) + 1,
        created_by=current_user.id,
        source_profile_ids=[],
        target_profile_ids=[],
    )
    session.add(version)
    await session.commit()
    await session.refresh(version)
    return version


@router.get("/pipelines/{pipeline_id}/versions", response_model=list[PipelineVersionResponse])
async def list_pipeline_versions(
    pipeline_id: UUID,
    current_user: CurrentUser,
    session: DbSession,
) -> list[PipelineVersion]:
    """查询 Pipeline 的不可变版本和草稿状态，避免前端直接访问数据库。"""
    pipeline = await _get_pipeline_for_user(
        pipeline_id,
        current_user,
        session,
        {
            ProjectRole.MAKER,
            ProjectRole.CHECKER_1,
            ProjectRole.CHECKER_2,
            ProjectRole.OPERATOR,
            ProjectRole.AUDITOR,
        },
    )
    result = await session.scalars(
        select(PipelineVersion)
        .where(PipelineVersion.pipeline_id == pipeline.id)
        .order_by(PipelineVersion.version_number.desc())
    )
    return list(result.all())


@router.post("/versions/{version_id}/generation", response_model=AgentRunResponse)
async def generate_version(
    version_id: UUID,
    payload: GenerationStartRequest,
    request: Request,
    current_user: CurrentUser,
    session: DbSession,
) -> AgentRunResponse:
    """运行 LangGraph 生成并仅在门禁通过时冻结草稿版本。"""
    version, pipeline, contexts, thread_id, prompt_digest = await _prepare_generation_context(
        version_id, payload, current_user, session
    )
    agent_run = AgentRun(
        project_id=pipeline.project_id,
        pipeline_version_id=version.id,
        thread_id=thread_id,
        prompt_version=payload.prompt_version,
        request_json=contexts.model_dump(mode="json"),
        prompt_digest=prompt_digest,
        node_trace=[],
    )
    session.add(agent_run)
    await session.flush()
    try:
        async with postgres_checkpointer(
            request.app.state.settings.langgraph_checkpoint_database_url
        ) as checkpointer:
            result = await run_generation_workflow(
                contexts,
                request.app.state.llm_provider,
                thread_id=thread_id,
                checkpointer=checkpointer,
            )
    except Exception as exc:
        agent_run.status = AgentRunStatus.FAILED
        agent_run.error_code = "WORKFLOW_FAILED"
        agent_run.error_detail = "生成工作流异常"
        await session.commit()
        raise ApiError("WORKFLOW_FAILED", "生成工作流暂时失败", status_code=503) from exc
    return await _persist_generation_result(result, contexts, version, agent_run, session)


@router.post(
    "/versions/{version_id}/generation/async",
    response_model=AgentRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_async_generation(
    version_id: UUID,
    payload: GenerationStartRequest,
    current_user: CurrentUser,
    session: DbSession,
) -> AgentRunResponse:
    """创建异步 AgentRun 并交给 Celery，供控制台轮询逐节点进度。"""
    version, pipeline, contexts, thread_id, prompt_digest = await _prepare_generation_context(
        version_id, payload, current_user, session
    )
    agent_run = AgentRun(
        project_id=pipeline.project_id,
        pipeline_version_id=version.id,
        thread_id=thread_id,
        prompt_version=payload.prompt_version,
        request_json=contexts.model_dump(mode="json"),
        prompt_digest=prompt_digest,
        node_trace=[],
        status=AgentRunStatus.RUNNING.value,
    )
    session.add(agent_run)
    await session.commit()
    await session.refresh(agent_run)
    try:
        # 延迟导入避免 API 路由和 Celery 任务模块互相导入。
        from etl_agent.workers.tasks import generate_agent_run_task

        generate_agent_run_task.delay(str(agent_run.id))
    except Exception as exc:
        agent_run.status = AgentRunStatus.FAILED.value
        agent_run.error_code = "GENERATION_QUEUE_UNAVAILABLE"
        agent_run.error_detail = "Agent 任务队列暂时不可用"
        await session.commit()
        raise ApiError(
            "GENERATION_QUEUE_UNAVAILABLE", "Agent 任务队列暂时不可用", status_code=503
        ) from exc
    return _agent_run_response(agent_run)


@router.get("/agent-runs/{run_id}", response_model=AgentRunResponse)
async def get_agent_run(
    run_id: UUID,
    current_user: CurrentUser,
    session: DbSession,
) -> AgentRunResponse:
    """查询 AgentRun 的当前节点、澄清问题和校验结果。"""
    agent_run = await session.get(AgentRun, run_id)
    if agent_run is None:
        raise ApiError("AGENT_RUN_NOT_FOUND", "AgentRun 不存在", status_code=404)
    await require_project_role(
        agent_run.project_id,
        current_user,
        session,
        {
            ProjectRole.MAKER,
            ProjectRole.CHECKER_1,
            ProjectRole.CHECKER_2,
            ProjectRole.OPERATOR,
            ProjectRole.AUDITOR,
        },
    )
    plan: EtlPlan | None = None
    if agent_run.pipeline_version_id is not None:
        version = await session.get(PipelineVersion, agent_run.pipeline_version_id)
        if version is not None and version.etl_plan_json:
            plan = EtlPlan.model_validate(version.etl_plan_json)
    attempts = await session.scalars(
        select(GenerationAttempt)
        .where(GenerationAttempt.agent_run_id == agent_run.id)
        .order_by(GenerationAttempt.attempt_number)
    )
    response = _agent_run_response(agent_run, plan)
    response.attempts = [
        {
            "attempt_number": attempt.attempt_number,
            "kind": attempt.kind,
            "status": attempt.status,
            "output_digest": attempt.output_digest,
            "validation_errors": list(attempt.validation_errors),
        }
        for attempt in attempts
    ]
    return response


@router.post(
    "/agent-runs/{run_id}/answers",
    response_model=AgentRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def answer_agent_run(
    run_id: UUID,
    payload: GenerationAnswerRequest,
    current_user: CurrentUser,
    session: DbSession,
) -> AgentRunResponse:
    """提交澄清答案并排队到 Worker，前端继续轮询原 AgentRun 的真实进度。"""
    agent_run = await session.get(AgentRun, run_id)
    if agent_run is None:
        raise ApiError("AGENT_RUN_NOT_FOUND", "AgentRun 不存在", status_code=404)
    await require_project_role(agent_run.project_id, current_user, session, {ProjectRole.MAKER})
    if agent_run.pipeline_version_id is None:
        raise ApiError("VERSION_NOT_FOUND", "AgentRun 未绑定 PipelineVersion", status_code=409)
    if agent_run.status != AgentRunStatus.NEEDS_CLARIFICATION:
        raise ApiError("AGENT_RUN_NOT_WAITING", "该 AgentRun 当前不等待澄清答案", status_code=409)
    try:
        previous_request = GenerationRequest.model_validate(agent_run.request_json)
    except ValueError as exc:
        raise ApiError(
            "AGENT_REQUEST_INVALID", "AgentRun 请求快照不可恢复", status_code=409
        ) from exc
    contexts = previous_request.model_copy(
        update={"answers": {**previous_request.answers, **payload.answers}}
    )
    agent_run.request_json = contexts.model_dump(mode="json")
    agent_run.status = AgentRunStatus.RUNNING.value
    agent_run.clarification_questions = []
    try:
        await session.commit()
        # 延迟导入避免 API 路由和 Celery 任务模块互相导入。
        from etl_agent.workers.tasks import generate_agent_run_task

        generate_agent_run_task.delay(str(agent_run.id))
    except Exception as exc:
        agent_run.status = AgentRunStatus.FAILED.value
        agent_run.error_code = "GENERATION_QUEUE_UNAVAILABLE"
        agent_run.error_detail = "Agent 任务队列暂时不可用"
        await session.commit()
        raise ApiError(
            "GENERATION_QUEUE_UNAVAILABLE", "Agent 任务队列暂时不可用", status_code=503
        ) from exc
    return _agent_run_response(agent_run)


@router.post(
    "/agent-runs/{run_id}/chat",
    response_model=AgentRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def chat_with_agent(
    run_id: UUID,
    payload: AgentChatRequest,
    current_user: CurrentUser,
    session: DbSession,
) -> AgentRunResponse:
    """记录候选审查问题并交给 Worker 异步回答，保持对话可恢复。"""
    agent_run = await session.get(AgentRun, run_id)
    if agent_run is None:
        raise ApiError("AGENT_RUN_NOT_FOUND", "AgentRun 不存在", status_code=404)
    await require_project_role(agent_run.project_id, current_user, session, {ProjectRole.MAKER})
    if agent_run.status != AgentRunStatus.COMPLETED.value:
        raise ApiError("AGENT_CHAT_REQUIRES_COMPLETED", "生成完成后才能审查候选", status_code=409)
    if agent_run.chat_status in {"queued", "running"}:
        raise ApiError("AGENT_CHAT_BUSY", "上一条审查问题仍在处理中", status_code=409)
    message = payload.message.strip()
    if not message:
        raise ApiError("AGENT_CHAT_EMPTY", "审查问题不能为空", status_code=422)
    messages = list(agent_run.chat_messages or [])
    messages.append(
        {
            "role": "user",
            "content": message,
            "created_at": datetime.now(UTC).isoformat(),
        }
    )
    agent_run.chat_messages = messages[-100:]
    agent_run.chat_status = "queued"
    agent_run.chat_error_code = None
    agent_run.chat_error_detail = None
    await session.commit()
    try:
        # 延迟导入避免 API 路由和 Celery 任务模块互相导入。
        from etl_agent.workers.tasks import run_agent_chat_task

        run_agent_chat_task.delay(str(agent_run.id))
    except Exception as exc:
        agent_run.chat_status = "failed"
        agent_run.chat_error_code = "GENERATION_QUEUE_UNAVAILABLE"
        agent_run.chat_error_detail = "Agent 对话队列暂时不可用"
        await session.commit()
        raise ApiError(
            "GENERATION_QUEUE_UNAVAILABLE", "Agent 对话队列暂时不可用", status_code=503
        ) from exc
    return _agent_run_response(agent_run)


@router.get("/versions/{version_id}/design", response_model=PipelineDesignResponse)
async def get_version_design(
    version_id: UUID,
    current_user: CurrentUser,
    session: DbSession,
) -> PipelineDesignResponse:
    """查询已冻结版本的 EtlPlan/HOCON，不返回可变草稿。"""
    version = await _get_version(version_id, session)
    pipeline = await session.get(Pipeline, version.pipeline_id)
    if pipeline is None:
        raise ApiError("PIPELINE_NOT_FOUND", "Pipeline 不存在", status_code=404)
    await require_project_role(
        pipeline.project_id,
        current_user,
        session,
        {
            ProjectRole.MAKER,
            ProjectRole.OPERATOR,
            ProjectRole.CHECKER_1,
            ProjectRole.CHECKER_2,
            ProjectRole.AUDITOR,
        },
    )
    if not version.immutable or not version.etl_plan_json or not version.hocon:
        raise ApiError("VERSION_NOT_READY", "该版本尚未通过生成门禁", status_code=409)
    from etl_agent.domain.generation import EtlPlan

    return PipelineDesignResponse(
        version=PipelineVersionResponse.model_validate(version),
        etl_plan=EtlPlan.model_validate(version.etl_plan_json),
        hocon=version.hocon,
    )
