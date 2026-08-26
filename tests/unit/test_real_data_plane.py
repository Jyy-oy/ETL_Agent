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


def _profile(connection_id, table_name: str, fields: list[str]) -> MetadataProfile:
    """构造单表元数据 Profile。"""
    return MetadataProfile(
        id=uuid4(),
        connection_id=connection_id,
        fingerprint="a" * 64,
        schema_snapshot={
            "tables": [
                {
                    "schema": "etl_demo" if table_name == "demo_orders" else "etl_demo_dw",
                    "name": table_name,
                    "columns": [{"name": field} for field in fields],
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
