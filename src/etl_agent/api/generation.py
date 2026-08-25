"""Pipeline 创建、Agent 生成和设计查询 API。"""

import hashlib
import json
from uuid import UUID, uuid4

from fastapi import APIRouter, Request, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from etl_agent.api.auth_dependencies import CurrentUser, DbSession, require_project_role
from etl_agent.api.errors import ApiError
from etl_agent.api.generation_models import (
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
    GenerationRequest,
    GenerationResult,
    ProfileContext,
    RuntimeBudget,
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
)
from etl_agent.workflows.checkpoint import postgres_checkpointer
from etl_agent.workflows.graph import run_generation_workflow

router = APIRouter(prefix="/api/v1", tags=["generation"])


def _profile_context(profile: MetadataProfile, connection: Connection) -> ProfileContext:
    """将数据库 Profile 转换为只包含字段和脱敏样本的模型上下文。"""
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
) -> Pipeline:
    """按项目成员边界读取 Pipeline，避免跨项目访问。"""
    pipeline = await session.get(Pipeline, pipeline_id)
    if pipeline is None:
        raise ApiError("PIPELINE_NOT_FOUND", "Pipeline 不存在", status_code=404)
    await require_project_role(
        pipeline.project_id, current_user, session, {ProjectRole.MAKER, ProjectRole.OPERATOR}
    )
    return pipeline


async def _get_version(version_id: UUID, session: DbSession) -> PipelineVersion:
    """读取 PipelineVersion 并统一处理不存在错误。"""
    version = await session.get(PipelineVersion, version_id)
    if version is None:
        raise ApiError("VERSION_NOT_FOUND", "PipelineVersion 不存在", status_code=404)
    return version


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
    try:
        run_status = AgentRunStatus(result.status)
    except ValueError:
        run_status = AgentRunStatus.FAILED
    return AgentRunResponse(
        id=agent_run.id,
        thread_id=agent_run.thread_id,
        status=run_status,
        pipeline_version_id=version.id,
        repair_count=result.repair_count,
        node_trace=result.node_trace,
        attempts=result.attempts,
        provider=result.provider,
        model=result.model,
        error_code=result.error_code,
        validation_issues=result.validation_issues,
        plan=result.plan,
    )


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


@router.post("/versions/{version_id}/generation", response_model=AgentRunResponse)
async def generate_version(
    version_id: UUID,
    payload: GenerationStartRequest,
    request: Request,
    current_user: CurrentUser,
    session: DbSession,
) -> AgentRunResponse:
    """运行 LangGraph 生成并仅在门禁通过时冻结草稿版本。"""
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


@router.post("/agent-runs/{run_id}/answers", response_model=AgentRunResponse)
async def answer_agent_run(
    run_id: UUID,
    payload: GenerationAnswerRequest,
    request: Request,
    current_user: CurrentUser,
    session: DbSession,
) -> AgentRunResponse:
    """提交澄清答案并使用原 thread_id 从 PostgreSQL Checkpoint 继续生成。"""
    agent_run = await session.get(AgentRun, run_id)
    if agent_run is None:
        raise ApiError("AGENT_RUN_NOT_FOUND", "AgentRun 不存在", status_code=404)
    await require_project_role(agent_run.project_id, current_user, session, {ProjectRole.MAKER})
    if agent_run.pipeline_version_id is None:
        raise ApiError("VERSION_NOT_FOUND", "AgentRun 未绑定 PipelineVersion", status_code=409)
    version = await _get_version(agent_run.pipeline_version_id, session)
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
    try:
        async with postgres_checkpointer(
            request.app.state.settings.langgraph_checkpoint_database_url
        ) as checkpointer:
            result = await run_generation_workflow(
                contexts,
                request.app.state.llm_provider,
                thread_id=agent_run.thread_id,
                checkpointer=checkpointer,
            )
    except Exception as exc:
        agent_run.status = AgentRunStatus.FAILED
        agent_run.error_code = "WORKFLOW_FAILED"
        agent_run.error_detail = "澄清恢复工作流异常"
        await session.commit()
        raise ApiError("WORKFLOW_FAILED", "澄清恢复工作流暂时失败", status_code=503) from exc
    return await _persist_generation_result(result, contexts, version, agent_run, session)


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
