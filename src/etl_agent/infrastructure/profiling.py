"""MySQL/Doris 只读 Metadata Profile 生成器。"""

import asyncio
import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from etl_agent.config import Settings
from etl_agent.infrastructure.connection_testing import open_mysql_compatible_connection
from etl_agent.infrastructure.models import Connection, ConnectionType
from etl_agent.infrastructure.secrets import SecretProvider, SecretProviderError

_SYSTEM_SCHEMAS = ("information_schema", "performance_schema", "mysql", "sys")
_SENSITIVE_FIELD_MARKERS = (
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "email",
    "phone",
    "mobile",
    "id_card",
    "身份证",
    "手机号",
)


class ProfileError(RuntimeError):
    """表示 Profile 探查失败或连接类型不受支持。"""


@dataclass(frozen=True)
class ProfileBuildResult:
    """Profile 生成结果，内容只包含脱敏后的元数据和样本。"""

    fingerprint: str
    schema_snapshot: dict[str, Any]
    redacted_sample: dict[str, Any]
    estimated_row_count: int | None


class MetadataProfileService:
    """执行有范围、有样本预算的 MySQL/Doris 只读元数据探查。"""

    def __init__(self, provider: SecretProvider, settings: Settings) -> None:
        """使用 SecretProvider 和超时配置初始化 Profile 服务。"""
        self.provider = provider
        self.settings = settings

    async def generate(
        self,
        connection: Connection,
        table_names: list[str],
        sample_rows: int,
    ) -> ProfileBuildResult:
        """打开只读连接并生成 Schema、近似行数和脱敏样本摘要。"""
        if connection.connection_type not in {ConnectionType.MYSQL, ConnectionType.DORIS}:
            raise ProfileError("当前适配器暂不支持该连接类型")
        client = None
        try:
            client = await open_mysql_compatible_connection(
                connection,
                self.provider,
                self.settings.health_check_timeout_seconds,
            )
            return await asyncio.to_thread(
                _build_profile_sync,
                client,
                connection.database_name,
                table_names,
                sample_rows,
            )
        except SecretProviderError:
            raise
        except Exception as exc:
            raise ProfileError("只读 Profile 探查失败") from exc
        finally:
            if client is not None:
                await asyncio.to_thread(client.close)


def _build_profile_sync(
    client: Any,
    database_name: str | None,
    table_names: list[str],
    sample_rows: int,
) -> ProfileBuildResult:
    """在同步数据库线程中读取元数据并构造确定性 Profile 摘要。"""
    tables = _load_tables(client, database_name, table_names)
    schema_snapshot = {"version": "v1", "tables": tables}
    redacted_sample = _load_samples(client, tables, sample_rows)
    estimated_row_count = sum(
        table["estimated_row_count"]
        for table in tables
        if isinstance(table.get("estimated_row_count"), int)
    )
    canonical = json.dumps(
        {"schema": schema_snapshot, "sample": redacted_sample},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return ProfileBuildResult(
        fingerprint=fingerprint,
        schema_snapshot=schema_snapshot,
        redacted_sample=redacted_sample,
        estimated_row_count=estimated_row_count or None,
    )


def _load_tables(
    client: Any,
    database_name: str | None,
    table_names: list[str],
) -> list[dict[str, Any]]:
    """读取白名单范围内的表、字段类型和信息架构近似行数。"""
    clauses = ["table_schema NOT IN (%s, %s, %s, %s)"]
    params: list[Any] = list(_SYSTEM_SCHEMAS)
    if database_name:
        clauses.append("table_schema = %s")
        params.append(database_name)
    if table_names:
        placeholders = ", ".join(["%s"] * len(table_names))
        clauses.append(f"table_name IN ({placeholders})")
        params.extend(table_names)
    query = f"""
        SELECT table_schema, table_name, column_name, data_type,
               is_nullable, ordinal_position
        FROM information_schema.columns
        WHERE {" AND ".join(clauses)}
        ORDER BY table_schema, table_name, ordinal_position
        LIMIT 5000
    """
    with client.cursor() as cursor:
        cursor.execute(query, params)
        rows = cursor.fetchall()
    tables: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        key = (
            str(_metadata_value(row, "table_schema")),
            str(_metadata_value(row, "table_name")),
        )
        table = tables.setdefault(
            key,
            {
                "schema": key[0],
                "name": key[1],
                "columns": [],
                "estimated_row_count": None,
            },
        )
        table["columns"].append(
            {
                "name": str(_metadata_value(row, "column_name")),
                "data_type": str(_metadata_value(row, "data_type")),
                "nullable": str(_metadata_value(row, "is_nullable")).upper() == "YES",
                "ordinal_position": int(_metadata_value(row, "ordinal_position")),
            }
        )
    _load_estimated_counts(client, list(tables.values()), database_name)
    return list(tables.values())


def _load_estimated_counts(
    client: Any,
    tables: list[dict[str, Any]],
    database_name: str | None,
) -> None:
    """读取 information_schema.tables 的近似行数并回填到表摘要。"""
    if not tables:
        return
    names = [table["name"] for table in tables]
    clauses = ["table_schema NOT IN (%s, %s, %s, %s)"]
    params: list[Any] = list(_SYSTEM_SCHEMAS)
    if database_name:
        clauses.append("table_schema = %s")
        params.append(database_name)
    placeholders = ", ".join(["%s"] * len(names))
    clauses.append(f"table_name IN ({placeholders})")
    params.extend(names)
    query = f"""
        SELECT table_schema, table_name, table_rows
        FROM information_schema.tables
        WHERE {" AND ".join(clauses)}
    """
    with client.cursor() as cursor:
        cursor.execute(query, params)
        rows = cursor.fetchall()
    counts = {
        (
            str(_metadata_value(row, "table_schema")),
            str(_metadata_value(row, "table_name")),
        ): _metadata_value(row, "table_rows")
        for row in rows
    }
    for table in tables:
        count = counts.get((table["schema"], table["name"]))
        table["estimated_row_count"] = int(count) if count is not None else None


def _load_samples(client: Any, tables: list[dict[str, Any]], sample_rows: int) -> dict[str, Any]:
    """按样本预算读取各表少量记录并按字段名执行脱敏。"""
    if sample_rows == 0:
        return {}
    samples: dict[str, Any] = {}
    for table in tables:
        qualified_name = f"{_quote_identifier(table['schema'])}.{_quote_identifier(table['name'])}"
        query = f"SELECT * FROM {qualified_name} LIMIT %s"
        with client.cursor() as cursor:
            cursor.execute(query, (sample_rows,))
            rows = cursor.fetchall()
        samples[f"{table['schema']}.{table['name']}"] = [
            {str(key): _redact_value(str(key), value) for key, value in row.items()} for row in rows
        ]
    return samples


def _metadata_value(row: Mapping[str, Any], field: str) -> Any:
    """兼容不同 MySQL 驱动对 information_schema 列名大小写的返回差异。"""
    for candidate in (field, field.lower(), field.upper()):
        if candidate in row:
            return row[candidate]
    raise KeyError(field)


def _quote_identifier(value: str) -> str:
    """使用反引号转义数据库标识符，避免样本查询拼接注入。"""
    return f"`{value.replace('`', '``')}`"


def _redact_value(field_name: str, value: Any) -> Any:
    """根据字段名脱敏敏感值，并把非 JSON 类型转换为稳定文本。"""
    normalized = field_name.lower().replace("-", "_")
    if any(marker in normalized for marker in _SENSITIVE_FIELD_MARKERS):
        return "[REDACTED]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, (datetime, date, Decimal)):
        return str(value)
    if isinstance(value, bytes):
        return f"[BYTES:{len(value)}]"
    text = str(value)
    return text[:256]
