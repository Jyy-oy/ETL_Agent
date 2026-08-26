"""Prepare 阶段 API 请求和响应模型。"""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from etl_agent.domain.generation import RuntimeBudget
from etl_agent.harness.models import (
    ApprovalDecision,
    ApprovalStatus,
    DataClassification,
    PreparationStatus,
    RiskLevel,
    ToolIntent,
)
from etl_agent.infrastructure.models import ExecutionRunStatus


class PreparationCreate(BaseModel):
    """声明要冻结的工具意图、环境、数据分级和受管预算。"""

    tool_intent: ToolIntent = ToolIntent.ETL_EXECUTE
    environment: str = Field(default="development", min_length=1, max_length=32)
    data_classification: DataClassification = DataClassification.INTERNAL
    writes_target: bool = True
    runtime_budget: RuntimeBudget = Field(default_factory=RuntimeBudget)
    resource_scope: dict[str, Any] = Field(default_factory=dict, max_length=50)


class PreparationResponse(BaseModel):
    """返回冻结 Preparation 事实和后续审批所需的最小摘要。"""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    pipeline_version_id: UUID
    created_by: UUID
    status: PreparationStatus
    risk_level: RiskLevel
    policy_version: str
    input_fingerprint: str
    required_roles: list[str]
    resource_scope: dict[str, Any]
    runtime_budget: RuntimeBudget
    facts: dict[str, Any]
    approval_requests: list["ApprovalRequestResponse"] = Field(default_factory=list)
    expires_at: datetime
    created_at: datetime
    updated_at: datetime


class ApprovalDecisionRequest(BaseModel):
    """提交单个 Checker 审批槽的批准或拒绝决定。"""

    decision: ApprovalDecision
    comment: str | None = Field(default=None, max_length=1000)


class ApprovalRequestResponse(BaseModel):
    """返回审批槽、当前状态和已决策主体的最小摘要。"""

    id: UUID
    project_id: UUID
    preparation_id: UUID
    required_role: str
    status: ApprovalStatus
    decision: ApprovalDecision | None
    approver_id: UUID | None
    comment: str | None
    decided_at: datetime | None
    created_at: datetime
    updated_at: datetime


class CommitResponse(BaseModel):
    """返回 Commit 创建的执行事实和 Outbox 摘要，不暴露 Capability 原文。"""

    execution_run_id: UUID
    preparation_id: UUID
    status: ExecutionRunStatus
    idempotency_key: str
    outbox_event_id: UUID
    capability_token_digest: str
    committed_at: datetime
    idempotent: bool = False


class ExecutionRunResponse(BaseModel):
    """返回执行查询所需的脱敏状态、摘要和错误信息。"""

    id: UUID
    project_id: UUID
    preparation_id: UUID
    pipeline_version_id: UUID
    status: ExecutionRunStatus
    engine_name: str
    engine_job_id: str | None
    idempotency_key: str
    artifact_digest: str
    input_fingerprint: str
    capability_token_digest: str
    correlation_id: str
    committed_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    metrics: dict[str, Any]
    error_code: str | None
    error_detail: str | None
    created_at: datetime
    updated_at: datetime
