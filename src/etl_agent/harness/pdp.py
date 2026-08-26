"""M4.1 确定性 Policy Decision Point。"""

from etl_agent.harness.models import (
    DataClassification,
    PolicyInput,
    RiskDecision,
    RiskLevel,
)

POLICY_VERSION = "pdp-v1"


def decide_policy(policy_input: PolicyInput) -> RiskDecision:
    """根据环境、写入意图、数据分级和预算确定风险及审批槽。"""
    reasons: list[str] = []
    risk = RiskLevel.P0

    if policy_input.writes_target:
        risk = RiskLevel.P1
        reasons.append("目标端写入需要至少一名独立 Checker 审查")
    if policy_input.data_classification in {
        DataClassification.CONFIDENTIAL,
        DataClassification.RESTRICTED,
    }:
        risk = RiskLevel.P2
        reasons.append("机密或受限数据需要数据与安全双重审查")
    if policy_input.data_classification is DataClassification.RESTRICTED:
        risk = RiskLevel.P3
        reasons.append("受限数据按最高风险策略处理")
    if policy_input.environment.lower() in {"production", "prod"}:
        risk = RiskLevel.P3
        reasons.append("生产环境执行需要最高风险审批")
    if policy_input.runtime_budget.max_output_amplification > 10:
        risk = RiskLevel.P3
        reasons.append("输出放大比超过默认预算上限")

    required_roles = {
        RiskLevel.P0: [],
        RiskLevel.P1: ["checker_1"],
        RiskLevel.P2: ["checker_1", "checker_2"],
        RiskLevel.P3: ["checker_1", "checker_2"],
    }[risk]
    if not reasons:
        reasons.append("只读且公开数据操作处于最低风险级别")
    return RiskDecision(
        policy_version=POLICY_VERSION,
        risk_level=risk,
        required_roles=required_roles,
        reasons=reasons,
    )
