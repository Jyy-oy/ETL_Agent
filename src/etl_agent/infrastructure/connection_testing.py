"""MySQL/Doris 连接测试适配器。"""

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Literal

import pymysql  # type: ignore[import-untyped]

from etl_agent.config import Settings
from etl_agent.infrastructure.models import Connection, ConnectionType
from etl_agent.infrastructure.secrets import SecretProvider, SecretProviderError


@dataclass(frozen=True)
class ConnectionTestResult:
    """连接测试的脱敏结果。"""

    status: Literal["passed", "failed", "unsupported"]
    detail: str
    latency_ms: int


def _connection_parameters(
    connection: Connection,
    credentials: dict[str, str],
    timeout_seconds: float,
) -> dict[str, Any]:
    """合并连接登记信息和运行时凭据，生成 PyMySQL 连接参数。"""
    password = credentials.get("password")
    # 空字符串密码在本地 Doris 开发账号中是合法凭据；只有缺少字段才算不完整。
    if password is None:
        raise SecretProviderError("Secret 缺少 password")
    return {
        "host": connection.host,
        "port": connection.port,
        "user": connection.username or credentials.get("username"),
        "password": password,
        "database": connection.database_name or credentials.get("database"),
        "connect_timeout": max(1, int(timeout_seconds)),
        "read_timeout": max(1, int(timeout_seconds)),
        "write_timeout": max(1, int(timeout_seconds)),
        "cursorclass": pymysql.cursors.DictCursor,
        "autocommit": True,
    }


async def open_mysql_compatible_connection(
    connection: Connection,
    provider: SecretProvider,
    timeout_seconds: float,
) -> Any:
    """解析 SecretRef 并打开只用于探查的 MySQL 兼容数据库连接。"""
    credentials = dict(await provider.read(connection.secret_ref))
    parameters = _connection_parameters(connection, credentials, timeout_seconds)
    if not parameters["user"]:
        raise SecretProviderError("连接缺少 username")
    return await asyncio.to_thread(pymysql.connect, **parameters)


async def run_connection_test(
    connection: Connection,
    provider: SecretProvider,
    settings: Settings,
) -> ConnectionTestResult:
    """测试 MySQL/Doris 连接和 SELECT 1 能力，不返回底层异常或凭据。"""
    started = time.perf_counter()
    if connection.connection_type not in {ConnectionType.MYSQL, ConnectionType.DORIS}:
        return ConnectionTestResult(
            "unsupported", "当前适配器暂不支持该连接类型", _latency(started)
        )
    client = None
    try:
        client = await open_mysql_compatible_connection(
            connection, provider, settings.health_check_timeout_seconds
        )
        await asyncio.to_thread(_execute_select_one, client)
        return ConnectionTestResult("passed", "连接成功且只读探针通过", _latency(started))
    except SecretProviderError:
        return ConnectionTestResult("failed", "SecretRef 无法解析或凭据不完整", _latency(started))
    except Exception:
        return ConnectionTestResult("failed", "连接失败或只读探针失败", _latency(started))
    finally:
        if client is not None:
            await asyncio.to_thread(client.close)


def _execute_select_one(client: Any) -> None:
    """在同步数据库客户端中执行最小只读连通性查询。"""
    with client.cursor() as cursor:
        cursor.execute("SELECT 1")
        cursor.fetchone()


def _latency(started: float) -> int:
    """将探针耗时转换为毫秒整数。"""
    return round((time.perf_counter() - started) * 1000)
