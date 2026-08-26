"""SeaTunnel 执行引擎端口与 HTTP 适配器。"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

import httpx


class EngineError(RuntimeError):
    """外部执行引擎不可用或返回结构不符合契约。"""


class EngineJobStatus(StrEnum):
    """执行引擎对外映射的稳定作业状态。"""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class EngineJobRef:
    """保存引擎作业 ID 和不含敏感信息的原始摘要。"""

    job_id: str
    raw: dict[str, Any]


@dataclass(frozen=True, slots=True)
class EngineStatus:
    """保存引擎作业状态和可选的错误摘要。"""

    status: EngineJobStatus
    detail: str | None = None


class ExecutionEngine(Protocol):
    """数据面执行引擎的最小可替换端口。"""

    async def submit(self, payload: dict[str, Any]) -> EngineJobRef: ...

    async def get_status(self, job_id: str) -> EngineStatus: ...

    async def cancel(self, job_id: str) -> bool: ...


def build_seatunnel_submit_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """将内部 Outbox 数据转换为不含 Capability 的 SeaTunnel 提交载荷。"""
    hocon = payload.get("hocon")
    if not isinstance(hocon, str) or not hocon.strip():
        raise EngineError("SeaTunnel 提交载荷缺少 HOCON 作业配置")
    return {
        "schema_version": "seatunnel.submit.v1",
        "job_config": hocon,
        "job_name": str(payload.get("job_name", payload.get("execution_run_id", "etl-job"))),
        "metadata": {
            "execution_run_id": str(payload.get("execution_run_id", "")),
            "preparation_id": str(payload.get("preparation_id", "")),
            "artifact_digest": str(payload.get("artifact_digest", "")),
        },
    }


class SeaTunnelAdapter:
    """通过可配置 Zeta HTTP 路径提交、查询和取消 SeaTunnel 作业。"""

    def __init__(
        self,
        endpoint: str,
        *,
        submit_path: str = "/submit",
        status_path: str = "/jobs/{job_id}",
        cancel_path: str = "/jobs/{job_id}/cancel",
        timeout_seconds: float = 30.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        """保存 Zeta 地址和路径，允许测试注入 HTTP 客户端。"""
        self.endpoint = endpoint.rstrip("/")
        self.submit_path = submit_path
        self.status_path = status_path
        self.cancel_path = cancel_path
        self.timeout_seconds = timeout_seconds
        self._client = client

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """执行一次受超时约束的 HTTP 请求并统一转换连接异常。"""
        owned_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=self.timeout_seconds)
        try:
            return await client.request(method, f"{self.endpoint}{path}", **kwargs)
        except httpx.HTTPError as exc:
            raise EngineError("SeaTunnel Zeta 请求失败") from exc
        finally:
            if owned_client:
                await client.aclose()

    async def submit(self, payload: dict[str, Any]) -> EngineJobRef:
        """提交已由 Tool Broker 清洗过的 HOCON 作业，并提取引擎作业 ID。"""
        request_payload = build_seatunnel_submit_payload(payload)
        response = await self._request("POST", self.submit_path, json=request_payload)
        try:
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPStatusError, ValueError) as exc:
            raise EngineError("SeaTunnel 作业提交失败") from exc
        if not isinstance(body, dict):
            raise EngineError("SeaTunnel 返回的作业响应不是 JSON 对象")
        job_id = body.get("job_id") or body.get("jobId") or body.get("id")
        if not isinstance(job_id, str) or not job_id.strip():
            raise EngineError("SeaTunnel 作业响应缺少 job_id")
        return EngineJobRef(job_id=job_id, raw=body)

    async def get_status(self, job_id: str) -> EngineStatus:
        """查询作业状态并映射到控制面稳定枚举。"""
        response = await self._request("GET", self.status_path.format(job_id=job_id))
        try:
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPStatusError, ValueError) as exc:
            raise EngineError("SeaTunnel 作业状态查询失败") from exc
        value = str(body.get("status", "unknown")).lower() if isinstance(body, dict) else "unknown"
        try:
            status = EngineJobStatus(value)
        except ValueError:
            status = EngineJobStatus.UNKNOWN
        detail = body.get("detail") if isinstance(body, dict) else None
        return EngineStatus(status=status, detail=str(detail) if detail else None)

    async def cancel(self, job_id: str) -> bool:
        """请求取消作业并将 HTTP 成功映射为布尔结果。"""
        response = await self._request("POST", self.cancel_path.format(job_id=job_id))
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise EngineError("SeaTunnel 作业取消失败") from exc
        return True
