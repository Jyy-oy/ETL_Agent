"""SeaTunnel 执行引擎端口与 HTTP 适配器。"""

import re
from dataclasses import dataclass
from datetime import UTC, datetime
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
    CANCELLED = "cancelled"
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
    metrics: dict[str, Any] | None = None


_DETAIL_SECRET_PATTERN = re.compile(
    r"(?i)(password|passwd|secret|token|api[_-]?key)\s*[:=]\s*([^\s,;]+)"
)
_DETAIL_URI_PATTERN = re.compile(r"(?i)([a-z][a-z0-9+.-]*://[^\s:/]+:)[^@\s]+(@)")


def sanitize_engine_detail(value: Any) -> str | None:
    """脱敏外部引擎错误摘要中的密码、Token 和 URI 用户凭据。"""
    if value is None:
        return None
    detail = str(value).strip().replace("\n", " ")
    detail = _DETAIL_SECRET_PATTERN.sub(r"\1=<redacted>", detail)
    detail = _DETAIL_URI_PATTERN.sub(r"\1<redacted>\2", detail)
    return detail[:512] or None


def _metric_int(value: Any) -> int:
    """把 SeaTunnel 的字符串或数字指标安全转换为非负整数。"""
    try:
        return max(0, int(float(value)))
    except (TypeError, ValueError):
        return 0


def _nested_metric_total(value: Any) -> int:
    """汇总 SeaTunnel 按表名返回的嵌套计数指标。"""
    if isinstance(value, dict):
        return sum(_metric_int(item) for item in value.values())
    return _metric_int(value)


def _elapsed_seconds(body: dict[str, Any]) -> int | None:
    """根据 SeaTunnel 创建和完成时间补齐运行时长预算指标。"""
    created = body.get("createTime")
    finished = body.get("finishTime") or datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
    if not isinstance(created, str) or not isinstance(finished, str):
        return None
    try:
        start = datetime.strptime(created, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
        end = datetime.strptime(finished, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
    except ValueError:
        return None
    return max(0, int((end - start).total_seconds()))


def normalize_seatunnel_metrics(body: dict[str, Any]) -> dict[str, Any]:
    """将 SeaTunnel 原生指标转换为质量和预算规则使用的稳定字段。"""
    raw = body.get("metrics")
    metrics = dict(raw) if isinstance(raw, dict) else {}
    normalized: dict[str, Any] = dict(metrics)
    normalized["input_records"] = _nested_metric_total(
        metrics.get("SourceReceivedCount", metrics.get("TableSourceReceivedCount", 0))
    )
    normalized["output_records"] = _nested_metric_total(
        metrics.get("SinkWriteCount", metrics.get("TableSinkWriteCount", 0))
    )
    normalized["input_bytes"] = _nested_metric_total(
        metrics.get("SourceReceivedBytes", metrics.get("TableSourceReceivedBytes", 0))
    )
    normalized["output_bytes"] = _nested_metric_total(
        metrics.get("SinkWriteBytes", metrics.get("TableSinkWriteBytes", 0))
    )
    normalized["rejected_records"] = _nested_metric_total(
        metrics.get(
            "RejectedRecordCount",
            metrics.get("ErrorRecordCount", metrics.get("SourceReadErrorCount", 0)),
        )
    )
    elapsed = _elapsed_seconds(body)
    if elapsed is not None:
        normalized["elapsed_seconds"] = elapsed
    return normalized


class ExecutionEngine(Protocol):
    """数据面执行引擎的最小可替换端口。"""

    async def submit(self, payload: dict[str, Any]) -> EngineJobRef:
        """提交冻结的执行制品并返回引擎作业引用。"""
        ...

    async def get_status(self, job_id: str) -> EngineStatus:
        """查询引擎作业状态并返回控制面稳定模型。"""
        ...

    async def cancel(self, job_id: str) -> bool:
        """请求停止一个已经提交的引擎作业。"""
        ...

    async def cleanup(self, job_id: str, payload: dict[str, Any] | None = None) -> bool:
        """清理作业产生的影子表、错误表或临时制品。"""
        ...

    async def atomic_swap(self, job_id: str, payload: dict[str, Any]) -> bool:
        """将质量通过的影子表原子切换为正式表。"""
        ...

    async def rollback(self, job_id: str, payload: dict[str, Any]) -> bool:
        """恢复正式表并清理本次执行产生的临时制品。"""
        ...


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
            "idempotency_key": str(payload.get("idempotency_key", "")),
        },
    }


class SeaTunnelAdapter:
    """通过可配置 Zeta HTTP 路径提交、查询和取消 SeaTunnel 作业。"""

    def __init__(
        self,
        endpoint: str,
        *,
        submit_path: str = "/submit-job",
        submit_format: str = "hocon",
        status_path: str = "/job-info/{job_id}",
        cancel_path: str = "/stop-job",
        cleanup_path: str = "/jobs/{job_id}/cleanup",
        swap_path: str = "/jobs/{job_id}/swap",
        rollback_path: str = "/jobs/{job_id}/rollback",
        timeout_seconds: float = 30.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        """保存 Zeta 地址和路径，允许测试注入 HTTP 客户端。"""
        self.endpoint = endpoint.rstrip("/")
        self.submit_path = submit_path
        self.submit_format = submit_format
        self.status_path = status_path
        self.cancel_path = cancel_path
        self.cleanup_path = cleanup_path
        self.swap_path = swap_path
        self.rollback_path = rollback_path
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
        if self.submit_format.lower() == "hocon":
            response = await self._request(
                "POST",
                self.submit_path,
                params={"format": "hocon"},
                content=request_payload["job_config"],
                headers={
                    "Content-Type": "text/plain",
                    "X-Idempotency-Key": str(payload.get("idempotency_key", "")),
                },
            )
        else:
            response = await self._request("POST", self.submit_path, json=request_payload)
        try:
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPStatusError, ValueError) as exc:
            raise EngineError("SeaTunnel 作业提交失败") from exc
        if not isinstance(body, dict):
            raise EngineError("SeaTunnel 返回的作业响应不是 JSON 对象")
        job_id = body.get("job_id") or body.get("jobId") or body.get("id")
        if not isinstance(job_id, (str, int)) or not str(job_id).strip():
            raise EngineError("SeaTunnel 作业响应缺少 job_id")
        return EngineJobRef(job_id=str(job_id), raw=body)

    async def get_status(self, job_id: str) -> EngineStatus:
        """查询作业状态并映射到控制面稳定枚举。"""
        response = await self._request("GET", self.status_path.format(job_id=job_id))
        try:
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPStatusError, ValueError) as exc:
            raise EngineError("SeaTunnel 作业状态查询失败") from exc
        value = (
            str(body.get("status", body.get("jobStatus", "unknown"))).lower()
            if isinstance(body, dict)
            else "unknown"
        )
        status = {
            "created": EngineJobStatus.QUEUED,
            "submitted": EngineJobStatus.QUEUED,
            "pending": EngineJobStatus.QUEUED,
            "scheduled": EngineJobStatus.QUEUED,
            "waiting": EngineJobStatus.QUEUED,
            "running": EngineJobStatus.RUNNING,
            "executing": EngineJobStatus.RUNNING,
            "canceling": EngineJobStatus.RUNNING,
            "failing": EngineJobStatus.RUNNING,
            "finished": EngineJobStatus.SUCCEEDED,
            "success": EngineJobStatus.SUCCEEDED,
            "succeed": EngineJobStatus.SUCCEEDED,
            "completed": EngineJobStatus.SUCCEEDED,
            "failed": EngineJobStatus.FAILED,
            "error": EngineJobStatus.FAILED,
            "cancelled": EngineJobStatus.CANCELLED,
            "canceled": EngineJobStatus.CANCELLED,
        }.get(value, EngineJobStatus.UNKNOWN)
        detail = None
        if isinstance(body, dict):
            detail = sanitize_engine_detail(body.get("detail") or body.get("errorMsg"))
        metrics = normalize_seatunnel_metrics(body) if isinstance(body, dict) else {}
        return EngineStatus(
            status=status,
            detail=detail,
            metrics=metrics if isinstance(metrics, dict) else {},
        )

    async def cancel(self, job_id: str) -> bool:
        """按 SeaTunnel REST 契约提交作业取消请求并映射为布尔结果。"""
        path = self.cancel_path.format(job_id=job_id)
        cancel_id: int | str = int(job_id) if job_id.isdigit() else job_id
        response = await self._request(
            "POST",
            path,
            json={"jobId": cancel_id},
            headers={"Content-Type": "application/json"},
        )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise EngineError("SeaTunnel 作业取消失败") from exc
        return True

    async def _post_action(
        self, path: str, job_id: str, payload: dict[str, Any] | None = None
    ) -> bool:
        """调用清理、原子切换或回滚端点，并隐藏外部响应细节。"""
        response = await self._request("POST", path.format(job_id=job_id), json=payload or {})
        try:
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPStatusError, ValueError) as exc:
            raise EngineError("SeaTunnel 作业操作失败") from exc
        return bool(body.get("ok", True)) if isinstance(body, dict) else True

    async def cleanup(self, job_id: str, payload: dict[str, Any] | None = None) -> bool:
        """请求引擎清理影子表和错误表等中间产物。"""
        del payload
        return await self._post_action(self.cleanup_path, job_id)

    async def atomic_swap(self, job_id: str, payload: dict[str, Any]) -> bool:
        """请求目标端执行经过质量门禁的原子发布。"""
        return await self._post_action(self.swap_path, job_id, payload)

    async def rollback(self, job_id: str, payload: dict[str, Any]) -> bool:
        """请求目标端恢复原表并清理本次影子表。"""
        return await self._post_action(self.rollback_path, job_id, payload)
