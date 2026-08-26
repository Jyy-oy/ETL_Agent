"""M4.4 Commit、Evidence Ledger 和敏感字段边界测试。"""

from uuid import uuid4

from etl_agent.api.preparations import _policy_input_from_preparation
from etl_agent.domain.generation import RuntimeBudget
from etl_agent.harness.ledger import GENESIS_HASH, compute_event_hash, digest_payload
from etl_agent.infrastructure.models import Preparation


def test_evidence_payload_digest_is_order_independent() -> None:
    """验证账本载荷字段顺序变化不会改变摘要。"""
    assert digest_payload({"b": 2, "a": 1}) == digest_payload({"a": 1, "b": 2})


def test_evidence_hash_links_to_previous_event() -> None:
    """验证前序哈希变化会导致当前账本事件哈希变化。"""
    resource_id = uuid4()
    common = {
        "sequence_number": 1,
        "event_type": "execution.committed",
        "resource_type": "execution_run",
        "resource_id": resource_id,
        "actor_id": None,
        "correlation_id": "request-1",
        "payload_digest": "a" * 64,
    }
    first = compute_event_hash(previous_hash=GENESIS_HASH, **common)
    second = compute_event_hash(previous_hash="b" * 64, **common)

    assert first != second


def test_commit_response_never_declares_capability_plaintext() -> None:
    """验证 Commit 响应模型只允许返回 Capability 摘要而非原文。"""
    from etl_agent.api.preparation_models import CommitResponse

    assert "capability_token" not in CommitResponse.model_fields
    assert "capability_token_digest" in CommitResponse.model_fields


def test_commit_reconstructs_read_only_policy_input() -> None:
    """验证 Commit 重建指纹时不会把只读 Preparation 错误当成写入。"""
    preparation = Preparation(
        facts_json={
            "tool_intent": "etl_execute",
            "environment": "development",
            "data_classification": "public",
            "writes_target": False,
        },
        runtime_budget=RuntimeBudget().model_dump(mode="json"),
    )

    assert _policy_input_from_preparation(preparation).writes_target is False
