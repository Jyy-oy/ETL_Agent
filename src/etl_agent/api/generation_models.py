"""Pipeline 和 Agent 生成 API 契约模型。"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from etl_agent.domain.generation import (
    EtlPlan,
    ProfileContext,
    RuntimeBudget,
    ValidationIssue,
)
from etl_agent.infrastructure.models import AgentRunStatus, PipelineStatus, PipelineVersionStatus


class PipelineCreate(BaseModel):
    """创建项目 Pipeline 的基本信息。"""

    project_id: UUID
    code: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$")
    name: str = Field(min_length=1, max_length=256)


class PipelineResponse(BaseModel):
    """Pipeline 基本信息响应。"""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    code: str
    name: str
    status: PipelineStatus
    created_at: datetime
    updated_at: datetime


class PipelineVersionCreate(BaseModel):
    """创建可生成的草稿版本。"""

    created_by: UUID | None = None


class PipelineVersionResponse(BaseModel):
    """PipelineVersion 状态和不可变摘要响应。"""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    pipeline_id: UUID
    version_number: int
    status: PipelineVersionStatus
    immutable: bool
    artifact_digest: str | None
    created_by: UUID
    created_at: datetime
    updated_at: datetime


class GenerationStartRequest(BaseModel):
    """提交自然语言需求和受控 Profile 引用，Profile 内容由服务端读取。"""

    business_request: str = Field(min_length=1, max_length=20_000)
    source_profile_ids: list[UUID] = Field(min_length=1, max_length=50)
    target_profile_ids: list[UUID] = Field(min_length=1, max_length=50)
    max_runtime_budget: RuntimeBudget = Field(default_factory=RuntimeBudget)
    prompt_version: str = Field(default="etl-plan-v1", min_length=1, max_length=64)
    thread_id: str | None = Field(default=None, min_length=1, max_length=128)


class GenerationAnswerRequest(BaseModel):
    """提交澄清问题答案，键和值均限制为可审计的短文本。"""

    answers: dict[str, str] = Field(min_length=1, max_length=50)


class AgentRunResponse(BaseModel):
    """生成运行结果和最小可审计证据。"""

    id: UUID
    thread_id: str
    status: AgentRunStatus
    pipeline_version_id: UUID
    repair_count: int
    node_trace: list[str]
    attempts: list[dict[str, object]] = Field(default_factory=list)
    provider: str | None
    model: str | None
    error_code: str | None
    validation_issues: list[ValidationIssue] = Field(default_factory=list)
    plan: EtlPlan | None = None


class PipelineDesignResponse(BaseModel):
    """已冻结版本的 EtlPlan、HOCON 和摘要。"""

    version: PipelineVersionResponse
    etl_plan: EtlPlan
    hocon: str


class ProfileContextResponse(ProfileContext):
    """保留类型别名，便于 OpenAPI 展示脱敏 Profile 摘要。"""
