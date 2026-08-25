"""Dependency health probes with sanitized public results."""

import asyncio
import time
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlparse

import asyncpg
import httpx
from redis import asyncio as redis_asyncio

from etl_agent.api.health_models import DependencyHealth
from etl_agent.config import Settings


@dataclass(frozen=True)
class HealthReport:
    status: Literal["ok", "degraded", "down"]
    dependencies: dict[str, DependencyHealth]


class HealthService:
    required_dependencies = {"postgresql", "redis", "minio", "vault", "llm"}

    def __init__(self, settings: Settings) -> None:
        """使用应用配置初始化健康检查服务。"""
        self.settings = settings

    async def check(self) -> HealthReport:
        """并行检查所有外部依赖并汇总整体就绪状态。"""
        checks = await asyncio.gather(
            self._check_postgresql(),
            self._check_redis(),
            self._check_http(
                "minio", self.settings.minio_endpoint.rstrip("/") + "/minio/health/live"
            ),
            self._check_http("vault", self.settings.vault_addr.rstrip("/") + "/v1/sys/health"),
            self._check_seatunnel(),
            self._check_llm(),
        )
        dependencies = {name: result for name, result in checks}
        required = [dependencies[name].status == "ok" for name in self.required_dependencies]
        optional_down = dependencies["seatunnel"].status == "down"
        overall: Literal["ok", "degraded", "down"]
        if all(required) and not optional_down:
            overall = "ok"
        elif all(required):
            overall = "degraded"
        else:
            overall = "down"
        return HealthReport(status=overall, dependencies=dependencies)

    async def _check_postgresql(self) -> tuple[str, DependencyHealth]:
        """通过执行 SELECT 1 检查 PostgreSQL 连接和基本查询能力。"""
        started = time.perf_counter()
        try:
            connection = await asyncpg.connect(
                self.settings.asyncpg_database_url,
                timeout=self.settings.health_check_timeout_seconds,
            )
            try:
                await connection.fetchval("SELECT 1")
            finally:
                await connection.close()
            return "postgresql", self._ok(started)
        except Exception:
            return "postgresql", self._down(started, "connection failed")

    async def _check_redis(self) -> tuple[str, DependencyHealth]:
        """通过 PING 检查 Redis 可用性，并确保连接客户端最终关闭。"""
        started = time.perf_counter()
        client = redis_asyncio.from_url(
            self.settings.redis_url,
            socket_connect_timeout=self.settings.health_check_timeout_seconds,
            socket_timeout=self.settings.health_check_timeout_seconds,
        )
        try:
            await client.ping()
            return "redis", self._ok(started)
        except Exception:
            return "redis", self._down(started, "connection failed")
        finally:
            await client.aclose()

    async def _check_http(self, name: str, endpoint: str) -> tuple[str, DependencyHealth]:
        """访问指定 HTTP 健康端点，并记录非 200 响应或网络异常。"""
        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(
                timeout=self.settings.health_check_timeout_seconds
            ) as client:
                response = await client.get(endpoint)
            if response.status_code == 200:
                return name, self._ok(started)
            return name, self._down(started, f"HTTP {response.status_code}")
        except Exception:
            return name, self._down(started, "endpoint unavailable")

    async def _check_seatunnel(self) -> tuple[str, DependencyHealth]:
        """通过建立 TCP 连接检查 SeaTunnel Zeta 端点是否可达。"""
        started = time.perf_counter()
        parsed = urlparse(self.settings.seatunnel_zeta_endpoint)
        if not parsed.hostname or not parsed.port:
            return "seatunnel", DependencyHealth(status="optional", detail="not configured")
        writer = None
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(parsed.hostname, parsed.port),
                timeout=self.settings.health_check_timeout_seconds,
            )
            return "seatunnel", self._ok(started)
        except Exception:
            return "seatunnel", self._down(started, "endpoint unavailable")
        finally:
            if writer is not None:
                writer.close()
                await writer.wait_closed()

    async def _check_llm(self) -> tuple[str, DependencyHealth]:
        """检查远端 LLM 所需的地址、密钥和模型配置是否完整。"""
        if self.settings.llm_base_url and self.settings.llm_api_key and self.settings.llm_model:
            return "llm", DependencyHealth(status="ok", detail="configured")
        return "llm", DependencyHealth(status="degraded", detail="configuration incomplete")

    @staticmethod
    def _ok(started: float) -> DependencyHealth:
        """构造成功探针结果，并计算本次检查耗时。"""
        return DependencyHealth(status="ok", detail="ready", latency_ms=_latency(started))

    @staticmethod
    def _down(started: float, detail: str) -> DependencyHealth:
        """构造失败探针结果，并计算本次检查耗时。"""
        return DependencyHealth(status="down", detail=detail, latency_ms=_latency(started))


def _latency(started: float) -> int:
    """将单调时钟起点到当前时刻的间隔转换为毫秒整数。"""
    return round((time.perf_counter() - started) * 1000)
