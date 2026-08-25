"""M2.1 连接登记和 Profile 契约测试。"""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from etl_agent.api.connection_models import ConnectionCreate
from etl_agent.infrastructure.models import Base


def test_connection_payload_rejects_secret_options() -> None:
    """验证密码等敏感字段不能通过 options 绕过 SecretRef 约束。"""
    with pytest.raises(ValidationError, match="secret_ref"):
        ConnectionCreate(
            project_id=uuid4(),
            code="mysql_source",
            name="MySQL 源库",
            connection_type="mysql",
            host="db.example.internal",
            port=3306,
            secret_ref="kv/data/etl-agent/mysql-source",
            options={"password": "must-not-be-accepted"},
        )


def test_m2_connection_profile_tables_are_registered() -> None:
    """验证连接和 Profile ORM 表已注册到元数据。"""
    assert {"connections", "metadata_profiles"} <= set(Base.metadata.tables)
