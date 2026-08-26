"""阶段 3 的显式外部依赖集成测试。

默认全部跳过；只有在配置非生产百炼密钥或 VM Checkpoint 测试开关后才执行。
"""

import asyncio
import sys
from uuid import uuid4

import pytest

from etl_agent.config import Settings
from etl_agent.domain.generation import GenerationRequest, ProfileContext
from etl_agent.infrastructure.llm import FakeLLMProvider, OpenAICompatibleProvider
from etl_agent.workflows.checkpoint import postgres_checkpointer
from etl_agent.workflows.graph import run_generation_workflow

if sys.platform == "win32":
    # psycopg 异步连接不支持 Proactor，集成测试也必须使用 Selector。
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


pytestmark = pytest.mark.integration


def _generation_request(*, answers: dict[str, str] | None = None) -> GenerationRequest:
    """构造只含虚拟字段和脱敏 Profile 的集成测试请求。"""
    source_profile = ProfileContext(
        profile_id=uuid4(),
        connection_id=uuid4(),
        fingerprint="a" * 64,
        fields=["id", "updated_at"],
    )
    target_profile = ProfileContext(
        profile_id=uuid4(),
        connection_id=uuid4(),
        fingerprint="b" * 64,
        fields=["id", "updated_at"],
    )
    return GenerationRequest(
        business_request="增量同步订单",
        source_profiles=[source_profile],
        target_profiles=[target_profile],
        answers=answers or {},
    )


def _candidate_for(request: GenerationRequest) -> dict[str, object]:
    """根据同一请求的 Profile 引用构造可通过门禁的 fake 候选。"""
    source = request.source_profiles[0]
    target = request.target_profiles[0]
    return {
        "schema_version": "etl-plan.v1",
        "source": {
            "connection_id": str(source.connection_id),
            "profile_id": str(source.profile_id),
        },
        "target": {
            "connection_id": str(target.connection_id),
            "profile_id": str(target.profile_id),
        },
        "field_mappings": [
            {"source_field": "id", "target_field": "id"},
            {"source_field": "updated_at", "target_field": "updated_at"},
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


@pytest.mark.asyncio
async def test_postgres_checkpoint_clarification_recovery() -> None:
    """验证 VM PostgreSQL Checkpoint 可保存中断并由同一 thread 恢复。"""
    settings = Settings()
    if not settings.checkpoint_integration_enabled:
        pytest.skip("CHECKPOINT_INTEGRATION_ENABLED=false，未启用外部 Checkpoint 测试")
    first_request = _generation_request()
    resumed_request = first_request.model_copy(
        update={"answers": {"incremental_field": "updated_at"}}
    )
    thread_id = f"m3-integration-{uuid4()}"
    provider = FakeLLMProvider([_candidate_for(resumed_request)])

    async with postgres_checkpointer(settings.langgraph_checkpoint_database_url) as checkpointer:
        interrupted = await run_generation_workflow(
            first_request,
            provider,
            thread_id=thread_id,
            checkpointer=checkpointer,
        )
        resumed = await run_generation_workflow(
            resumed_request,
            provider,
            thread_id=thread_id,
            checkpointer=checkpointer,
        )

    assert interrupted.status == "needs_clarification"
    assert resumed.status == "completed"
    assert interrupted.node_trace[-1] == "HumanInterruptNode"
    assert resumed.plan is not None


@pytest.mark.asyncio
async def test_real_bailian_provider_smoke() -> None:
    """在显式开关打开时验证百炼兼容接口返回 JSON 对象，不打印密钥或业务数据。"""
    settings = Settings()
    if not settings.llm_real_smoke_enabled:
        pytest.skip("LLM_REAL_SMOKE_ENABLED=false，未启用真实百炼测试")
    if not settings.llm_api_key or settings.llm_api_key.startswith("replace-"):
        pytest.skip("未配置非占位 LLM_API_KEY")
    provider = OpenAICompatibleProvider(settings)
    result = await provider.generate_structured(
        _generation_request(),
        {"type": "object", "additionalProperties": True},
    )

    assert isinstance(result.payload, dict)
    assert len(result.response_digest) == 64
    assert result.provider == "openai_compatible"
