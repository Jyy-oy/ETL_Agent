"""M5.1 SeaTunnel Adapter 和 Worker 入口边界测试。"""

import httpx
import pytest

from etl_agent.workers.engine import (
    EngineError,
    EngineJobStatus,
    SeaTunnelAdapter,
    build_seatunnel_submit_payload,
)


def test_seatunnel_payload_rejects_missing_hocon() -> None:
    """验证没有冻结 HOCON 制品时不会发起引擎副作用。"""
    with pytest.raises(EngineError, match="HOCON"):
        build_seatunnel_submit_payload({"execution_run_id": "run-1"})


@pytest.mark.asyncio
async def test_seatunnel_adapter_submit_status_and_cancel() -> None:
    """验证 Adapter 将提交、状态和取消响应映射到稳定端口模型。"""
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/submit":
            return httpx.Response(200, json={"job_id": "job-1"})
        if request.method == "GET" and request.url.path == "/jobs/job-1":
            return httpx.Response(200, json={"status": "running"})
        if request.method == "POST" and request.url.path == "/jobs/job-1/cancel":
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
