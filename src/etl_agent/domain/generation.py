"""阶段 3 的 Agent 生成领域模型。

这些模型是 LLM 输出和控制面事实之间的边界。模型输出必须先通过
Pydantic 结构校验和后续确定性门禁，才允许进入 PipelineVersion。
"""

from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class GenerationModel(BaseModel):
    """拒绝未知字段，避免模型通过额外字段注入未定义控制指令。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class TransformOperation(StrEnum):
    """首期允许的有限转换操作集合。"""

    CAST = "cast"
    RENAME = "rename"
    MASK = "mask"
    FILTER = "filter"
    FILL_NULL = "fill_null"


class ProfileContext(GenerationModel):
    """提供给 Agent 的脱敏 Profile 摘要，不包含凭据或全量业务数据。"""

    profile_id: UUID
    connection_id: UUID
    fingerprint: str = Field(min_length=64, max_length=64)
    fields: list[str] = Field(default_factory=list, max_length=500)
    redacted_sample: dict[str, Any] = Field(default_factory=dict)


class ConnectionProfileRef(GenerationModel):
    """EtlPlan 中对源或目标 Profile 的不可变引用。"""

    connection_id: UUID
    profile_id: UUID


class FieldMapping(GenerationModel):
    """声明一个源字段到目标字段的映射。"""

    source_field: str = Field(min_length=1, max_length=256)
    target_field: str = Field(min_length=1, max_length=256)
    transform: TransformOperation | None = None


class TransformRule(GenerationModel):
    """声明一个受限转换及其参数，不允许执行任意脚本。"""

    operation: TransformOperation
    source_fields: list[str] = Field(default_factory=list, max_length=50)
    target_field: str | None = Field(default=None, max_length=256)
    parameters: dict[str, str | int | float | bool] = Field(default_factory=dict)


class QualityContract(GenerationModel):
    """冻结在 ETL 版本中的质量约束。"""

    required_fields: list[str] = Field(default_factory=list, max_length=500)
    max_rejection_rate: float = Field(default=0.05, ge=0, le=1)
    error_table_suffix: str = Field(default="__errors", pattern=r"^[a-zA-Z_][a-zA-Z0-9_]{0,63}$")


class RuntimeBudget(GenerationModel):
    """受管运行预算，所有值均由服务端上限约束。"""

    max_input_records: int = Field(default=10_000_000, gt=0)
    max_output_bytes: int = Field(default=10_737_418_240, gt=0)
    max_runtime_seconds: int = Field(default=3_600, gt=0)
    max_output_amplification: float = Field(default=10, gt=0)
    max_rejection_rate: float = Field(default=0.05, ge=0, le=1)


def cap_runtime_budget(requested: RuntimeBudget, maximum: RuntimeBudget) -> RuntimeBudget:
    """按服务端上限裁剪请求预算，避免客户端或模型扩大资源范围。"""
    return RuntimeBudget(
        max_input_records=min(requested.max_input_records, maximum.max_input_records),
        max_output_bytes=min(requested.max_output_bytes, maximum.max_output_bytes),
        max_runtime_seconds=min(requested.max_runtime_seconds, maximum.max_runtime_seconds),
        max_output_amplification=min(
            requested.max_output_amplification, maximum.max_output_amplification
        ),
        max_rejection_rate=min(requested.max_rejection_rate, maximum.max_rejection_rate),
    )


class EtlPlan(GenerationModel):
    """结构化 ETL 设计候选，验证通过后才可冻结为 PipelineVersion。"""

    schema_version: str = Field(default="etl-plan.v1", pattern=r"^etl-plan\.v1$")
    source: ConnectionProfileRef
    target: ConnectionProfileRef
    field_mappings: list[FieldMapping] = Field(min_length=1, max_length=500)
    transforms: list[TransformRule] = Field(default_factory=list, max_length=500)
    quality_contract: QualityContract = Field(default_factory=QualityContract)
    runtime_budget: RuntimeBudget = Field(default_factory=RuntimeBudget)
    hocon: str = Field(min_length=1, max_length=1_000_000)

    @model_validator(mode="after")
    def validate_mapping_targets(self) -> "EtlPlan":
        """拒绝重复目标字段，避免后续生成配置产生歧义覆盖。"""
        targets = [mapping.target_field for mapping in self.field_mappings]
        if len(targets) != len(set(targets)):
            raise ValueError("field_mappings 的 target_field 不得重复")
        return self

    @field_validator("hocon")
    @classmethod
    def reject_empty_hocon(cls, value: str) -> str:
        """确保候选至少包含一段待编译的 HOCON 文本。"""
        if not value.strip():
            raise ValueError("hocon 不能为空")
        return value


class GenerationRequest(GenerationModel):
    """Agent 生成请求及经脱敏的 Profile 上下文。"""

    business_request: str = Field(min_length=1, max_length=20_000)
    source_profiles: list[ProfileContext] = Field(default_factory=list, max_length=50)
    target_profiles: list[ProfileContext] = Field(default_factory=list, max_length=50)
    max_runtime_budget: RuntimeBudget = Field(default_factory=RuntimeBudget)
    prompt_version: str = Field(default="etl-plan-v1", min_length=1, max_length=64)
    answers: dict[str, str] = Field(default_factory=dict)


class ClarificationQuestion(GenerationModel):
    """需要 Maker 补充的缺参问题。"""

    key: str = Field(min_length=1, max_length=64)
    question: str = Field(min_length=1, max_length=500)
    required: bool = True


class ValidationIssue(GenerationModel):
    """确定性校验失败的稳定错误描述。"""

    code: str = Field(min_length=1, max_length=64)
    message: str = Field(min_length=1, max_length=500)
    path: list[str | int] = Field(default_factory=list, max_length=20)


class GenerationResult(GenerationModel):
    """工作流对外返回的可持久化结果，不含完整 Prompt 或 Secret。"""

    status: str
    plan: EtlPlan | None = None
    clarification_questions: list[ClarificationQuestion] = Field(default_factory=list)
    validation_issues: list[ValidationIssue] = Field(default_factory=list)
    repair_count: int = Field(default=0, ge=0)
    node_trace: list[str] = Field(default_factory=list)
    attempts: list[dict[str, Any]] = Field(default_factory=list)
    provider: str | None = None
    model: str | None = None
    error_code: str | None = None
