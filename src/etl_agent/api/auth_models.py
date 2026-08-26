"""认证、用户和项目访问控制的 API 契约模型。"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from etl_agent.infrastructure.models import ProjectRole


class RegisterRequest(BaseModel):
    """本地开发环境注册用户所需字段，可选绑定项目 Checker 职责。"""

    username: str = Field(min_length=3, max_length=128, pattern=r"^[a-zA-Z0-9_.-]+$")
    display_name: str = Field(min_length=1, max_length=256)
    password: str = Field(min_length=8, max_length=256)
    # 仅用于开发环境便捷注册；生产环境应由项目管理员分配职责。
    project_code: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$",
    )
    project_role: ProjectRole | None = None


class LoginRequest(BaseModel):
    """本地账号登录所需凭据。"""

    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=256)


class UserResponse(BaseModel):
    """不包含密码哈希的用户响应。"""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    username: str
    display_name: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


class TokenResponse(BaseModel):
    """短时访问令牌响应。"""

    access_token: str
    token_type: str = "bearer"
    expires_in: int


class ProjectCreate(BaseModel):
    """创建项目所需的稳定标识和展示名称。"""

    code: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$")
    name: str = Field(min_length=1, max_length=256)


class ProjectResponse(BaseModel):
    """项目基本信息响应。"""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    code: str
    name: str
    status: str
    created_at: datetime
    updated_at: datetime


class MemberCreate(BaseModel):
    """将已有用户加入项目并分配一个职责槽。"""

    user_id: UUID
    role: ProjectRole


class MemberResponse(BaseModel):
    """项目成员及其职责槽响应。"""

    user: UserResponse
    role: ProjectRole
    membership_status: str
