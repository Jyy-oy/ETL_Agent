"""认证令牌、项目成员和职责槽依赖。"""

from typing import Annotated
from uuid import UUID

import jwt
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.api.errors import ApiError
from etl_agent.infrastructure.database import get_db_session
from etl_agent.infrastructure.models import ProjectMembership, ProjectRole, ProjectRoleGrant, User
from etl_agent.infrastructure.security import decode_access_token

_bearer_scheme = HTTPBearer(auto_error=False)
DbSession = Annotated[AsyncSession, Depends(get_db_session)]
BearerCredentials = Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)]


async def get_current_user(
    request: Request,
    credentials: BearerCredentials,
    session: DbSession,
) -> User:
    """校验 Bearer JWT 并加载当前有效用户。"""
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise ApiError("AUTH_REQUIRED", "需要提供 Bearer 访问令牌", status_code=401)
    try:
        payload = decode_access_token(credentials.credentials, request.app.state.settings)
        user_id = UUID(str(payload["sub"]))
    except (jwt.InvalidTokenError, KeyError, TypeError, ValueError) as exc:
        raise ApiError("AUTH_INVALID", "访问令牌无效或已过期", status_code=401) from exc
    user = await session.get(User, user_id)
    if user is None or not user.is_active:
        raise ApiError("AUTH_INVALID", "用户不存在或已停用", status_code=401)
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def require_project_membership(
    project_id: UUID,
    current_user: CurrentUser,
    session: DbSession,
) -> ProjectMembership:
    """校验当前用户属于指定项目并返回有效成员关系。"""
    membership = await session.scalar(
        select(ProjectMembership).where(
            ProjectMembership.project_id == project_id,
            ProjectMembership.user_id == current_user.id,
            ProjectMembership.status == "active",
        )
    )
    if membership is None:
        raise ApiError("PROJECT_FORBIDDEN", "当前用户不是该项目的有效成员", status_code=403)
    return membership


async def require_project_role(
    project_id: UUID,
    current_user: CurrentUser,
    session: DbSession,
    allowed_roles: set[ProjectRole],
) -> set[ProjectRole]:
    """校验当前用户在项目中至少拥有一个允许的职责槽。"""
    await require_project_membership(project_id, current_user, session)
    values = await session.scalars(
        select(ProjectRoleGrant.role).where(
            ProjectRoleGrant.project_id == project_id,
            ProjectRoleGrant.user_id == current_user.id,
        )
    )
    roles = {ProjectRole(value) for value in values.all()}
    if not roles & allowed_roles:
        raise ApiError("ROLE_FORBIDDEN", "当前职责槽无权执行该操作", status_code=403)
    return roles
