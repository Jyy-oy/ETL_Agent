"""M5.1 SeaTunnel Adapter 和 Worker 入口边界测试。"""

import json

import httpx
import pytest

from etl_agent.workers.engine import (
    EngineError,
    EngineJobStatus,
    SeaTunnelAdapter,
    build_seatunnel_submit_payload,
    normalize_seatunnel_metrics,
    sanitize_engine_detail,
)


def test_seatunnel_payload_rejects_missing_hocon() -> None:
    """验证没有冻结 HOCON 制品时不会发起引擎副作用。"""
    with pytest.raises(EngineError, match="HOCON"):
        build_seatunnel_submit_payload({"execution_run_id": "run-1"})


@pytest.mark.asyncio
async def test_seatunnel_adapter_submit_status_and_cancel() -> None:
    """验证 Adapter 将提交、状态和取消响应映射到稳定端口模型。"""

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/submit-job":
            assert request.headers["content-type"] == "text/plain"
            assert request.content.startswith(b"env")
            return httpx.Response(200, json={"job_id": "job-1"})
        if request.method == "GET" and request.url.path == "/job-info/job-1":
            return httpx.Response(200, json={"status": "RUNNING", "metrics": {"input_records": 1}})
        if request.method == "POST" and request.url.path == "/stop-job":
            assert json.loads(request.content) == {"jobId": "job-1"}
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = SeaTunnelAdapter("http://zeta", client=client)
    try:
        job = await adapter.submit({"hocon": "env { parallelism = 1 }"})
        assert job.job_id == "job-1"
        assert (await adapter.get_status(job.job_id)).status is EngineJobStatus.RUNNING
        assert await adapter.cancel(job.job_id) is True
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_seatunnel_adapter_exposes_sanitized_submit_error_detail() -> None:
    """验证提交失败保留上游摘要和状态码，同时不暴露连接凭据。"""

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            500,
            json={"message": "SQL failed: password=plain-secret near BIGINT"},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = SeaTunnelAdapter("http://zeta", client=client)
    try:
        with pytest.raises(EngineError) as error:
            await adapter.submit({"hocon": "env { parallelism = 1 }"})
    finally:
        await client.aclose()
    assert "SeaTunnel HTTP 500" in str(error.value)
    assert "near BIGINT" in str(error.value)
    assert "plain-secret" not in str(error.value)


@pytest.mark.asyncio
async def test_seatunnel_adapter_maps_native_job_status_and_metrics() -> None:
    """验证 SeaTunnel 2.3.10 的 jobStatus 和原生指标会转换为控制面字段。"""

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "jobStatus": "FINISHED",
                "errorMsg": None,
                "createTime": "2026-08-26 02:38:09",
                "finishTime": "2026-08-26 02:38:11",
                "metrics": {
                    "SourceReceivedCount": "32",
                    "SinkWriteCount": "31",
                    "SourceReceivedBytes": "288",
                    "SinkWriteBytes": "280",
                    "TableSourceReceivedCount": {"source": "32"},
                },
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = SeaTunnelAdapter("http://zeta", client=client)
    try:
        result = await adapter.get_status("job-1")
    finally:
        await client.aclose()
    assert result.status is EngineJobStatus.SUCCEEDED
    assert result.metrics == {
        "SourceReceivedCount": "32",
        "SinkWriteCount": "31",
        "SourceReceivedBytes": "288",
        "SinkWriteBytes": "280",
        "TableSourceReceivedCount": {"source": "32"},
        "input_records": 32,
        "output_records": 31,
        "input_bytes": 288,
        "output_bytes": 280,
        "rejected_records": 0,
        "elapsed_seconds": 2,
    }


def test_normalize_seatunnel_metrics_supports_nested_connector_counts() -> None:
    """验证只有按表名返回的指标时仍能得到稳定的质量字段。"""
    metrics = normalize_seatunnel_metrics(
        {
            "metrics": {
                "TableSourceReceivedCount": {"a": "2", "b": 3},
                "TableSinkWriteCount": {"a": "4"},
                "TableSourceReceivedBytes": {"a": "20"},
                "TableSinkWriteBytes": {"a": "30"},
            }
        }
    )
    assert metrics["input_records"] == 5
    assert metrics["output_records"] == 4
    assert metrics["input_bytes"] == 20
    assert metrics["output_bytes"] == 30


def test_sanitize_engine_detail_hides_connection_secrets() -> None:
    """验证外部引擎异常中的连接密码和 URI 凭据不会落入监督事实。"""
    detail = sanitize_engine_detail(
        "connect mysql://etl:plain-password@mysql:3306/db password=another-secret token=abc"
    )
    assert detail is not None
    assert "plain-password" not in detail
    assert "another-secret" not in detail
    assert "token=abc" not in detail
    assert "<redacted>" in detail


@pytest.mark.asyncio
async def test_seatunnel_adapter_supports_cleanup_swap_and_rollback() -> None:
    """验证影子表清理、原子切换和回滚统一走可配置动作端点。"""

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path in {
            "/jobs/job-1/cleanup",
            "/jobs/job-1/swap",
            "/jobs/job-1/rollback",
        }:
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = SeaTunnelAdapter("http://zeta", client=client)
    try:
        assert await adapter.cleanup("job-1") is True
        assert await adapter.atomic_swap("job-1", {"shadow_table": "t__shadow"}) is True
        assert await adapter.rollback("job-1", {"shadow_table": "t__shadow"}) is True
    finally:
        await client.aclose()
