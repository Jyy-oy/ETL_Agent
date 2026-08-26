"""M5 数据质量分流和运行预算的确定性规则。"""

from dataclasses import dataclass
from typing import Any

from etl_agent.domain.generation import QualityContract, RuntimeBudget


@dataclass(frozen=True, slots=True)
class QualityAssessment:
    """保存一次数据质量评估的脱敏结果。"""

    status: str
    input_records: int
    output_records: int
    rejected_records: int
    rejection_rate: float
    missing_required_fields: tuple[str, ...]
    error_code: str | None
    detail: str | None

    def as_dict(self) -> dict[str, Any]:
        """将评估结果转换为可安全写入 JSON 的字典。"""
        return {
            "status": self.status,
            "input_records": self.input_records,
            "output_records": self.output_records,
            "rejected_records": self.rejected_records,
            "rejection_rate": self.rejection_rate,
            "missing_required_fields": list(self.missing_required_fields),
            "error_code": self.error_code,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class BudgetAssessment:
    """保存一次运行预算评估的决策和超限字段。"""

    decision: str
    exceeded: tuple[str, ...]
    detail: str | None

    def as_dict(self) -> dict[str, Any]:
        """将预算评估转换为稳定 JSON 结构。"""
        return {
            "decision": self.decision,
            "exceeded": list(self.exceeded),
            "detail": self.detail,
        }


def _non_negative_int(metrics: dict[str, Any], key: str) -> int:
    """读取引擎指标中的非负整数，非法值按零处理。"""
    try:
        return max(0, int(metrics.get(key, 0)))
    except (TypeError, ValueError):
        return 0


def assess_quality(contract: QualityContract, metrics: dict[str, Any]) -> QualityAssessment:
    """按冻结 QualityContract 判断通过、分流或失败，不执行任何外部操作。"""
    input_records = _non_negative_int(metrics, "input_records")
    output_records = _non_negative_int(metrics, "output_records")
    rejected_records = _non_negative_int(metrics, "rejected_records")
    total = max(input_records, output_records + rejected_records)
    rejection_rate = rejected_records / total if total else 0.0
    raw_missing = metrics.get("missing_required_fields", [])
    missing = (
        tuple(sorted({str(item) for item in raw_missing})) if isinstance(raw_missing, list) else ()
    )
    missing = tuple(field for field in missing if field in set(contract.required_fields))
    if missing:
        return QualityAssessment(
            "failed",
            input_records,
            output_records,
            rejected_records,
            rejection_rate,
            missing,
            "REQUIRED_FIELD_MISSING",
            "目标表缺少质量契约要求的字段",
        )
    if rejection_rate > contract.max_rejection_rate:
        return QualityAssessment(
            "rejected",
            input_records,
            output_records,
            rejected_records,
            rejection_rate,
            (),
            "REJECTION_RATE_EXCEEDED",
            "错误数据比例超过质量契约阈值",
        )
    return QualityAssessment(
        "passed",
        input_records,
        output_records,
        rejected_records,
        rejection_rate,
        (),
        None,
        None,
    )


def assess_budget(budget: RuntimeBudget, metrics: dict[str, Any]) -> BudgetAssessment:
    """比较运行指标和冻结预算，返回继续、预警或硬中断决策。"""
    observed = {
        "max_input_records": _non_negative_int(metrics, "input_records"),
        "max_output_bytes": _non_negative_int(metrics, "output_bytes"),
        "max_runtime_seconds": _non_negative_int(metrics, "elapsed_seconds"),
    }
    limits = {
        "max_input_records": budget.max_input_records,
        "max_output_bytes": budget.max_output_bytes,
        "max_runtime_seconds": budget.max_runtime_seconds,
    }
    exceeded = tuple(name for name, value in observed.items() if value > limits[name])
    input_records = observed["max_input_records"]
    output_records = _non_negative_int(metrics, "output_records")
    amplification = output_records / input_records if input_records else 0.0
    if amplification > budget.max_output_amplification:
        exceeded += ("max_output_amplification",)
    rejected = _non_negative_int(metrics, "rejected_records")
    rejection_rate = rejected / input_records if input_records else 0.0
    if rejection_rate > budget.max_rejection_rate:
        exceeded += ("max_rejection_rate",)
    if exceeded:
        return BudgetAssessment("hard_stop", exceeded, "运行指标超过冻结预算")
    return BudgetAssessment("continue", (), None)
