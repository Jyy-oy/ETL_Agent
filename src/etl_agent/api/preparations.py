"""M4.1 Preparation 冻结 API。"""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from fastapi import APIRouter, Request, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from etl_agent.api.auth_dependencies import CurrentUser, DbSession, require_project_role
from etl_agent.api.errors import ApiError
from etl_agent.api.preparation_models import (
    ApprovalDecisionRequest,
    ApprovalRequestResponse,
    CommitResponse,
    ExecutionActionRequest,
    ExecutionQualityResultResponse,
    ExecutionRunResponse,
    PreparationCreate,
    PreparationResponse,
    RuntimeSupervisionSnapshotResponse,
)
from etl_agent.domain.generation import RuntimeBudget, cap_runtime_budget
from etl_agent.harness.actions import issue_execution_action_capability
from etl_agent.harness.capability import (
    CapabilityClaims,
    CapabilityError,
    issue_capability,
    load_private_key,
)
from etl_agent.harness.ledger import append_evidence_event
from etl_agent.harness.models import (
    ApprovalDecision,
    ApprovalStatus,
    DataClassification,
    PolicyInput,
    PreparationFacts,
    PreparationStatus,
    RiskLevel,
    ToolIntent,
)
from etl_agent.harness.pdp import decide_policy
from etl_agent.infrastructure.models import (
    ApprovalRequest,
    Connection,
    ExecutionQualityResult,
    ExecutionRun,
    ExecutionRunStatus,
    MetadataProfile,
    OutboxEvent,
    OutboxEventStatus,
    Pipeline,
    PipelineVersion,
    PipelineVersionStatus,
    Preparation,
    ProjectRole,
    RollbackStatus,
    RuntimeSupervisionSnapshot,
)

router = APIRouter(prefix="/api/v1", tags=["harness"])


def _preparation_fingerprint(
    version: PipelineVersion,
    profile_fingerprints: list[str],
    policy_input: PolicyInput,
) -> str:
    """根据版本摘要、Profile 摘要和策略输入计算冻结事实指纹。"""
    payload = {
        "artifact_digest": version.artifact_digest,
        "profile_fingerprints": sorted(profile_fingerprints),
        "policy_input": policy_input.model_dump(mode="json"),
    }
    serialized = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _approval_response(approval: ApprovalRequest) -> ApprovalRequestResponse:
    """把数据库审批槽转换为稳定的 API 响应模型。"""
    return ApprovalRequestResponse(
        id=approval.id,
        project_id=approval.project_id,
        preparation_id=approval.preparation_id,
        required_role=approval.required_role,
        status=ApprovalStatus(approval.status),
        decision=ApprovalDecision(approval.decision) if approval.decision else None,
        approver_id=approval.approver_id,
        comment=approval.comment,
        decided_at=approval.decided_at,
        created_at=approval.created_at,
        updated_at=approval.updated_at,
    )


def _preparation_response(
    preparation: Preparation,
    approvals: list[ApprovalRequest] | None = None,
) -> PreparationResponse:
    """把数据库 Preparation 转换为稳定的 API 响应模型。"""
    return PreparationResponse(
        id=preparation.id,
        project_id=preparation.project_id,
        pipeline_version_id=preparation.pipeline_version_id,
        created_by=preparation.created_by,
        status=PreparationStatus(preparation.status),
        risk_level=RiskLevel(preparation.risk_level),
        policy_version=preparation.policy_version,
        input_fingerprint=preparation.input_fingerprint,
        required_roles=list(preparation.required_roles),
        resource_scope=dict(preparation.resource_scope),
        runtime_budget=RuntimeBudget.model_validate(preparation.runtime_budget),
        facts=dict(preparation.facts_json),
        approval_requests=[_approval_response(approval) for approval in (approvals or [])],
        expires_at=preparation.expires_at,
        created_at=preparation.created_at,
        updated_at=preparation.updated_at,
    )


def _execution_response(execution: ExecutionRun) -> ExecutionRunResponse:
    """把执行事实转换为不包含 Capability 原文的查询响应。"""
    return ExecutionRunResponse(
        id=execution.id,
        project_id=execution.project_id,
        preparation_id=execution.preparation_id,
        pipeline_version_id=execution.pipeline_version_id,
        status=ExecutionRunStatus(execution.status),
        engine_name=execution.engine_name,
        engine_job_id=execution.engine_job_id,
        idempotency_key=execution.idempotency_key,
        artifact_digest=execution.artifact_digest,
        input_fingerprint=execution.input_fingerprint,
        capability_token_digest=execution.capability_token_digest,
        correlation_id=execution.correlation_id,
        committed_at=execution.committed_at,
        started_at=execution.started_at,
        completed_at=execution.completed_at,
        metrics=dict(execution.metrics_json),
        error_code=execution.error_code,
        error_detail=execution.error_detail,
        quality_status=execution.quality_status,
        publish_status=execution.publish_status,
        rollback_status=execution.rollback_status,
        shadow_table=execution.shadow_table,
        error_table=execution.error_table,
        created_at=execution.created_at,
        updated_at=execution.updated_at,
    )


def _policy_input_from_preparation(preparation: Preparation) -> PolicyInput:
    """从冻结 Preparation 事实还原指纹计算所需的确定性策略输入。"""
    facts = preparation.facts_json
    return PolicyInput(
        tool_intent=ToolIntent(str(facts.get("tool_intent", ToolIntent.ETL_EXECUTE.value))),
        environment=str(facts.get("environment", "development")),
        data_classification=DataClassification(
            str(facts.get("data_classification", DataClassification.INTERNAL.value))
        ),
        writes_target=bool(facts.get("writes_target", True)),
        runtime_budget=RuntimeBudget.model_validate(preparation.runtime_budget),
    )


@router.post(
    "/versions/{version_id}/prepare",
    response_model=PreparationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def prepare_version(
    version_id: UUID,
    payload: PreparationCreate,
    request: Request,
    current_user: CurrentUser,
    session: DbSession,
) -> PreparationResponse:
    """对已冻结版本执行无副作用的 PDP 决策并创建 Preparation。"""
    version = await session.get(PipelineVersion, version_id)
    if version is None:
        raise ApiError("VERSION_NOT_FOUND", "PipelineVersion 不存在", status_code=404)
    pipeline = await session.get(Pipeline, version.pipeline_id)
    if pipeline is None:
        raise ApiError("PIPELINE_NOT_FOUND", "Pipeline 不存在", status_code=404)
    await require_project_role(pipeline.project_id, current_user, session, {ProjectRole.MAKER})
    if not version.immutable or version.status != PipelineVersionStatus.READY:
        raise ApiError("VERSION_NOT_READY", "只有通过门禁的不可变版本才能 Prepare", status_code=409)
    if not version.artifact_digest:
        raise ApiError("VERSION_DIGEST_MISSING", "版本缺少制品摘要，无法 Prepare", status_code=409)

    profile_ids = [*version.source_profile_ids, *version.target_profile_ids]
    if not profile_ids:
        raise ApiError(
            "PROFILE_REFERENCE_MISSING", "版本未引用 Profile，无法 Prepare", status_code=409
        )
    profile_result = await session.execute(
        select(MetadataProfile, Connection)
        .join(Connection, Connection.id == MetadataProfile.connection_id)
        .where(
            MetadataProfile.id.in_(profile_ids),
            Connection.project_id == pipeline.project_id,
        )
    )
    profiles = list(profile_result.all())
    if len(profiles) != len(set(profile_ids)):
        raise ApiError("PROFILE_NOT_FOUND", "版本引用的 Profile 不存在或已越权", status_code=409)

    effective_budget = cap_runtime_budget(payload.runtime_budget, RuntimeBudget())
    policy_input = PolicyInput(
        tool_intent=payload.tool_intent,
        environment=payload.environment,
        data_classification=payload.data_classification,
        writes_target=payload.writes_target,
        runtime_budget=effective_budget,
    )
    decision = decide_policy(policy_input)
    resource_scope = {
        **payload.resource_scope,
        "pipeline_version_id": str(version.id),
        "source_profile_ids": list(version.source_profile_ids),
        "target_profile_ids": list(version.target_profile_ids),
        "connection_ids": sorted({str(connection.id) for _, connection in profiles}),
    }
    input_fingerprint = _preparation_fingerprint(
        version, [profile.fingerprint for profile, _ in profiles], policy_input
    )
    facts = PreparationFacts(
        tool_intent=payload.tool_intent,
        environment=payload.environment,
        data_classification=payload.data_classification,
        writes_target=payload.writes_target,
        risk_level=decision.risk_level,
        policy_version=decision.policy_version,
        required_roles=decision.required_roles,
        runtime_budget=effective_budget,
        resource_scope=resource_scope,
        input_fingerprint=input_fingerprint,
    )
    now = datetime.now(UTC)
    ttl_seconds = max(
        60, min(int(getattr(request.app.state.settings, "preparation_ttl_seconds", 300)), 3600)
    )
    preparation = Preparation(
        id=uuid4(),
        project_id=pipeline.project_id,
        pipeline_version_id=version.id,
        created_by=current_user.id,
        status=(
            PreparationStatus.APPROVAL_PENDING.value
            if decision.required_roles
            else PreparationStatus.PREPARED.value
        ),
        risk_level=decision.risk_level.value,
        policy_version=decision.policy_version,
        input_fingerprint=input_fingerprint,
        required_roles=decision.required_roles,
        resource_scope=resource_scope,
        runtime_budget=effective_budget.model_dump(mode="json"),
        facts_json={**facts.model_dump(mode="json"), "pdp_reasons": decision.reasons},
        expires_at=now + timedelta(seconds=ttl_seconds),
    )
    session.add(preparation)
    # 先把父表 Preparation 刷入当前事务，确保无 ORM 关系映射时审批槽外键可用。
    await session.flush()
    approvals = [
        ApprovalRequest(
            id=uuid4(),
            project_id=pipeline.project_id,
            preparation_id=preparation.id,
            required_role=required_role,
        )
        for required_role in decision.required_roles
    ]
    session.add_all(approvals)
    await session.commit()
    await session.refresh(preparation)
    return _preparation_response(preparation, approvals)


@router.get("/projects/{project_id}/preparations", response_model=list[PreparationResponse])
async def list_preparations(
    project_id: UUID,
    current_user: CurrentUser,
    session: DbSession,
) -> list[PreparationResponse]:
    """查询项目 Preparation 及审批槽，供审批工作台展示冻结事实。"""
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
    preparations = await session.scalars(
        select(Preparation)
        .where(Preparation.project_id == project_id)
        .order_by(Preparation.created_at.desc())
    )
    rows: list[PreparationResponse] = []
    for preparation in preparations:
        approvals = await session.scalars(
            select(ApprovalRequest)
            .where(ApprovalRequest.preparation_id == preparation.id)
            .order_by(ApprovalRequest.created_at)
        )
        rows.append(_preparation_response(preparation, list(approvals.all())))
    return rows


@router.post(
    "/approval-requests/{approval_id}/decisions",
    response_model=ApprovalRequestResponse,
)
async def decide_approval(
    approval_id: UUID,
    payload: ApprovalDecisionRequest,
    current_user: CurrentUser,
    session: DbSession,
) -> ApprovalRequestResponse:
    """校验 Checker 职责分离并提交一个不可重复的审批决定。"""
    approval = await session.scalar(
        select(ApprovalRequest).where(ApprovalRequest.id == approval_id).with_for_update()
    )
    if approval is None:
        raise ApiError("APPROVAL_NOT_FOUND", "审批请求不存在", status_code=404)
    preparation = await session.scalar(
        select(Preparation).where(Preparation.id == approval.preparation_id).with_for_update()
    )
    if preparation is None:
        raise ApiError("PREPARATION_NOT_FOUND", "Preparation 不存在", status_code=404)
    now = datetime.now(UTC)
    if preparation.expires_at <= now:
        preparation.status = PreparationStatus.EXPIRED.value
        await session.commit()
        raise ApiError("PREPARATION_EXPIRED", "Preparation 已过期，请重新 Prepare", status_code=409)
    if approval.status != ApprovalStatus.PENDING.value:
        raise ApiError("APPROVAL_ALREADY_DECIDED", "该审批槽已经完成决策", status_code=409)
    try:
        required_role = ProjectRole(approval.required_role)
    except ValueError as exc:
        raise ApiError("APPROVAL_ROLE_INVALID", "审批槽职责无效", status_code=409) from exc
    await require_project_role(preparation.project_id, current_user, session, {required_role})
    if current_user.id == preparation.created_by:
        raise ApiError(
            "SELF_APPROVAL_FORBIDDEN", "Preparation 创建人不得审批自己的申请", status_code=403
        )

    approval.approver_id = current_user.id
    approval.decision = payload.decision.value
    approval.comment = payload.comment
    approval.decided_at = now
    approval.status = (
        ApprovalStatus.APPROVED.value
        if payload.decision is ApprovalDecision.APPROVE
        else ApprovalStatus.REJECTED.value
    )
    if approval.status == ApprovalStatus.REJECTED.value:
        preparation.status = PreparationStatus.REJECTED.value
    else:
        pending_count = await session.scalar(
            select(ApprovalRequest.id).where(
                ApprovalRequest.preparation_id == preparation.id,
                ApprovalRequest.status == ApprovalStatus.PENDING.value,
                ApprovalRequest.id != approval.id,
            )
        )
        if pending_count is None:
            preparation.status = PreparationStatus.APPROVED.value
    await session.commit()
    await session.refresh(approval)
    return _approval_response(approval)


@router.post(
    "/preparations/{preparation_id}/commit",
    response_model=CommitResponse,
    status_code=status.HTTP_201_CREATED,
)
async def commit_preparation(
    preparation_id: UUID,
    request: Request,
    response: Response,
    current_user: CurrentUser,
    session: DbSession,
) -> CommitResponse:
    """复核冻结事实并在一个事务中创建 ExecutionRun、Outbox 和账本事件。"""
    preparation = await session.scalar(
        select(Preparation).where(Preparation.id == preparation_id).with_for_update()
    )
    if preparation is None:
        raise ApiError("PREPARATION_NOT_FOUND", "Preparation 不存在", status_code=404)
    await require_project_role(
        preparation.project_id, current_user, session, {ProjectRole.OPERATOR}
    )

    existing_execution = await session.scalar(
        select(ExecutionRun).where(ExecutionRun.preparation_id == preparation.id)
    )
    if preparation.status == PreparationStatus.COMMITTED.value:
        if existing_execution is None:
            raise ApiError(
                "EXECUTION_FACT_MISSING", "Preparation 已提交但执行事实缺失", status_code=409
            )
        outbox = await session.scalar(
            select(OutboxEvent).where(
                OutboxEvent.aggregate_id == existing_execution.id,
                OutboxEvent.event_type == "execution.submit",
            )
        )
        if outbox is None:
            raise ApiError("OUTBOX_EVENT_MISSING", "执行事实缺少待投递事件", status_code=409)
        response.status_code = status.HTTP_200_OK
        return CommitResponse(
            execution_run_id=existing_execution.id,
            preparation_id=preparation.id,
            status=ExecutionRunStatus(existing_execution.status),
            idempotency_key=existing_execution.idempotency_key,
            outbox_event_id=outbox.id,
            capability_token_digest=existing_execution.capability_token_digest,
            committed_at=existing_execution.committed_at,
            idempotent=True,
        )

    if preparation.status not in {
        PreparationStatus.APPROVED.value,
        PreparationStatus.PREPARED.value,
    }:
        raise ApiError(
            "PREPARATION_NOT_COMMITTABLE",
            "Preparation 尚未满足 Commit 条件",
            status_code=409,
        )
    now = datetime.now(UTC)
    if preparation.expires_at <= now:
        preparation.status = PreparationStatus.EXPIRED.value
        await session.commit()
        raise ApiError("PREPARATION_EXPIRED", "Preparation 已过期，请重新 Prepare", status_code=409)

    version = await session.scalar(
        select(PipelineVersion)
        .where(PipelineVersion.id == preparation.pipeline_version_id)
        .with_for_update()
    )
    if version is None or not version.immutable or version.status != PipelineVersionStatus.READY:
        raise ApiError(
            "VERSION_NOT_READY", "关联版本已不可执行，请重新生成并 Prepare", status_code=409
        )
    if not version.artifact_digest:
        raise ApiError("VERSION_DIGEST_MISSING", "版本缺少制品摘要，无法 Commit", status_code=409)

    profile_ids = [*version.source_profile_ids, *version.target_profile_ids]
    profile_result = await session.execute(
        select(MetadataProfile, Connection)
        .join(Connection, Connection.id == MetadataProfile.connection_id)
        .where(
            MetadataProfile.id.in_(profile_ids),
            Connection.project_id == preparation.project_id,
        )
    )
    profiles = list(profile_result.all())
    if len(profiles) != len(set(profile_ids)):
        raise ApiError("PROFILE_NOT_FOUND", "版本引用的 Profile 不存在或已越权", status_code=409)
    current_fingerprint = _preparation_fingerprint(
        version,
        [profile.fingerprint for profile, _ in profiles],
        _policy_input_from_preparation(preparation),
    )
    if current_fingerprint != preparation.input_fingerprint:
        preparation.status = PreparationStatus.REJECTED.value
        await session.commit()
        raise ApiError(
            "PREPARATION_FINGERPRINT_MISMATCH",
            "Preparation 事实已变化，Commit 被拒绝",
            status_code=409,
            details={"resource_type": "preparation", "resource_id": str(preparation.id)},
        )

    approvals = await session.scalars(
        select(ApprovalRequest).where(ApprovalRequest.preparation_id == preparation.id)
    )
    approval_rows = list(approvals.all())
    required_roles = set(preparation.required_roles)
    approved_roles = {
        approval.required_role
        for approval in approval_rows
        if approval.status == ApprovalStatus.APPROVED.value
    }
    if not required_roles.issubset(approved_roles):
        raise ApiError("APPROVALS_INCOMPLETE", "所有必需审批槽批准后才能 Commit", status_code=409)

    idempotency_key = request.headers.get("Idempotency-Key", "").strip()
    if not idempotency_key:
        idempotency_key = f"commit:{preparation.id}"
    if len(idempotency_key) > 128:
        raise ApiError(
            "IDEMPOTENCY_KEY_INVALID", "Idempotency-Key 长度不能超过 128", status_code=400
        )
    duplicate_key_execution = await session.scalar(
        select(ExecutionRun).where(ExecutionRun.idempotency_key == idempotency_key)
    )
    if duplicate_key_execution is not None:
        if duplicate_key_execution.preparation_id != preparation.id:
            raise ApiError(
                "IDEMPOTENCY_KEY_REUSED", "Idempotency-Key 已用于其他执行", status_code=409
            )
        outbox = await session.scalar(
            select(OutboxEvent).where(
                OutboxEvent.aggregate_id == duplicate_key_execution.id,
                OutboxEvent.event_type == "execution.submit",
            )
        )
        if outbox is None:
            raise ApiError("OUTBOX_EVENT_MISSING", "幂等执行缺少待投递事件", status_code=409)
        preparation.status = PreparationStatus.COMMITTED.value
        await session.commit()
        response.status_code = status.HTTP_200_OK
        return CommitResponse(
            execution_run_id=duplicate_key_execution.id,
            preparation_id=preparation.id,
            status=ExecutionRunStatus(duplicate_key_execution.status),
            idempotency_key=duplicate_key_execution.idempotency_key,
            outbox_event_id=outbox.id,
            capability_token_digest=duplicate_key_execution.capability_token_digest,
            committed_at=duplicate_key_execution.committed_at,
            idempotent=True,
        )

    settings = request.app.state.settings
    try:
        private_key = load_private_key(settings.capability_private_key_path)
        capability_ttl = max(60, min(int(settings.capability_ttl_seconds), 3600))
        issued_at = int(now.timestamp())
        capability_token = issue_capability(
            CapabilityClaims(
                jti=uuid4(),
                subject=current_user.id,
                tool="seatunnel.submit",
                environment=str(preparation.facts_json.get("environment", "development")),
                preparation_id=preparation.id,
                artifact_digest=version.artifact_digest,
                issued_at=issued_at,
                expires_at=issued_at + capability_ttl,
            ),
            private_key,
        )
    except (CapabilityError, AttributeError, TypeError, ValueError) as exc:
        raise ApiError(
            "CAPABILITY_ISSUE_FAILED",
            "无法签发执行 Capability，请检查密钥配置",
            status_code=503,
        ) from exc

    capability_digest = hashlib.sha256(capability_token.encode("utf-8")).hexdigest()
    correlation_id = getattr(request.state, "request_id", str(uuid4()))
    execution = ExecutionRun(
        project_id=preparation.project_id,
        preparation_id=preparation.id,
        pipeline_version_id=version.id,
        created_by=current_user.id,
        status=ExecutionRunStatus.QUEUED.value,
        idempotency_key=idempotency_key,
        artifact_digest=version.artifact_digest,
        input_fingerprint=preparation.input_fingerprint,
        capability_token_digest=capability_digest,
        correlation_id=correlation_id,
        committed_at=now,
    )
    session.add(execution)
    await session.flush()
    outbox = OutboxEvent(
        project_id=preparation.project_id,
        aggregate_type="execution_run",
        aggregate_id=execution.id,
        event_type="execution.submit",
        deduplication_key=f"execution.submit:{execution.id}",
        status=OutboxEventStatus.PENDING.value,
        payload_json={
            "schema_version": "execution.submit.v1",
            "execution_run_id": str(execution.id),
            "preparation_id": str(preparation.id),
            "pipeline_version_id": str(version.id),
            "artifact_digest": version.artifact_digest,
            "input_fingerprint": preparation.input_fingerprint,
            "engine": "seatunnel",
            "resource_scope": dict(preparation.resource_scope),
            "runtime_budget": dict(preparation.runtime_budget),
        },
        capability_token=capability_token,
    )
    session.add(outbox)
    preparation.status = PreparationStatus.COMMITTED.value
    await append_evidence_event(
        session,
        project_id=preparation.project_id,
        event_type="execution.committed",
        resource_type="execution_run",
        resource_id=execution.id,
        actor_id=current_user.id,
        correlation_id=correlation_id,
        payload={
            "execution_run_id": str(execution.id),
            "preparation_id": str(preparation.id),
            "artifact_digest": version.artifact_digest,
            "input_fingerprint": preparation.input_fingerprint,
            "capability_token_digest": capability_digest,
        },
    )
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        duplicate = await session.scalar(
            select(ExecutionRun).where(ExecutionRun.preparation_id == preparation.id)
        )
        if duplicate is not None:
            duplicate_outbox = await session.scalar(
                select(OutboxEvent).where(
                    OutboxEvent.aggregate_id == duplicate.id,
                    OutboxEvent.event_type == "execution.submit",
                )
            )
            if duplicate_outbox is not None:
                response.status_code = status.HTTP_200_OK
                return CommitResponse(
                    execution_run_id=duplicate.id,
                    preparation_id=preparation.id,
                    status=ExecutionRunStatus(duplicate.status),
                    idempotency_key=duplicate.idempotency_key,
                    outbox_event_id=duplicate_outbox.id,
                    capability_token_digest=duplicate.capability_token_digest,
                    committed_at=duplicate.committed_at,
                    idempotent=True,
                )
        raise ApiError("COMMIT_CONFLICT", "Commit 并发冲突，请重试", status_code=409) from exc
    await session.refresh(execution)
    await session.refresh(outbox)
    return CommitResponse(
        execution_run_id=execution.id,
        preparation_id=preparation.id,
        status=ExecutionRunStatus(execution.status),
        idempotency_key=execution.idempotency_key,
        outbox_event_id=outbox.id,
        capability_token_digest=execution.capability_token_digest,
        committed_at=execution.committed_at,
    )


@router.get("/execution-runs/{execution_id}", response_model=ExecutionRunResponse)
async def get_execution_run(
    execution_id: UUID,
    current_user: CurrentUser,
    session: DbSession,
) -> ExecutionRunResponse:
    """查询项目成员可见的执行事实，隐藏内部 Capability 和连接凭据。"""
    execution = await session.get(ExecutionRun, execution_id)
    if execution is None:
        raise ApiError("EXECUTION_NOT_FOUND", "ExecutionRun 不存在", status_code=404)
    await require_project_role(
        execution.project_id,
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
    return _execution_response(execution)


@router.get("/projects/{project_id}/execution-runs", response_model=list[ExecutionRunResponse])
async def list_execution_runs(
    project_id: UUID,
    current_user: CurrentUser,
    session: DbSession,
) -> list[ExecutionRunResponse]:
    """查询项目执行事实，供运行中心按最新提交时间展示。"""
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
    executions = await session.scalars(
        select(ExecutionRun)
        .where(ExecutionRun.project_id == project_id)
        .order_by(ExecutionRun.created_at.desc())
    )
    return [_execution_response(execution) for execution in executions]


async def _queue_execution_action(
    session,
    request: Request,
    execution: ExecutionRun,
    current_user,
    *,
    event_type: str,
    tool: str,
    payload: dict[str, object],
) -> OutboxEvent:
    """为一个执行动作签发单次 Capability 并写入 Transactional Outbox。"""
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
        return existing
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
        raise ApiError("PREPARATION_NOT_FOUND", "Preparation 不存在", status_code=409)
    settings = request.app.state.settings
    try:
        token = issue_execution_action_capability(
            private_key_path=settings.capability_private_key_path,
            subject=current_user.id,
            tool=tool,
            environment=str(preparation.facts_json.get("environment", "development")),
            preparation_id=execution.preparation_id,
            artifact_digest=execution.artifact_digest,
            ttl_seconds=settings.capability_ttl_seconds,
        )
    except (CapabilityError, AttributeError, TypeError, ValueError) as exc:
        raise ApiError(
            "CAPABILITY_ISSUE_FAILED", "无法签发执行动作 Capability", status_code=503
        ) from exc
    event = OutboxEvent(
        id=uuid4(),
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
        actor_id=current_user.id,
        correlation_id=execution.correlation_id,
        payload={"reason": payload.get("reason", ""), "event_id": str(event.id)},
    )
    await session.flush()
    return event


@router.post(
    "/execution-runs/{execution_id}/cancel", response_model=ExecutionRunResponse, status_code=202
)
async def cancel_execution_run(
    execution_id: UUID,
    payload: ExecutionActionRequest,
    request: Request,
    current_user: CurrentUser,
    session: DbSession,
) -> ExecutionRunResponse:
    """登记取消请求，实际引擎调用仍由 Tool Broker 执行。"""
    execution = await session.scalar(
        select(ExecutionRun).where(ExecutionRun.id == execution_id).with_for_update()
    )
    if execution is None:
        raise ApiError("EXECUTION_NOT_FOUND", "ExecutionRun 不存在", status_code=404)
    await require_project_role(execution.project_id, current_user, session, {ProjectRole.OPERATOR})
    if execution.status in {
        ExecutionRunStatus.CANCELLED.value,
        ExecutionRunStatus.SUCCEEDED.value,
        ExecutionRunStatus.FAILED.value,
    }:
        return _execution_response(execution)
    execution.status = ExecutionRunStatus.CANCEL_REQUESTED.value
    await _queue_execution_action(
        session,
        request,
        execution,
        current_user,
        event_type="execution.cancel",
        tool="seatunnel.cancel",
        payload={"reason": payload.reason},
    )
    await session.commit()
    await session.refresh(execution)
    return _execution_response(execution)


@router.post(
    "/execution-runs/{execution_id}/rollback", response_model=ExecutionRunResponse, status_code=202
)
async def rollback_execution_run(
    execution_id: UUID,
    payload: ExecutionActionRequest,
    request: Request,
    current_user: CurrentUser,
    session: DbSession,
) -> ExecutionRunResponse:
    """登记影子表清理和目标恢复请求，实际回滚由 Tool Broker 执行。"""
    execution = await session.scalar(
        select(ExecutionRun).where(ExecutionRun.id == execution_id).with_for_update()
    )
    if execution is None:
        raise ApiError("EXECUTION_NOT_FOUND", "ExecutionRun 不存在", status_code=404)
    await require_project_role(execution.project_id, current_user, session, {ProjectRole.OPERATOR})
    if execution.rollback_status == RollbackStatus.COMPLETED.value:
        return _execution_response(execution)
    if execution.status not in {
        ExecutionRunStatus.SUCCEEDED.value,
        ExecutionRunStatus.FAILED.value,
        ExecutionRunStatus.CANCELLED.value,
    }:
        raise ApiError("EXECUTION_NOT_ROLLBACKABLE", "只有终态执行才能回滚", status_code=409)
    if not execution.engine_job_id:
        raise ApiError("ENGINE_JOB_ID_MISSING", "执行事实缺少引擎作业 ID", status_code=409)
    execution.rollback_status = RollbackStatus.REQUESTED.value
    await _queue_execution_action(
        session,
        request,
        execution,
        current_user,
        event_type="execution.rollback",
        tool="seatunnel.rollback",
        payload={
            "reason": payload.reason,
            "shadow_table": execution.shadow_table,
            "error_table": execution.error_table,
            **{
                key: value
                for key, value in execution.metrics_json.items()
                if key
                in {
                    "target_connection_id",
                    "target_host",
                    "target_port",
                    "target_secret_ref",
                    "target_database",
                    "target_table",
                }
            },
        },
    )
    await session.commit()
    await session.refresh(execution)
    return _execution_response(execution)


@router.get(
    "/execution-runs/{execution_id}/supervision",
    response_model=list[RuntimeSupervisionSnapshotResponse],
)
async def list_execution_supervision(
    execution_id: UUID,
    current_user: CurrentUser,
    session: DbSession,
) -> list[RuntimeSupervisionSnapshotResponse]:
    """查询运行监督快照，供运行中心展示预算和质量变化。"""
    execution = await session.get(ExecutionRun, execution_id)
    if execution is None:
        raise ApiError("EXECUTION_NOT_FOUND", "ExecutionRun 不存在", status_code=404)
    await require_project_role(
        execution.project_id,
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
    rows = await session.scalars(
        select(RuntimeSupervisionSnapshot)
        .where(RuntimeSupervisionSnapshot.execution_run_id == execution.id)
        .order_by(RuntimeSupervisionSnapshot.created_at)
    )
    return [
        RuntimeSupervisionSnapshotResponse(
            id=row.id,
            execution_run_id=row.execution_run_id,
            engine_status=row.engine_status,
            decision=row.decision,
            observed_metrics=dict(row.observed_metrics),
            exceeded_budget_fields=list(row.exceeded_budget_fields),
            detail=row.detail,
            created_at=row.created_at,
        )
        for row in rows
    ]


@router.get(
    "/execution-runs/{execution_id}/quality",
    response_model=ExecutionQualityResultResponse | None,
)
async def get_execution_quality(
    execution_id: UUID,
    current_user: CurrentUser,
    session: DbSession,
) -> ExecutionQualityResultResponse | None:
    """查询执行最终质量报告，报告为空表示引擎尚未进入终态。"""
    execution = await session.get(ExecutionRun, execution_id)
    if execution is None:
        raise ApiError("EXECUTION_NOT_FOUND", "ExecutionRun 不存在", status_code=404)
    await require_project_role(
        execution.project_id,
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
    result = await session.scalar(
        select(ExecutionQualityResult).where(
            ExecutionQualityResult.execution_run_id == execution.id
        )
    )
    if result is None:
        return None
    return ExecutionQualityResultResponse(
        id=result.id,
        execution_run_id=result.execution_run_id,
        status=result.status,
        input_records=result.input_records,
        output_records=result.output_records,
        rejected_records=result.rejected_records,
        rejection_rate=result.rejection_rate,
        report=dict(result.report_json),
        shadow_table=result.shadow_table,
        error_table=result.error_table,
        created_at=result.created_at,
    )
