"""真实合成 MySQL -> Doris 数据面运行时编译和目标表适配器。

数据库中只保存不可变 PipelineVersion、连接元数据和 SecretRef。Worker 在提交
SeaTunnel 前才读取 Vault，并在进程内短暂组装带凭据的 Connector 配置；运行事实
只保存目标表、影子表等非敏感元数据。
"""

import asyncio
import re
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import pymysql  # type: ignore[import-untyped]
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.config import Settings
from etl_agent.domain.generation import EtlPlan, TransformOperation
from etl_agent.infrastructure.models import Connection, ConnectionType, MetadataProfile
from etl_agent.infrastructure.secrets import SecretProvider, SecretProviderError
from etl_agent.workers.engine import EngineJobRef, EngineStatus, SeaTunnelAdapter


class RuntimeCompilationError(RuntimeError):
    """表示 Pipeline 不能安全编译为真实数据面作业。"""


@dataclass(frozen=True, slots=True)
class RuntimeJobArtifact:
    """保存一次提交所需的临时作业配置和可审计非敏感元数据。"""

    hocon: str
    metadata: dict[str, str]


def _quote_identifier(value: str) -> str:
    """校验并引用数据库标识符，避免把模型输出直接拼接进 DDL/查询。"""
    normalized = value.strip()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", normalized):
        raise RuntimeCompilationError("真实数据面仅支持字母、数字和下划线组成的标识符")
    return f"`{normalized}`"


def _hocon_string(value: str) -> str:
    """将运行时字符串编码为 HOCON 双引号字符串。"""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def _single_table(profile: MetadataProfile, label: str) -> tuple[dict[str, Any], set[str]]:
    """提取单表 Profile，并返回表摘要和字段集合。"""
    tables = profile.schema_snapshot.get("tables")
    if not isinstance(tables, list) or len(tables) != 1 or not isinstance(tables[0], dict):
        raise RuntimeCompilationError(f"真实数据面当前要求 {label} Profile 只包含一张表")
    table = tables[0]
    columns = table.get("columns")
    if not isinstance(columns, list) or not columns:
        raise RuntimeCompilationError(f"{label} Profile 缺少字段结构")
    fields = {
        str(column.get("name"))
        for column in columns
        if isinstance(column, dict) and column.get("name")
    }
    if not fields:
        raise RuntimeCompilationError(f"{label} Profile 没有可用字段")
    return table, fields


async def _load_profile_connection(
    session: AsyncSession,
    profile_id: str,
) -> tuple[MetadataProfile, Connection]:
    """按 Profile ID 读取连接元数据，禁止跨项目或缺失连接继续提交。"""
    try:
        profile_uuid = UUID(str(profile_id))
    except ValueError as exc:
        raise RuntimeCompilationError("Pipeline 引用了无效 Profile ID") from exc
    result = await session.execute(
        select(MetadataProfile, Connection)
        .join(Connection, Connection.id == MetadataProfile.connection_id)
        .where(MetadataProfile.id == profile_uuid)
    )
    row = result.first()
    if row is None:
        raise RuntimeCompilationError("Pipeline 引用的 Profile 不存在")
    profile, connection = row
    return profile, connection


async def compile_runtime_job(
    session: AsyncSession,
    execution,
    version,
    *,
    settings: Settings,
    provider: SecretProvider,
) -> dict[str, Any]:
    """读取冻结事实并生成一次性真实 MySQL -> Doris SeaTunnel 作业载荷。"""
    plan = EtlPlan.model_validate(version.etl_plan_json or {})
    if len(version.source_profile_ids) != 1 or len(version.target_profile_ids) != 1:
        raise RuntimeCompilationError("真实数据面当前只支持一张源表和一张目标表")
    source_profile, source_connection = await _load_profile_connection(
        session, version.source_profile_ids[0]
    )
    target_profile, target_connection = await _load_profile_connection(
        session, version.target_profile_ids[0]
    )
    if source_connection.connection_type != ConnectionType.MYSQL:
        raise RuntimeCompilationError("真实数据面源连接必须是 MySQL")
    if target_connection.connection_type != ConnectionType.DORIS:
        raise RuntimeCompilationError("真实数据面目标连接必须是 Doris")
    source_table, source_fields = _single_table(source_profile, "源")
    target_table, target_fields = _single_table(target_profile, "目标")
    if not plan.field_mappings:
        raise RuntimeCompilationError("ETL 方案没有字段映射")

    query_fields: list[str] = []
    target_columns: list[str] = []
    for mapping in plan.field_mappings:
        if mapping.source_field not in source_fields:
            raise RuntimeCompilationError(f"源字段不存在: {mapping.source_field}")
        if mapping.target_field not in target_fields:
            raise RuntimeCompilationError(f"目标字段不存在: {mapping.target_field}")
        if mapping.transform not in {None, TransformOperation.RENAME}:
            raise RuntimeCompilationError("真实数据面首期仅支持直接映射和重命名")
        source_expr = _quote_identifier(mapping.source_field)
        if mapping.source_field != mapping.target_field:
            source_expr += f" AS {_quote_identifier(mapping.target_field)}"
        query_fields.append(source_expr)
        target_columns.append(_quote_identifier(mapping.target_field))

    source_credentials = await _read_credentials(provider, source_connection, "源")
    target_credentials = await _read_credentials(provider, target_connection, "目标")
    source_database = source_connection.database_name or source_credentials.get("database")
    target_database = target_connection.database_name or target_credentials.get("database")
    if not source_database or not target_database:
        raise RuntimeCompilationError("源或目标连接缺少数据库名称")

    source_schema = str(source_table.get("schema") or source_database)
    source_name = str(source_table.get("name") or "")
    target_name = str(target_table.get("name") or "")
    _quote_identifier(source_schema)
    _quote_identifier(source_name)
    _quote_identifier(target_database)
    _quote_identifier(target_name)
    suffix = execution.id.hex[:12]
    # Doris 标识符长度留出动作后缀空间，避免长目标表名使作业提交失败。
    shadow_name = f"{target_name[:39]}__shadow_{suffix}"
    error_name = f"{target_name[:39]}__errors_{suffix}"
    _quote_identifier(shadow_name)
    _quote_identifier(error_name)

    query = (
        "SELECT "
        + ", ".join(query_fields)
        + f" FROM {_quote_identifier(source_schema)}.{_quote_identifier(source_name)}"
    )
    source_url = (
        f"jdbc:mysql://{settings.seatunnel_mysql_host}:{source_connection.port}/"
        f"{source_database}?useSSL=false&serverTimezone=UTC"
    )
    label = f"etl-agent-{execution.id.hex[:16]}"
    hocon = f"""env {{
  parallelism = 1
  job.mode = "BATCH"
}}

source {{
  Jdbc {{
    url = {_hocon_string(source_url)}
    driver = "com.mysql.cj.jdbc.Driver"
    user = {_hocon_string(source_credentials["username"])}
    password = {_hocon_string(source_credentials["password"])}
    query = {_hocon_string(query)}
  }}
}}

sink {{
  Doris {{
    fenodes = {_hocon_string(settings.seatunnel_doris_fenodes)}
    username = {_hocon_string(target_credentials["username"])}
    password = {_hocon_string(target_credentials["password"])}
    database = {_hocon_string(target_database)}
    table = {_hocon_string(shadow_name)}
    sink.label-prefix = {_hocon_string(label)}
    sink.enable-2pc = true
    doris.config = {{
      format = "json"
      read_json_by_line = "true"
    }}
  }}
}}
"""
    metadata = {
        "source_connection_id": str(source_connection.id),
        "target_connection_id": str(target_connection.id),
        "target_host": target_connection.host,
        "target_port": str(target_connection.port),
        "target_secret_ref": target_connection.secret_ref,
        "target_database": target_database,
        "target_table": target_name,
        "shadow_table": shadow_name,
        "error_table": error_name,
    }
    return {"hocon": hocon, **metadata}


async def _read_credentials(
    provider: SecretProvider,
    connection: Connection,
    label: str,
) -> dict[str, str]:
    """从 Vault 读取一次性连接凭据，并拒绝缺少用户名或密码的连接。"""
    try:
        credentials = dict(await provider.read(connection.secret_ref))
    except SecretProviderError as exc:
        raise RuntimeCompilationError(f"{label}连接 SecretRef 无法解析") from exc
    username = connection.username or credentials.get("username")
    password = credentials.get("password")
    if not username or password is None:
        raise RuntimeCompilationError(f"{label}连接凭据不完整")
    return {"username": username, "password": password, **credentials}


class DorisTargetAdapter:
    """通过 Doris FE MySQL 协议创建影子表并执行原子替换或回滚。"""

    def __init__(self, provider: SecretProvider, settings: Settings) -> None:
        """保存 SecretProvider 和超时配置，不在构造阶段读取凭据。"""
        self.provider = provider
        self.settings = settings

    async def _run_sql(self, payload: dict[str, Any], statements: list[str]) -> None:
        """在 Worker 线程中执行一组受控 Doris DDL。"""
        credentials = await self._credentials(payload)
        host = str(payload.get("target_host", "")).strip()
        port = int(payload.get("target_port", 0))
        database = str(payload.get("target_database", "")).strip()
        if not host or not port or not database:
            raise RuntimeCompilationError("Doris 目标运行元数据不完整")
        connection = await asyncio.to_thread(
            pymysql.connect,
            host=host,
            port=port,
            user=credentials["username"],
            password=credentials["password"],
            database=database,
            connect_timeout=max(1, int(self.settings.health_check_timeout_seconds)),
            read_timeout=max(1, int(self.settings.health_check_timeout_seconds)),
            write_timeout=max(1, int(self.settings.health_check_timeout_seconds)),
            autocommit=True,
        )
        try:
            await asyncio.to_thread(self._execute_statements, connection, statements)
        finally:
            await asyncio.to_thread(connection.close)

    @staticmethod
    def _execute_statements(connection: Any, statements: list[str]) -> None:
        """顺序执行已经由标识符白名单构造的 Doris SQL。"""
        with connection.cursor() as cursor:
            for statement in statements:
                cursor.execute(statement)

    async def _credentials(self, payload: dict[str, Any]) -> dict[str, str]:
        """运行时解析目标 SecretRef，永不把密码写入执行载荷或日志。"""
        secret_ref = str(payload.get("target_secret_ref", "")).strip()
        if not secret_ref:
            raise RuntimeCompilationError("Doris 目标缺少 SecretRef")
        try:
            values = dict(await self.provider.read(secret_ref))
        except SecretProviderError as exc:
            raise RuntimeCompilationError("Doris 目标 SecretRef 无法解析") from exc
        username = values.get("username")
        password = values.get("password")
        if not username or password is None:
            raise RuntimeCompilationError("Doris 目标凭据不完整")
        return {"username": username, "password": password}

    @staticmethod
    def _table(payload: dict[str, Any], key: str) -> str:
        """读取并引用运行元数据中的表名。"""
        value = str(payload.get(key, "")).strip()
        if not value:
            raise RuntimeCompilationError(f"Doris 运行载荷缺少 {key}")
        return _quote_identifier(value)

    async def prepare_shadow(self, payload: dict[str, Any]) -> None:
        """按目标表结构创建并清空本次执行的影子表。"""
        _quote_identifier(str(payload.get("target_database", "")))
        target = self._table(payload, "target_table")
        shadow = self._table(payload, "shadow_table")
        await self._run_sql(
            payload,
            [
                f"CREATE TABLE IF NOT EXISTS {shadow} LIKE {target}",
                f"TRUNCATE TABLE {shadow}",
            ],
        )

    async def cleanup(self, payload: dict[str, Any]) -> None:
        """删除失败或取消执行留下的影子表和错误表。"""
        shadow = self._table(payload, "shadow_table")
        error = self._table(payload, "error_table")
        await self._run_sql(
            payload,
            [f"DROP TABLE IF EXISTS {shadow}", f"DROP TABLE IF EXISTS {error}"],
        )

    async def atomic_swap(self, payload: dict[str, Any]) -> bool:
        """使用 Doris ALTER TABLE REPLACE WITH 完成原子数据交换。"""
        target = self._table(payload, "target_table")
        shadow = self._table(payload, "shadow_table")
        await self._run_sql(
            payload,
            [f"ALTER TABLE {target} REPLACE WITH TABLE {shadow} PROPERTIES ('swap' = 'true')"],
        )
        return True

    async def rollback(self, payload: dict[str, Any]) -> bool:
        """再次交换目标表和影子表恢复上一版本，并删除恢复后影子表。"""
        target = self._table(payload, "target_table")
        shadow = self._table(payload, "shadow_table")
        await self._run_sql(
            payload,
            [
                f"ALTER TABLE {target} REPLACE WITH TABLE {shadow} PROPERTIES ('swap' = 'true')",
                f"DROP TABLE IF EXISTS {shadow}",
            ],
        )
        return True


class SeaTunnelDorisEngine:
    """把 SeaTunnel 作业和 Doris 发布动作组合成一个受管执行引擎。"""

    def __init__(self, seatunnel: SeaTunnelAdapter, target: DorisTargetAdapter) -> None:
        """保存两个外部适配器，所有调用仍由 Outbox Broker 发起。"""
        self.seatunnel = seatunnel
        self.target = target

    async def submit(self, payload: dict[str, Any]) -> EngineJobRef:
        """提交前准备影子表，SeaTunnel 提交失败时立即清理。"""
        await self.target.prepare_shadow(payload)
        try:
            return await self.seatunnel.submit(payload)
        except Exception:
            await self.target.cleanup(payload)
            raise

    async def get_status(self, job_id: str) -> EngineStatus:
        """读取并复用 SeaTunnel 的稳定状态映射。"""
        return await self.seatunnel.get_status(job_id)

    async def cancel(self, job_id: str) -> bool:
        """通过 SeaTunnel REST 请求停止作业。"""
        return await self.seatunnel.cancel(job_id)

    async def cleanup(self, job_id: str, payload: dict[str, Any] | None = None) -> bool:
        """清理 Doris 影子表；SeaTunnel 2.3.10 没有原生清理动作。"""
        del job_id
        if payload is None:
            raise RuntimeCompilationError("清理动作缺少 Doris 目标元数据")
        await self.target.cleanup(payload)
        return True

    async def atomic_swap(self, job_id: str, payload: dict[str, Any]) -> bool:
        """执行 Doris 目标表原子切换。"""
        del job_id
        return await self.target.atomic_swap(payload)

    async def rollback(self, job_id: str, payload: dict[str, Any]) -> bool:
        """执行 Doris 目标表原子回滚。"""
        del job_id
        return await self.target.rollback(payload)
