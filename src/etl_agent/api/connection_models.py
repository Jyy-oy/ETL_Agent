"""连接登记和元数据 Profile 的 API 契约模型。"""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from etl_agent.infrastructure.models import ConnectionStatus, ConnectionType, ProfileStatus

_SENSITIVE_OPTION_KEYS = {"password", "secret", "token", "api_key", "access_key", "secret_key"}


class ConnectionCreate(BaseModel):
    """创建项目连接时允许提交的非敏感字段。"""

    project_id: UUID
    code: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$")
    name: str = Field(min_length=1, max_length=256)
    connection_type: ConnectionType
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(gt=0, le=65535)
    database_name: str | None = Field(default=None, max_length=256)
    username: str | None = Field(default=None, max_length=128)
    secret_ref: str = Field(min_length=1, max_length=512)
    options: dict[str, Any] = Field(default_factory=dict)

    @field_validator("options")
    @classmethod
    def reject_secret_options(cls, value: dict[str, Any]) -> dict[str, Any]:
        """拒绝把密码、令牌等敏感字段伪装在扩展选项中提交。"""
        sensitive_keys = {key.lower() for key in value} & _SENSITIVE_OPTION_KEYS
        if sensitive_keys:
            raise ValueError(f"敏感字段必须通过 secret_ref 管理: {sorted(sensitive_keys)}")
        return value


class ConnectionResponse(BaseModel):
    """连接查询响应，明确不包含任何凭据字段。"""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    code: str
    name: str
    connection_type: ConnectionType
    host: str
    port: int
    database_name: str | None
    username: str | None
    secret_ref: str
    options: dict[str, Any]
    status: ConnectionStatus
    created_at: datetime
    updated_at: datetime


class ProfileResponse(BaseModel):
    """脱敏元数据 Profile 查询响应。"""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: UUID
    connection_id: UUID
    profile_version: str
    fingerprint: str
    schema_snapshot: dict[str, Any] = Field(
        validation_alias="schema_snapshot",
        serialization_alias="schema_json",
    )
    redacted_sample: dict[str, Any]
    estimated_row_count: int | None
    status: ProfileStatus
    error_detail: str | None
    created_at: datetime
    updated_at: datetime
