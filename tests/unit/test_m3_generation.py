"""阶段 3 Agent 生成、Provider 和确定性门禁测试。"""

from uuid import uuid4

import httpx
import pytest
import respx

from etl_agent.config import Settings
from etl_agent.domain.generation import (
    GenerationRequest,
    ProfileContext,
    RuntimeBudget,
    cap_runtime_budget,
)
from etl_agent.infrastructure.llm import (
    FakeLLMProvider,
    LLMProviderError,
    OpenAICompatibleProvider,
)
from etl_agent.workflows.graph import run_generation_workflow


def _request() -> tuple[GenerationRequest, dict[str, object]]:
    """构造一份可通过门禁的脱敏订单 Profile 和候选。"""
    source_id, target_id = uuid4(), uuid4()
    source_connection, target_connection = uuid4(), uuid4()
    request = GenerationRequest(
        business_request="同步订单",
        source_profiles=[
            ProfileContext(
                profile_id=source_id,
                connection_id=source_connection,
                fingerprint="a" * 64,
                fields=["id", "amount"],
            )
        ],
        target_profiles=[
            ProfileContext(
                profile_id=target_id,
                connection_id=target_connection,
                fingerprint="b" * 64,
                fields=["id", "amount"],
            )
        ],
    )
    candidate: dict[str, object] = {
        "schema_version": "etl-plan.v1",
        "source": {"connection_id": str(source_connection), "profile_id": str(source_id)},
        "target": {"connection_id": str(target_connection), "profile_id": str(target_id)},
        "field_mappings": [
            {"source_field": "id", "target_field": "id"},
            {"source_field": "amount", "target_field": "amount", "transform": "cast"},
        ],
        "transforms": [],
        "quality_contract": {
            "required_fields": ["id"],
            "max_rejection_rate": 0.05,
            "error_table_suffix": "__errors",
        },
        "runtime_budget": request.max_runtime_budget.model_dump(mode="json"),
        "hocon": "env { parallelism = 1 }",
    }
    return request, candidate


@pytest.mark.asyncio
async def test_valid_candidate_completes_and_records_nodes() -> None:
    """验证合法候选经过完整节点链路后才返回 completed。"""
    request, candidate = _request()
    result = await run_generation_workflow(request, FakeLLMProvider([candidate]))

    assert result.status == "completed"
    assert result.plan is not None
    assert result.repair_count == 0
    assert result.node_trace == [
        "IntentParseNode",
        "ProfileEnrichmentNode",
        "CandidateGenerationNode",
        "SchemaValidationNode",
        "HoconCompileNode",
        "DeterministicGateNode",
    ]


@pytest.mark.asyncio
async def test_missing_profiles_interrupts_without_calling_provider() -> None:
    """验证缺少源/目标 Profile 时进入澄清中断且不触发 LLM。"""
    provider = FakeLLMProvider([])
    request = GenerationRequest(business_request="增量同步")
    result = await run_generation_workflow(request, provider)

    assert result.status == "needs_clarification"
    assert {question.key for question in result.clarification_questions} == {
        "source_profile",
        "target_profile",
        "incremental_field",
    }
    assert result.node_trace == ["IntentParseNode", "HumanInterruptNode"]
    assert provider.calls == 0


@pytest.mark.asyncio
async def test_invalid_candidate_is_repaired_once_then_rejected() -> None:
    """验证未知字段和错误枚举不会被接受，修复次数受上限约束。"""
    request, candidate = _request()
    invalid = {
        **candidate,
        "unexpected": "ignore",
        "field_mappings": [{"source_field": "id", "target_field": "id", "transform": "shell"}],
    }
    provider = FakeLLMProvider([invalid, invalid])
    result = await run_generation_workflow(request, provider)

    assert result.status == "validation_failed"
    assert result.plan is None
    assert result.repair_count == 1
    assert provider.calls == 2
    assert len(result.attempts) == 2
    assert any(issue.code == "SCHEMA_INVALID" for issue in result.validation_issues)


@pytest.mark.asyncio
async def test_model_cannot_expand_runtime_budget() -> None:
    """验证模型生成的预算超过服务端上限时被确定性门禁拒绝。"""
    request, candidate = _request()
    candidate["runtime_budget"] = {
        **request.max_runtime_budget.model_dump(mode="json"),
        "max_input_records": request.max_runtime_budget.max_input_records + 1,
    }
    result = await run_generation_workflow(request, FakeLLMProvider([candidate]), max_repairs=0)

    assert result.status == "validation_failed"
    assert any(issue.code == "BUDGET_EXCEEDED" for issue in result.validation_issues)


def test_runtime_budget_is_capped_by_server_limit() -> None:
    """验证 API 使用的预算裁剪函数不会接受超过服务端上限的值。"""
    capped = cap_runtime_budget(
        RuntimeBudget(max_input_records=99_999_999, max_output_bytes=99_999_999_999),
        RuntimeBudget(),
    )

    assert capped.max_input_records == RuntimeBudget().max_input_records
    assert capped.max_output_bytes == RuntimeBudget().max_output_bytes


@pytest.mark.asyncio
async def test_invalid_hocon_is_rejected() -> None:
    """验证 HOCON 语法错误不能冻结 EtlPlan。"""
    request, candidate = _request()
    candidate["hocon"] = "env { parallelism = [ }"
    result = await run_generation_workflow(request, FakeLLMProvider([candidate]), max_repairs=0)

    assert result.status == "validation_failed"
    assert any(issue.code == "HOCON_INVALID" for issue in result.validation_issues)


@pytest.mark.asyncio
@respx.mock
async def test_openai_compatible_provider_returns_structured_json_without_logging_secret() -> None:
    """验证 OpenAI-compatible Provider 使用 Bearer 请求并解析结构化 JSON。"""
    route = respx.post("https://bailian.example/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"ok": true}'}}]},
        )
    )
    settings = Settings(
        _env_file=None,
        llm_base_url="https://bailian.example/v1",
        llm_api_key="test-secret-key",
        llm_model="qwen-test",
        llm_request_timeout_seconds=5,
        llm_max_retries=0,
    )
    request, _ = _request()
    request = request.model_copy(
        update={
            "source_profiles": [
                request.source_profiles[0].model_copy(
                    update={"redacted_sample": {"password": "should-not-leak"}}
                )
            ]
        }
    )
    response = await OpenAICompatibleProvider(settings).generate_structured(
        request, {"type": "object"}
    )

    assert response.payload == {"ok": True}
    assert route.called
    assert route.calls[0].request.headers["Authorization"] == "Bearer test-secret-key"
    assert b"should-not-leak" not in route.calls[0].request.content


@pytest.mark.asyncio
@respx.mock
async def test_provider_retries_transient_upstream_failure() -> None:
    """验证 503 只触发有限重试，并在恢复后返回结构化结果。"""
    route = respx.post("https://bailian.example/v1/chat/completions").mock(
        side_effect=[
            httpx.Response(503, json={"error": "busy"}),
            httpx.Response(
                200,
                json={"choices": [{"message": {"content": '{"ok": true}'}}]},
            ),
        ]
    )
    settings = Settings(
        _env_file=None,
        llm_base_url="https://bailian.example/v1",
        llm_api_key="test-secret-key",
        llm_model="qwen-test",
        llm_request_timeout_seconds=5,
        llm_max_retries=1,
    )
    request, _ = _request()

    response = await OpenAICompatibleProvider(settings).generate_structured(
        request, {"type": "object"}
    )

    assert response.payload == {"ok": True}
    assert response.attempts == 2
    assert route.call_count == 2


@pytest.mark.asyncio
@respx.mock
async def test_provider_rejects_oversized_prompt_before_network_call() -> None:
    """验证脱敏 Prompt 超过上限时不会发起远端请求。"""
    route = respx.post("https://bailian.example/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": []})
    )
    settings = Settings(
        _env_file=None,
        llm_base_url="https://bailian.example/v1",
        llm_api_key="test-secret-key",
        llm_model="qwen-test",
        llm_max_prompt_bytes=64,
    )
    request, _ = _request()

    with pytest.raises(LLMProviderError) as error:
        await OpenAICompatibleProvider(settings).generate_structured(request, {"type": "object"})

    assert error.value.code == "LLM_PROMPT_TOO_LARGE"
    assert not route.called
