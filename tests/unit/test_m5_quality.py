"""M5 质量契约、运行预算和监督决策测试。"""

from etl_agent.domain.generation import QualityContract, RuntimeBudget
from etl_agent.workers.quality import assess_budget, assess_quality


def test_quality_contract_rejects_excessive_bad_rows() -> None:
    """验证错误数据比例超过阈值时进入 rejected 分流。"""
    result = assess_quality(
        QualityContract(max_rejection_rate=0.1),
        {"input_records": 100, "output_records": 80, "rejected_records": 20},
    )

    assert result.status == "rejected"
    assert result.error_code == "REJECTION_RATE_EXCEEDED"
    assert result.rejection_rate == 0.2


def test_quality_contract_requires_declared_fields() -> None:
    """验证目标缺少必填字段时不允许发布影子表。"""
    result = assess_quality(
        QualityContract(required_fields=["customer_id"]),
        {
            "input_records": 10,
            "output_records": 10,
            "rejected_records": 0,
            "missing_required_fields": ["customer_id"],
        },
    )

    assert result.status == "failed"
    assert result.error_code == "REQUIRED_FIELD_MISSING"


def test_runtime_budget_hard_stops_on_record_and_amplification_limits() -> None:
    """验证输入行数和输出放大比越界会生成硬中断决策。"""
    result = assess_budget(
        RuntimeBudget(max_input_records=100, max_output_amplification=2),
        {"input_records": 101, "output_records": 250, "rejected_records": 0},
    )

    assert result.decision == "hard_stop"
    assert "max_input_records" in result.exceeded
    assert "max_output_amplification" in result.exceeded
