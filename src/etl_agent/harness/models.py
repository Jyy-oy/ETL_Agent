"""M4 Harness 的策略输入、风险决策和 Preparation 领域模型。"""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from etl_agent.domain.generation import RuntimeBudget


class HarnessModel(BaseModel):
    """拒绝未定义字段，确保策略输入不会被调用方静默扩展。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ToolIntent(StrEnum):
    """首期 Prepare 支持的受管工具意图。"""

    ETL_EXECUTE = "etl_execute"


class DataClassification(StrEnum):
    """用于确定 PDP 风险的最低数据分级。"""

    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"


class RiskLevel(StrEnum):
    """PDP 输出的稳定风险级别，数值越大表示控制要求越高。"""

    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"


class PreparationStatus(StrEnum):
    """Preparation 的最小生命周期状态。"""

    PREPARED = "prepared"
    APPROVAL_PENDING = "approval_pending"
    APPROVED = "approved"
    COMMITTED = "committed"
    REJECTED = "rejected"
    EXPIRED = "expired"


class ApprovalDecision(StrEnum):
    """Checker 对单个审批槽提交的决定。"""

    APPROVE = "approve"
    REJECT = "reject"


class ApprovalStatus(StrEnum):
    """审批槽的状态，决定只能从 pending 进入终态。"""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class PolicyInput(HarnessModel):
    """PDP 使用的最小确定性策略输入，不包含连接凭据或业务样本。"""

    tool_intent: ToolIntent = ToolIntent.ETL_EXECUTE
    environment: str = Field(default="development", min_length=1, max_length=32)
    data_classification: DataClassification = DataClassification.INTERNAL
    writes_target: bool = True
    runtime_budget: RuntimeBudget = Field(default_factory=RuntimeBudget)


class RiskDecision(HarnessModel):
    """PDP 输出的风险、审批槽和可解释策略原因。"""

    policy_version: str = Field(min_length=1, max_length=64)
    risk_level: RiskLevel
    required_roles: list[str] = Field(default_factory=list, max_length=10)
    allowed: bool = True
    reasons: list[str] = Field(default_factory=list, max_length=20)


class PreparationFacts(HarnessModel):
    """冻结在 Preparation 中的执行事实，供后续 Approve/Commit 复核。"""

    schema_version: str = Field(default="preparation.v1", pattern=r"^preparation\.v1$")
    tool_intent: ToolIntent
    environment: str = Field(min_length=1, max_length=32)
    data_classification: DataClassification
    writes_target: bool = True
    risk_level: RiskLevel
    policy_version: str = Field(min_length=1, max_length=64)
    required_roles: list[str] = Field(default_factory=list, max_length=10)
    runtime_budget: RuntimeBudget
    resource_scope: dict[str, Any] = Field(default_factory=dict)
    input_fingerprint: str = Field(min_length=64, max_length=64)
