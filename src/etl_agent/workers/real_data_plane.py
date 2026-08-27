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
from etl_agent.domain.generation import EtlPlan, FieldMapping, TransformOperation
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


_FILTER_PATTERN = re.compile(
    r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*(<=|>=|<>|=|<|>)\s*"
    r"(-?(?:\d+(?:\.\d*)?|\.\d+))\s*$"
)
# MySQL CAST 只接受有限的目标类型；Doris/通用 SQL 类型名不能直接复用。
_MYSQL_CAST_TYPES = {
    "bigint": "SIGNED",
    "int": "SIGNED",
    "integer": "SIGNED",
    "smallint": "SIGNED",
    "tinyint": "SIGNED",
    "decimal": "DECIMAL(18,2)",
    "numeric": "DECIMAL(18,2)",
    "double": "DOUBLE",
    "float": "FLOAT",
    "date": "DATE",
    "datetime": "DATETIME",
    "timestamp": "DATETIME",
    "char": "CHAR(255)",
    "varchar": "CHAR(255)",
    "text": "CHAR(255)",
    "boolean": "SIGNED",
    "bool": "SIGNED",
}


_PROFILE_TYPE_ALIASES = {
    "integer": "int",
    "numeric": "decimal",
    "bool": "boolean",
}


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


def _profile_column_types(profile: MetadataProfile) -> dict[str, str]:
    """读取 Profile 中的字段类型，供安全 CAST 白名单使用。"""
    tables = profile.schema_snapshot.get("tables")
    if not isinstance(tables, list) or len(tables) != 1 or not isinstance(tables[0], dict):
        return {}
    columns = tables[0].get("columns")
    if not isinstance(columns, list):
        return {}
    return {
        str(column["name"]): str(column.get("data_type", "")).lower()
        for column in columns
        if isinstance(column, dict) and column.get("name")
    }


def _cast_type(profile_type: str, field: str) -> str:
    """把 Profile 类型转换为 MySQL 固定 SQL 类型，拒绝模型自定义类型片段。"""
    normalized = _normalize_profile_type(profile_type)
    cast_type = _MYSQL_CAST_TYPES.get(normalized)
    if cast_type is None:
        raise RuntimeCompilationError(f"字段 {field} 的类型不在真实数据面 CAST 白名单中")
    return cast_type


def _normalize_profile_type(profile_type: str) -> str:
    """去除类型参数并归一化安全的 Profile 类型别名。"""
    base_type = profile_type.strip().lower().split("(", 1)[0].strip()
    return _PROFILE_TYPE_ALIASES.get(base_type, base_type)


def _same_profile_type(source_type: str, target_type: str) -> bool:
    """判断源、目标 Profile 类型是否相同，避免生成没有意义的 CAST。"""
    return _normalize_profile_type(source_type) == _normalize_profile_type(target_type)


def _compile_filter_condition(condition: str, source_fields: set[str]) -> str:
    """编译单个数值比较过滤条件，避免把模型文本直接拼接进 SQL。"""
    matched = _FILTER_PATTERN.fullmatch(condition)
    if matched is None:
        raise RuntimeCompilationError("真实数据面过滤条件只支持字段与数值的简单比较")
    field, operator, value = matched.groups()
    if field not in source_fields:
        raise RuntimeCompilationError(f"过滤条件引用了不存在的源字段: {field}")
    return f"{_quote_identifier(field)} {operator} {value}"


def _filter_parameter(rule: Any) -> str:
    """读取过滤规则条件，并兼容历史候选曾使用的 expression 参数名。"""
    parameters = rule.parameters
    condition = parameters.get("condition")
    if isinstance(condition, str) and condition.strip():
        return condition
    # 历史版本的 LLM 候选曾把同一语义命名为 expression，不能让已冻结版本失效。
    expression = parameters.get("expression")
    if isinstance(expression, str) and expression.strip():
        return expression
    raise RuntimeCompilationError("过滤转换缺少 condition 参数（兼容旧版本的 expression）")


def _mapping_expression(
    source_field: str,
    target_field: str,
    transform: TransformOperation | None,
    source_types: dict[str, str],
    target_types: dict[str, str],
) -> str:
    """生成一个字段映射 SQL 表达式，只允许直接映射、重命名和类型转换。"""
    if transform not in {None, TransformOperation.RENAME, TransformOperation.CAST}:
        raise RuntimeCompilationError("真实数据面字段映射只支持直接映射、重命名和 CAST")
    source_sql = _quote_identifier(source_field)
    alias = _quote_identifier(target_field)
    cast_applied = False
    if transform is TransformOperation.CAST:
        source_type = source_types.get(source_field, "")
        target_type = target_types.get(target_field) or source_types.get(source_field)
        if not target_type:
            raise RuntimeCompilationError(f"字段 {target_field} 缺少可用于 CAST 的 Profile 类型")
        # 源、目标类型一致时直接映射，避免 MySQL 不支持的冗余类型名和无效转换。
        if not source_type or not _same_profile_type(source_type, target_type):
            source_sql = f"CAST({source_sql} AS {_cast_type(target_type, target_field)})"
            cast_applied = True
    if source_field != target_field or cast_applied:
        return f"{source_sql} AS {alias}"
    return source_sql


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
    source_types = _profile_column_types(source_profile)
    target_types = _profile_column_types(target_profile)
    if not plan.field_mappings:
        raise RuntimeCompilationError("ETL 方案没有字段映射")

    mapping_expressions: dict[str, str] = {}
    mapping_models: dict[str, FieldMapping] = {}
    for mapping in plan.field_mappings:
        if mapping.source_field not in source_fields:
            raise RuntimeCompilationError(f"源字段不存在: {mapping.source_field}")
        if mapping.target_field not in target_fields:
            raise RuntimeCompilationError(f"目标字段不存在: {mapping.target_field}")
        mapping_models[mapping.target_field] = mapping
        mapping_expressions[mapping.target_field] = _mapping_expression(
            mapping.source_field,
            mapping.target_field,
            mapping.transform,
            source_types,
            target_types,
        )

    # JDBC 结果按 SELECT 顺序写入 Doris；以目标 Profile 列顺序重排，避免模型返回顺序造成错列。
    target_column_order = [
        str(column.get("name"))
        for column in target_table.get("columns", [])
        if isinstance(column, dict) and column.get("name") in mapping_expressions
    ]
    query_fields = [mapping_expressions[field] for field in target_column_order]
    mapping_by_target = {field: index for index, field in enumerate(target_column_order)}

    filters: list[str] = []
    for rule in plan.transforms:
        if rule.operation is TransformOperation.FILTER:
            filters.append(_compile_filter_condition(_filter_parameter(rule), source_fields))
            continue
        if rule.operation is TransformOperation.CAST:
            if len(rule.source_fields) != 1:
                raise RuntimeCompilationError("CAST 转换必须指定一个源字段")
            source_field = rule.source_fields[0]
            target_field = rule.target_field or source_field
            if source_field not in source_fields or target_field not in target_fields:
                raise RuntimeCompilationError("CAST 转换引用了不存在的字段")
            index = mapping_by_target.get(target_field)
            if index is None:
                raise RuntimeCompilationError(f"CAST 目标字段未声明映射: {target_field}")
            mapping = mapping_models[target_field]
            query_fields[index] = _mapping_expression(
                mapping.source_field,
                mapping.target_field,
                TransformOperation.CAST,
                source_types,
                target_types,
            )
            continue
        raise RuntimeCompilationError("真实数据面当前仅支持 FILTER 和 CAST 转换，其他转换暂未实现")

    # QualityContract 的必填目标字段必须在源查询中排除 NULL；反向查询同时把这些行送入错误表，
    # 避免 SQL 三值逻辑让不合规记录既没有进入目标表，也没有留下质量证据。
    required_conditions: list[str] = []
    for required_field in plan.quality_contract.required_fields:
        mapping_model = mapping_models.get(required_field)
        if mapping_model is None:
            raise RuntimeCompilationError(f"质量必填字段未声明映射: {required_field}")
        required_conditions.append(f"{_quote_identifier(mapping_model.source_field)} IS NOT NULL")
    accept_conditions = [*filters, *required_conditions]

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
    # 错误表后缀来自冻结的 QualityContract；先限制为 Doris 标识符，避免模型文本进入 DDL。
    error_suffix = plan.quality_contract.error_table_suffix.strip()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", error_suffix):
        raise RuntimeCompilationError("错误表后缀只能包含字母、数字和下划线")
    # Doris 标识符最多 64 个字符，按实际后缀长度为动作名预留空间。
    shadow_name = f"{target_name[: 64 - len('__shadow_') - len(suffix) - 1]}__shadow_{suffix}"
    error_name = f"{target_name[: 64 - len(error_suffix) - len(suffix) - 1]}{error_suffix}_{suffix}"
    _quote_identifier(shadow_name)
    _quote_identifier(error_name)

    query = (
        "SELECT "
        + ", ".join(query_fields)
        + f" FROM {_quote_identifier(source_schema)}.{_quote_identifier(source_name)}"
    )
    if accept_conditions:
        query += " WHERE " + " AND ".join(accept_conditions)
    error_query = ""
    if accept_conditions:
        # 条件已经过白名单编译；反向查询只读取拒绝行，供错误表回收使用。
        # MySQL 的 NULL 比较结果为 UNKNOWN；补充 IS NULL 分支，确保未知条件也进入错误表。
        accept_expression = " AND ".join(accept_conditions)
        error_query = (
            "SELECT "
            + ", ".join(query_fields)
            + f" FROM {_quote_identifier(source_schema)}.{_quote_identifier(source_name)}"
            + " WHERE NOT ("
            + accept_expression
            + ") OR ("
            + accept_expression
            + ") IS NULL"
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
        "source_host": source_connection.host,
        "source_port": str(source_connection.port),
        "source_secret_ref": source_connection.secret_ref,
        "source_database": source_database,
        "source_table": f"{source_schema}.{source_name}",
        "error_query": error_query,
        "error_columns": ",".join(target_column_order),
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

    async def _source_credentials(self, payload: dict[str, Any]) -> dict[str, str]:
        """运行时解析源端 SecretRef，拒绝把源端密码写入执行事实。"""
        secret_ref = str(payload.get("source_secret_ref", "")).strip()
        if not secret_ref:
            raise RuntimeCompilationError("MySQL 源端缺少 SecretRef")
        try:
            values = dict(await self.provider.read(secret_ref))
        except SecretProviderError as exc:
            raise RuntimeCompilationError("MySQL 源端 SecretRef 无法解析") from exc
        username = values.get("username")
        password = values.get("password")
        if not username or password is None:
            raise RuntimeCompilationError("MySQL 源端凭据不完整")
        return {"username": username, "password": password}

    @staticmethod
    def _read_rows(connection: Any, query: str) -> list[tuple[Any, ...]]:
        """读取已经由编译器生成的拒绝行查询结果。"""
        with connection.cursor() as cursor:
            cursor.execute(query)
            return list(cursor.fetchall())

    @staticmethod
    def _write_rows(connection: Any, statement: str, rows: list[tuple[Any, ...]]) -> None:
        """批量写入错误表，值使用驱动参数绑定而不是字符串拼接。"""
        with connection.cursor() as cursor:
            cursor.executemany(statement, rows)

    async def capture_rejected_rows(self, payload: dict[str, Any]) -> int:
        """把 FILTER 反向查询得到的不合规行写入 Doris 错误表。"""
        query = str(payload.get("error_query", "")).strip()
        if not query:
            return 0
        # 编译器只会生成 SELECT；额外检查防止运行事实被篡改后执行多条语句。
        if not query.upper().startswith("SELECT ") or ";" in query:
            raise RuntimeCompilationError("错误行查询不是受控 SELECT")
        columns = [item.strip() for item in str(payload.get("error_columns", "")).split(",")]
        if not columns or any(not item for item in columns):
            raise RuntimeCompilationError("错误表缺少目标字段列表")
        quoted_columns = ", ".join(_quote_identifier(item) for item in columns)
        source_credentials = await self._source_credentials(payload)
        source_host = str(payload.get("source_host", "")).strip()
        source_database = str(payload.get("source_database", "")).strip()
        try:
            source_port = int(payload.get("source_port", 0))
        except (TypeError, ValueError) as exc:
            raise RuntimeCompilationError("MySQL 源端端口无效") from exc
        if not source_host or not source_database or not source_port:
            raise RuntimeCompilationError("MySQL 源端运行元数据不完整")
        source_connection = await asyncio.to_thread(
            pymysql.connect,
            host=source_host,
            port=source_port,
            user=source_credentials["username"],
            password=source_credentials["password"],
            database=source_database,
            connect_timeout=max(1, int(self.settings.health_check_timeout_seconds)),
            read_timeout=max(1, int(self.settings.health_check_timeout_seconds)),
        )
        try:
            rows = await asyncio.to_thread(self._read_rows, source_connection, query)
        finally:
            await asyncio.to_thread(source_connection.close)
        await self._run_sql(payload, [f"TRUNCATE TABLE {self._table(payload, 'error_table')}"])
        if not rows:
            return 0
        target_credentials = await self._credentials(payload)
        target_host = str(payload.get("target_host", "")).strip()
        target_database = str(payload.get("target_database", "")).strip()
        try:
            target_port = int(payload.get("target_port", 0))
        except (TypeError, ValueError) as exc:
            raise RuntimeCompilationError("Doris 目标端口无效") from exc
        if not target_host or not target_database or not target_port:
            raise RuntimeCompilationError("Doris 目标运行元数据不完整")
        target_connection = await asyncio.to_thread(
            pymysql.connect,
            host=target_host,
            port=target_port,
            user=target_credentials["username"],
            password=target_credentials["password"],
            database=target_database,
            connect_timeout=max(1, int(self.settings.health_check_timeout_seconds)),
            read_timeout=max(1, int(self.settings.health_check_timeout_seconds)),
            write_timeout=max(1, int(self.settings.health_check_timeout_seconds)),
            autocommit=True,
        )
        try:
            statement = (
                f"INSERT INTO {self._table(payload, 'error_table')} "
                f"({quoted_columns}) VALUES ({', '.join(['%s'] * len(columns))})"
            )
            await asyncio.to_thread(self._write_rows, target_connection, statement, rows)
        finally:
            await asyncio.to_thread(target_connection.close)
        return len(rows)

    @staticmethod
    def _table(payload: dict[str, Any], key: str) -> str:
        """读取并引用运行元数据中的表名。"""
        value = str(payload.get(key, "")).strip()
        if not value:
            raise RuntimeCompilationError(f"Doris 运行载荷缺少 {key}")
        return _quote_identifier(value)

    async def prepare_shadow(self, payload: dict[str, Any]) -> None:
        """按目标表结构创建影子表和错误表，并清空本次执行的临时数据。"""
        _quote_identifier(str(payload.get("target_database", "")))
        target = self._table(payload, "target_table")
        shadow = self._table(payload, "shadow_table")
        error = self._table(payload, "error_table")
        await self._run_sql(
            payload,
            [
                f"CREATE TABLE IF NOT EXISTS {shadow} LIKE {target}",
                f"TRUNCATE TABLE {shadow}",
                f"CREATE TABLE IF NOT EXISTS {error} LIKE {target}",
                f"TRUNCATE TABLE {error}",
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

    async def capture_rejected_rows(self, payload: dict[str, Any]) -> int:
        """在主作业成功后回收 FILTER 拒绝行，供质量报告和错误表使用。"""
        return await self.target.capture_rejected_rows(payload)

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
