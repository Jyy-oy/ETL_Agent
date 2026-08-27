"""真实合成 MySQL -> Doris 数据面编译和发布适配器测试。"""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from etl_agent.config import Settings
from etl_agent.domain.generation import EtlPlan
from etl_agent.infrastructure.models import Connection, MetadataProfile
from etl_agent.workers.real_data_plane import DorisTargetAdapter, compile_runtime_job


class _FakeResult:
    """模拟 SQLAlchemy execute(...).first() 返回值。"""

    def __init__(self, row) -> None:
        """保存一行 Profile 和 Connection。"""
        self.row = row

    def first(self):
        """返回预置行。"""
        return self.row


class _FakeSession:
    """按 Profile ID 顺序返回预置连接元数据。"""

    def __init__(self, rows) -> None:
        """保存测试查询结果队列。"""
        self.rows = iter(rows)

    async def execute(self, statement):
        """忽略 SQL 表达式并返回下一组测试数据。"""
        del statement
        return _FakeResult(next(self.rows))


class _FakeSecretProvider:
    """返回不落库的合成 MySQL/Doris 凭据。"""

    async def read(self, secret_ref: str):
        """按 SecretRef 返回对应的学习账号。"""
        if secret_ref.endswith("mysql"):
            return {"username": "etl_demo", "password": "mysql-dev", "database": "etl_demo"}
        return {"username": "root", "password": "", "database": "etl_demo_dw"}


def _profile(
    connection_id,
    table_name: str,
    fields: list[str],
    type_overrides: dict[str, str] | None = None,
) -> MetadataProfile:
    """构造单表元数据 Profile，并支持覆盖指定字段类型。"""
    type_by_field = {
        "order_id": "bigint",
        "customer_id": "bigint",
        "order_status": "varchar",
        "amount": "decimal",
        "ordered_at": "datetime",
        "source_batch": "varchar",
    }
    if type_overrides:
        type_by_field.update(type_overrides)
    return MetadataProfile(
        id=uuid4(),
        connection_id=connection_id,
        fingerprint="a" * 64,
        schema_snapshot={
            "tables": [
                {
                    "schema": "etl_demo" if table_name == "demo_orders" else "etl_demo_dw",
                    "name": table_name,
                    "columns": [
                        {"name": field, "data_type": type_by_field.get(field, "varchar")}
                        for field in fields
                    ],
                }
            ]
        },
        redacted_sample={},
        estimated_row_count=10,
    )


def _connection(connection_id, kind: str, secret_ref: str, database: str) -> Connection:
    """构造测试用 MySQL 或 Doris 连接元数据。"""
    return Connection(
        id=connection_id,
        project_id=uuid4(),
        code=f"{kind}_test",
        name=kind,
        connection_type=kind,
        host="192.168.181.128",
        port=3306 if kind == "mysql" else 9030,
        database_name=database,
        username=None,
        secret_ref=secret_ref,
        options={},
    )


@pytest.mark.asyncio
async def test_compile_runtime_job_builds_real_connector_without_persisted_metadata_secret() -> (
    None
):
    """验证真实编译器生成 Jdbc/Doris 配置并只返回安全运行元数据。"""
    source_connection_id = uuid4()
    target_connection_id = uuid4()
    fields = ["order_id", "customer_id", "order_status", "amount", "ordered_at", "source_batch"]
    source_connection = _connection(source_connection_id, "mysql", "etl-agent/mysql", "etl_demo")
    target_connection = _connection(target_connection_id, "doris", "etl-agent/doris", "etl_demo_dw")
    source_profile = _profile(source_connection_id, "demo_orders", fields)
    target_profile = _profile(target_connection_id, "orders_current", fields)
    plan = EtlPlan.model_validate(
        {
            "source": {"connection_id": source_connection_id, "profile_id": source_profile.id},
            "target": {"connection_id": target_connection_id, "profile_id": target_profile.id},
            "field_mappings": [{"source_field": field, "target_field": field} for field in fields],
            "hocon": "env { parallelism = 1 }",
        }
    )
    version = SimpleNamespace(
        source_profile_ids=[str(source_profile.id)],
        target_profile_ids=[str(target_profile.id)],
        etl_plan_json=plan.model_dump(mode="json"),
    )
    execution = SimpleNamespace(id=uuid4())
    artifact = await compile_runtime_job(
        _FakeSession([(source_profile, source_connection), (target_profile, target_connection)]),
        execution,
        version,
        settings=Settings(seatunnel_mysql_host="mysql", seatunnel_doris_fenodes="doris-fe:8030"),
        provider=_FakeSecretProvider(),
    )
    assert "Jdbc" in artifact["hocon"]
    assert "Doris" in artifact["hocon"]
    assert 'user = "etl_demo"' in artifact["hocon"]
    assert artifact["target_database"] == "etl_demo_dw"
    assert all("password" not in key for key in artifact)


@pytest.mark.asyncio
async def test_compile_runtime_job_omits_redundant_cast_and_keeps_filter() -> None:
    """验证源、目标类型一致时不生成冗余 CAST，同时保留正数过滤。"""
    source_connection_id = uuid4()
    target_connection_id = uuid4()
    fields = ["order_id", "customer_id", "order_status", "amount", "ordered_at", "source_batch"]
    source_connection = _connection(source_connection_id, "mysql", "etl-agent/mysql", "etl_demo")
    target_connection = _connection(target_connection_id, "doris", "etl-agent/doris", "etl_demo_dw")
    source_profile = _profile(source_connection_id, "demo_orders", fields)
    target_profile = _profile(target_connection_id, "orders_current", fields)
    plan = EtlPlan.model_validate(
        {
            "source": {"connection_id": source_connection_id, "profile_id": source_profile.id},
            "target": {"connection_id": target_connection_id, "profile_id": target_profile.id},
            "field_mappings": [
                {"source_field": field, "target_field": field}
                for field in fields
                if field != "amount"
            ]
            + [{"source_field": "amount", "target_field": "amount", "transform": "cast"}],
            "transforms": [
                {
                    "operation": "filter",
                    "source_fields": ["amount"],
                    "parameters": {"condition": "amount > 0"},
                }
            ],
            "quality_contract": {"error_table_suffix": "__bad_rows"},
            "hocon": "env { parallelism = 1 }",
        }
    )
    version = SimpleNamespace(
        source_profile_ids=[str(source_profile.id)],
        target_profile_ids=[str(target_profile.id)],
        etl_plan_json=plan.model_dump(mode="json"),
    )
    artifact = await compile_runtime_job(
        _FakeSession([(source_profile, source_connection), (target_profile, target_connection)]),
        SimpleNamespace(id=uuid4()),
        version,
        settings=Settings(seatunnel_mysql_host="mysql", seatunnel_doris_fenodes="doris-fe:8030"),
        provider=_FakeSecretProvider(),
    )

    assert (
        "SELECT `order_id`, `customer_id`, `order_status`, "
        "`amount`, `ordered_at`, `source_batch`" in artifact["hocon"]
    )
    assert "CAST(`customer_id` AS BIGINT)" not in artifact["hocon"]
    assert "`email`" not in artifact["hocon"]
    assert "WHERE `amount` > 0" in artifact["hocon"]
    assert "WHERE NOT (`amount` > 0)" in artifact["error_query"]
    assert "OR (`amount` > 0) IS NULL" in artifact["error_query"]
    assert artifact["error_columns"] == ",".join(fields)
    assert artifact["error_table"].startswith("orders_current__bad_rows_")


@pytest.mark.asyncio
async def test_compile_runtime_job_accepts_legacy_expression_filter() -> None:
    """验证历史冻结计划使用 expression 时仍可编译，避免升级破坏已创建版本。"""
    source_connection_id = uuid4()
    target_connection_id = uuid4()
    fields = ["order_id", "amount"]
    source_connection = _connection(source_connection_id, "mysql", "etl-agent/mysql", "etl_demo")
    target_connection = _connection(target_connection_id, "doris", "etl-agent/doris", "etl_demo_dw")
    source_profile = _profile(source_connection_id, "demo_orders", fields)
    target_profile = _profile(target_connection_id, "orders_current", fields)
    plan = EtlPlan.model_validate(
        {
            "source": {"connection_id": source_connection_id, "profile_id": source_profile.id},
            "target": {"connection_id": target_connection_id, "profile_id": target_profile.id},
            "field_mappings": [{"source_field": field, "target_field": field} for field in fields],
            "transforms": [
                {
                    "operation": "filter",
                    "source_fields": ["amount"],
                    "parameters": {"expression": "amount > 0"},
                }
            ],
            "hocon": "env { parallelism = 1 }",
        }
    )
    version = SimpleNamespace(
        source_profile_ids=[str(source_profile.id)],
        target_profile_ids=[str(target_profile.id)],
        etl_plan_json=plan.model_dump(mode="json"),
    )
    artifact = await compile_runtime_job(
        _FakeSession([(source_profile, source_connection), (target_profile, target_connection)]),
        SimpleNamespace(id=uuid4()),
        version,
        settings=Settings(seatunnel_mysql_host="mysql", seatunnel_doris_fenodes="doris-fe:8030"),
        provider=_FakeSecretProvider(),
    )
    assert "WHERE `amount` > 0" in artifact["hocon"]


@pytest.mark.asyncio
async def test_compile_runtime_job_routes_nullable_required_rows_to_error_query() -> None:
    """验证必填字段为 NULL 的记录会进入反向错误查询，而不是被 SQL 三值逻辑丢弃。"""
    source_connection_id = uuid4()
    target_connection_id = uuid4()
    fields = ["order_id", "amount"]
    source_connection = _connection(source_connection_id, "mysql", "etl-agent/mysql", "etl_demo")
    target_connection = _connection(target_connection_id, "doris", "etl-agent/doris", "etl_demo_dw")
    source_profile = _profile(source_connection_id, "demo_orders", fields)
    target_profile = _profile(target_connection_id, "orders_current", fields)
    plan = EtlPlan.model_validate(
        {
            "source": {"connection_id": source_connection_id, "profile_id": source_profile.id},
            "target": {"connection_id": target_connection_id, "profile_id": target_profile.id},
            "field_mappings": [{"source_field": field, "target_field": field} for field in fields],
            "quality_contract": {"required_fields": fields, "error_table_suffix": "__errors"},
            "hocon": "env { parallelism = 1 }",
        }
    )
    version = SimpleNamespace(
        source_profile_ids=[str(source_profile.id)],
        target_profile_ids=[str(target_profile.id)],
        etl_plan_json=plan.model_dump(mode="json"),
    )
    artifact = await compile_runtime_job(
        _FakeSession([(source_profile, source_connection), (target_profile, target_connection)]),
        SimpleNamespace(id=uuid4()),
        version,
        settings=Settings(seatunnel_mysql_host="mysql", seatunnel_doris_fenodes="doris-fe:8030"),
        provider=_FakeSecretProvider(),
    )

    assert "WHERE `order_id` IS NOT NULL AND `amount` IS NOT NULL" in artifact["hocon"]
    assert "WHERE NOT (`order_id` IS NOT NULL AND `amount` IS NOT NULL)" in artifact["error_query"]


@pytest.mark.asyncio
async def test_compile_runtime_job_rejects_filter_without_condition_or_expression() -> None:
    """验证过滤规则缺少两个兼容参数时返回稳定的编译错误。"""
    source_connection_id = uuid4()
    target_connection_id = uuid4()
    fields = ["order_id", "amount"]
    source_connection = _connection(source_connection_id, "mysql", "etl-agent/mysql", "etl_demo")
    target_connection = _connection(target_connection_id, "doris", "etl-agent/doris", "etl_demo_dw")
    source_profile = _profile(source_connection_id, "demo_orders", fields)
    target_profile = _profile(target_connection_id, "orders_current", fields)
    plan = EtlPlan.model_validate(
        {
            "source": {"connection_id": source_connection_id, "profile_id": source_profile.id},
            "target": {"connection_id": target_connection_id, "profile_id": target_profile.id},
            "field_mappings": [{"source_field": field, "target_field": field} for field in fields],
            "transforms": [{"operation": "filter", "source_fields": ["amount"]}],
            "hocon": "env { parallelism = 1 }",
        }
    )
    version = SimpleNamespace(
        source_profile_ids=[str(source_profile.id)],
        target_profile_ids=[str(target_profile.id)],
        etl_plan_json=plan.model_dump(mode="json"),
    )
    with pytest.raises(Exception, match="过滤转换缺少 condition"):
        await compile_runtime_job(
            _FakeSession(
                [(source_profile, source_connection), (target_profile, target_connection)]
            ),
            SimpleNamespace(id=uuid4()),
            version,
            settings=Settings(
                seatunnel_mysql_host="mysql", seatunnel_doris_fenodes="doris-fe:8030"
            ),
            provider=_FakeSecretProvider(),
        )


@pytest.mark.asyncio
async def test_compile_runtime_job_uses_mysql_cast_dialect_for_type_mismatch() -> None:
    """验证类型确实不一致时使用 MySQL 支持的 CAST 类型，而非通用类型名。"""
    source_connection_id = uuid4()
    target_connection_id = uuid4()
    fields = ["order_id", "customer_id"]
    source_connection = _connection(source_connection_id, "mysql", "etl-agent/mysql", "etl_demo")
    target_connection = _connection(target_connection_id, "doris", "etl-agent/doris", "etl_demo_dw")
    source_profile = _profile(source_connection_id, "demo_orders", fields)
    target_profile = _profile(
        target_connection_id,
        "orders_current",
        fields,
        type_overrides={"customer_id": "varchar"},
    )
    plan = EtlPlan.model_validate(
        {
            "source": {"connection_id": source_connection_id, "profile_id": source_profile.id},
            "target": {"connection_id": target_connection_id, "profile_id": target_profile.id},
            "field_mappings": [{"source_field": field, "target_field": field} for field in fields],
            "transforms": [
                {
                    "operation": "cast",
                    "source_fields": ["customer_id"],
                    "target_field": "customer_id",
                    "parameters": {"type": "varchar"},
                }
            ],
            "hocon": "env { parallelism = 1 }",
        }
    )
    version = SimpleNamespace(
        source_profile_ids=[str(source_profile.id)],
        target_profile_ids=[str(target_profile.id)],
        etl_plan_json=plan.model_dump(mode="json"),
    )
    artifact = await compile_runtime_job(
        _FakeSession([(source_profile, source_connection), (target_profile, target_connection)]),
        SimpleNamespace(id=uuid4()),
        version,
        settings=Settings(seatunnel_mysql_host="mysql", seatunnel_doris_fenodes="doris-fe:8030"),
        provider=_FakeSecretProvider(),
    )

    assert "CAST(`customer_id` AS CHAR(255)) AS `customer_id`" in artifact["hocon"]
    assert "CAST(`customer_id` AS BIGINT)" not in artifact["hocon"]


@pytest.mark.asyncio
async def test_compile_runtime_job_rejects_unsafe_filter_condition() -> None:
    """验证过滤条件不能注入任意 SQL 片段。"""
    source_connection_id = uuid4()
    target_connection_id = uuid4()
    fields = ["order_id", "amount"]
    source_connection = _connection(source_connection_id, "mysql", "etl-agent/mysql", "etl_demo")
    target_connection = _connection(target_connection_id, "doris", "etl-agent/doris", "etl_demo_dw")
    source_profile = _profile(source_connection_id, "demo_orders", fields)
    target_profile = _profile(target_connection_id, "orders_current", fields)
    plan = EtlPlan.model_validate(
        {
            "source": {"connection_id": source_connection_id, "profile_id": source_profile.id},
            "target": {"connection_id": target_connection_id, "profile_id": target_profile.id},
            "field_mappings": [{"source_field": field, "target_field": field} for field in fields],
            "transforms": [
                {
                    "operation": "filter",
                    "source_fields": ["amount"],
                    "parameters": {"condition": "amount > 0 OR 1=1"},
                }
            ],
            "hocon": "env { parallelism = 1 }",
        }
    )
    version = SimpleNamespace(
        source_profile_ids=[str(source_profile.id)],
        target_profile_ids=[str(target_profile.id)],
        etl_plan_json=plan.model_dump(mode="json"),
    )
    with pytest.raises(Exception, match="过滤条件"):
        await compile_runtime_job(
            _FakeSession(
                [(source_profile, source_connection), (target_profile, target_connection)]
            ),
            SimpleNamespace(id=uuid4()),
            version,
            settings=Settings(
                seatunnel_mysql_host="mysql", seatunnel_doris_fenodes="doris-fe:8030"
            ),
            provider=_FakeSecretProvider(),
        )


@pytest.mark.asyncio
async def test_doris_target_adapter_prepares_shadow_and_error_tables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证每次执行都会创建并清空影子表和错误表。"""
    adapter = DorisTargetAdapter(_FakeSecretProvider(), Settings(health_check_timeout_seconds=1))
    statements: list[str] = []

    async def capture(payload, sql):
        """捕获受控 DDL，避免单元测试访问真实 Doris。"""
        del payload
        statements.extend(sql)

    monkeypatch.setattr(adapter, "_run_sql", capture)
    await adapter.prepare_shadow(
        {
            "target_database": "etl_demo_dw",
            "target_table": "orders_current",
            "shadow_table": "orders_current__shadow_abc",
            "error_table": "orders_current__errors_abc",
        }
    )
    assert statements == [
        "CREATE TABLE IF NOT EXISTS `orders_current__shadow_abc` LIKE `orders_current`",
        "TRUNCATE TABLE `orders_current__shadow_abc`",
        "CREATE TABLE IF NOT EXISTS `orders_current__errors_abc` LIKE `orders_current`",
        "TRUNCATE TABLE `orders_current__errors_abc`",
    ]


@pytest.mark.asyncio
async def test_doris_target_adapter_captures_rejected_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证拒绝行回收会清空错误表并使用参数绑定批量写入。"""
    adapter = DorisTargetAdapter(_FakeSecretProvider(), Settings(health_check_timeout_seconds=1))
    statements: list[str] = []
    written: list[tuple[str, list[tuple[object, ...]]]] = []

    async def capture_sql(payload, sql):
        """捕获错误表清理 DDL，避免访问真实 Doris。"""
        del payload
        statements.extend(sql)

    def fake_read_rows(connection, query):
        """返回两条合成拒绝行，验证查询会被传入驱动。"""
        del connection
        assert query.startswith("SELECT ")
        return [(1, 10001, "paid", -1.0, "2025-01-01", "bad")]

    def fake_write_rows(connection, statement, rows):
        """捕获参数化 INSERT，确认值不会拼接进 SQL。"""
        del connection
        written.append((statement, rows))

    class FakeConnection:
        """满足异步线程关闭调用的最小连接替身。"""

        def close(self):
            """模拟关闭连接。"""

    monkeypatch.setattr(adapter, "_run_sql", capture_sql)
    monkeypatch.setattr(adapter, "_read_rows", fake_read_rows)
    monkeypatch.setattr(adapter, "_write_rows", fake_write_rows)
    monkeypatch.setattr(
        "etl_agent.workers.real_data_plane.pymysql.connect", lambda **kwargs: FakeConnection()
    )

    count = await adapter.capture_rejected_rows(
        {
            "source_host": "192.168.181.128",
            "source_port": "3306",
            "source_secret_ref": "etl-agent/mysql",
            "source_database": "etl_demo",
            "error_query": (
                "SELECT `order_id`, `customer_id`, `order_status`, `amount`, `ordered_at`, "
                "`source_batch` FROM `etl_demo`.`demo_orders` WHERE NOT (`amount` > 0)"
            ),
            "error_columns": "order_id,customer_id,order_status,amount,ordered_at,source_batch",
            "target_host": "192.168.181.128",
            "target_port": "9030",
            "target_database": "etl_demo_dw",
            "target_secret_ref": "etl-agent/doris",
            "error_table": "orders_current__errors_test",
        }
    )

    assert count == 1
    assert statements == ["TRUNCATE TABLE `orders_current__errors_test`"]
    assert written[0][0] == (
        "INSERT INTO `orders_current__errors_test` ("
        "`order_id`, `customer_id`, `order_status`, `amount`, `ordered_at`, `source_batch`"
        ") VALUES (%s, %s, %s, %s, %s, %s)"
    )
    assert written[0][1] == [(1, 10001, "paid", -1.0, "2025-01-01", "bad")]


@pytest.mark.asyncio
async def test_doris_target_adapter_uses_atomic_replace_sql(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证目标适配器使用 Doris REPLACE WITH swap，而不是先删正式表。"""
    adapter = DorisTargetAdapter(_FakeSecretProvider(), Settings(health_check_timeout_seconds=1))
    statements: list[str] = []

    async def capture(payload, sql):
        """捕获受控 DDL，避免单元测试访问真实 Doris。"""
        del payload
        statements.extend(sql)

    monkeypatch.setattr(adapter, "_run_sql", capture)
    payload = {
        "target_host": "192.168.181.128",
        "target_port": "9030",
        "target_database": "etl_demo_dw",
        "target_table": "orders_current",
        "shadow_table": "orders_current__shadow_abc",
        "target_secret_ref": "etl-agent/doris",
    }
    assert await adapter.atomic_swap(payload)
    assert statements == [
        "ALTER TABLE `orders_current` REPLACE WITH TABLE `orders_current__shadow_abc` "
        "PROPERTIES ('swap' = 'true')"
    ]
