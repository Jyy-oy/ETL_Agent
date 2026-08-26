"""M4.1 PDP 和 Preparation 事实模型测试。"""

from etl_agent.api.preparation_models import PreparationCreate
from etl_agent.domain.generation import RuntimeBudget
from etl_agent.harness.models import DataClassification, PolicyInput, RiskLevel
from etl_agent.harness.pdp import decide_policy


def test_pdp_requires_one_checker_for_internal_write() -> None:
    """验证内部数据写入至少需要 Checker 1 审批。"""
    decision = decide_policy(PolicyInput())

    assert decision.risk_level == RiskLevel.P1
    assert decision.required_roles == ["checker_1"]


def test_pdp_requires_two_checkers_for_restricted_production() -> None:
    """验证生产环境受限数据提升到 P3 并要求双 Checker。"""
    decision = decide_policy(
        PolicyInput(
            environment="production",
            data_classification=DataClassification.RESTRICTED,
            runtime_budget=RuntimeBudget(max_output_amplification=20),
        )
    )

    assert decision.risk_level == RiskLevel.P3
    assert decision.required_roles == ["checker_1", "checker_2"]
    assert len(decision.reasons) >= 2


def test_pdp_keeps_public_read_only_at_p0() -> None:
    """验证公开数据只读意图不会无故增加审批要求。"""
    decision = decide_policy(
        PolicyInput(data_classification=DataClassification.PUBLIC, writes_target=False)
    )

    assert decision.risk_level == RiskLevel.P0
    assert decision.required_roles == []


def test_prepare_request_defaults_to_controlled_etl_execution() -> None:
    """验证 Prepare 请求默认使用内部数据和服务端运行预算。"""
    payload = PreparationCreate()

    assert payload.tool_intent.value == "etl_execute"
    assert payload.data_classification is DataClassification.INTERNAL
    assert payload.runtime_budget.max_runtime_seconds == 3_600
