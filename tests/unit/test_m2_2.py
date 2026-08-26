"""M2.2 SecretProvider、连接测试和只读 Profile 单元测试。"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from etl_agent.api.connection_models import ProfileResponse
from etl_agent.config import Settings
from etl_agent.infrastructure.connection_testing import _connection_parameters, run_connection_test
from etl_agent.infrastructure.models import Connection, MetadataProfile, ProfileStatus
from etl_agent.infrastructure.profiling import _build_profile_sync, _metadata_value
from etl_agent.infrastructure.secrets import (
    SecretProviderError,
    normalize_vault_path,
)


class FakeProvider:
    """返回固定凭据的测试 SecretProvider。"""

    async def read(self, secret_ref: str) -> dict[str, str]:
        """返回测试密码，不访问真实 Vault。"""
        return {"password": "test-password", "username": "etl"}


class FakeCursor:
    """根据 SQL 类型返回固定元数据或样本行的测试游标。"""

    def __init__(self, client: "FakeClient") -> None:
        self.client = client
        self.rows: list[dict[str, object]] = []

    def __enter__(self) -> "FakeCursor":
        """进入测试游标上下文。"""
        return self

    def __exit__(self, *_args: object) -> None:
        """退出测试游标上下文。"""

    def execute(self, query: str, _params: object = None) -> None:
        """按查询关键字选择测试数据集。"""
        if "information_schema.columns" in query:
            self.rows = [
                {
                    "table_schema": "app",
                    "table_name": "users",
                    "column_name": "email",
                    "data_type": "varchar",
                    "is_nullable": "NO",
                    "ordinal_position": 1,
                },
                {
                    "table_schema": "app",
                    "table_name": "users",
                    "column_name": "name",
                    "data_type": "varchar",
                    "is_nullable": "YES",
                    "ordinal_position": 2,
                },
            ]
        elif "information_schema.tables" in query:
            self.rows = [{"table_schema": "app", "table_name": "users", "table_rows": 3}]
        elif query.strip().upper().startswith("SELECT 1"):
            self.rows = [{"1": 1}]
        else:
            self.rows = [{"email": "person@example.com", "name": "Alice"}]

    def fetchall(self) -> list[dict[str, object]]:
        """返回当前查询的全部测试结果。"""
        return self.rows

    def fetchone(self) -> dict[str, object] | None:
        """返回当前查询的一条测试结果。"""
        return self.rows[0] if self.rows else None


class FakeClient:
    """提供游标和关闭方法的同步数据库客户端替身。"""

    def cursor(self) -> FakeCursor:
        """创建一个绑定当前客户端的测试游标。"""
        return FakeCursor(self)

    def close(self) -> None:
        """模拟关闭数据库连接。"""


def _connection(connection_type: str = "mysql") -> Connection:
    """构造不含密码的测试连接登记对象。"""
    return Connection(
        id=uuid4(),
        project_id=uuid4(),
        code="source",
        name="测试源",
        connection_type=connection_type,
        host="db.internal",
        port=3306,
        database_name="app",
        username="etl",
        secret_ref="mysql/source",
        options={},
    )


def test_normalize_vault_path_applies_prefix_and_rejects_traversal() -> None:
    """验证 Vault KV v2 路径会统一 mount/data 前缀并拒绝目录穿越。"""
    assert normalize_vault_path("secret/data/mysql", "etl-agent", "secret") == "etl-agent/mysql"
    with pytest.raises(SecretProviderError):
        normalize_vault_path("../password", "etl-agent", "secret")


def test_empty_password_is_allowed_for_local_doris() -> None:
    """验证本地 Doris 空密码账号不会被误判为缺少凭据。"""
    parameters = _connection_parameters(
        _connection("doris"),
        {"username": "root", "password": ""},
        5,
    )

    assert parameters["password"] == ""


@pytest.mark.asyncio
async def test_mysql_connection_probe_returns_passed(monkeypatch: pytest.MonkeyPatch) -> None:
    """验证 MySQL 兼容连接执行 SELECT 1 后返回通过。"""

    async def fake_open(*_args: object, **_kwargs: object) -> FakeClient:
        """返回测试数据库客户端。"""
        return FakeClient()

    monkeypatch.setattr(
        "etl_agent.infrastructure.connection_testing.open_mysql_compatible_connection",
        fake_open,
    )
    result = await run_connection_test(_connection(), FakeProvider(), Settings(_env_file=None))

    assert result.status == "passed"


@pytest.mark.asyncio
async def test_unsupported_connection_type_is_not_attempted() -> None:
    """验证未实现的连接类型不会访问 SecretProvider 或数据库。"""
    result = await run_connection_test(
        _connection("postgresql"), FakeProvider(), Settings(_env_file=None)
    )

    assert result.status == "unsupported"


def test_profile_redacts_samples_and_calculates_fingerprint() -> None:
    """验证 Profile 包含字段摘要、脱敏样本、近似行数和 SHA-256 指纹。"""
    result = _build_profile_sync(FakeClient(), "app", [], 2)

    assert result.estimated_row_count == 3
    assert result.redacted_sample["app.users"][0]["email"] == "[REDACTED]"
    assert result.redacted_sample["app.users"][0]["name"] == "Alice"
    assert len(result.fingerprint) == 64


def test_metadata_value_accepts_uppercase_information_schema_keys() -> None:
    """验证 MySQL 驱动返回大写 information_schema 列名时仍能读取元数据。"""
    assert _metadata_value({"TABLE_SCHEMA": "etl_demo"}, "table_schema") == "etl_demo"


def test_profile_response_serializes_schema_json_alias() -> None:
    """验证 Profile ORM 的 schema_snapshot 对外序列化为稳定的 schema_json。"""
    profile = MetadataProfile(
        id=uuid4(),
        connection_id=uuid4(),
        profile_version="v1",
        fingerprint="a" * 64,
        schema_snapshot={"version": "v1", "tables": []},
        redacted_sample={},
        status=ProfileStatus.READY,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    response = ProfileResponse.model_validate(profile)

    assert response.model_dump(by_alias=True)["schema_json"] == {"version": "v1", "tables": []}
