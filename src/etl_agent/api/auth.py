"""本地开发账号注册、登录和当前用户 API。"""

from fastapi import APIRouter, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from etl_agent.api.auth_dependencies import CurrentUser, DbSession
from etl_agent.api.auth_models import LoginRequest, RegisterRequest, TokenResponse, UserResponse
from etl_agent.api.errors import ApiError
from etl_agent.infrastructure.models import (
    Project,
    ProjectMembership,
    ProjectRole,
    ProjectRoleGrant,
    User,
)
from etl_agent.infrastructure.security import create_access_token, hash_password, verify_password

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register_user(
    payload: RegisterRequest,
    request: Request,
    session: DbSession,
) -> User:
    """仅在 development 环境提供本地用户注册，可直接绑定 Checker 职责。"""
    if request.app.state.settings.app_env != "development":
        raise ApiError("REGISTRATION_DISABLED", "当前环境不允许本地注册", status_code=403)

    # Checker 注册必须同时填写项目编码和职责槽，避免产生半完成的成员关系。
    if (payload.project_code is None) != (payload.project_role is None):
        raise ApiError(
            "REGISTRATION_ASSIGNMENT_INVALID",
            "项目编码和 Checker 职责必须同时填写",
            status_code=422,
        )
    if payload.project_role not in {None, ProjectRole.CHECKER_1, ProjectRole.CHECKER_2}:
        raise ApiError(
            "REGISTRATION_ROLE_INVALID",
            "注册页只能直接分配 Checker 1 或 Checker 2",
            status_code=422,
        )

    project: Project | None = None
    if payload.project_code is not None and payload.project_role is not None:
        project = await session.scalar(select(Project).where(Project.code == payload.project_code))
        if project is None:
            raise ApiError(
                "PROJECT_NOT_FOUND", "项目编码不存在，请先由 Maker 创建项目", status_code=404
            )
        occupied = await session.scalar(
            select(ProjectRoleGrant.id).where(
                ProjectRoleGrant.project_id == project.id,
                ProjectRoleGrant.role == payload.project_role.value,
            )
        )
        if occupied is not None:
            raise ApiError(
                "CHECKER_ROLE_OCCUPIED",
                "该 Checker 职责槽已经被占用",
                status_code=409,
            )

    user = User(
        username=payload.username,
        display_name=payload.display_name,
        password_hash=hash_password(payload.password),
    )
    session.add(user)
    try:
        await session.flush()
        # 开发环境注册 Checker 时，自动建立项目成员关系和职责槽。
        if project is not None and payload.project_role is not None:
            session.add(ProjectMembership(project_id=project.id, user_id=user.id))
            session.add(
                ProjectRoleGrant(
                    project_id=project.id,
                    user_id=user.id,
                    role=payload.project_role.value,
                )
            )
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        if project is not None and payload.project_role is not None:
            occupied = await session.scalar(
                select(ProjectRoleGrant.id).where(
                    ProjectRoleGrant.project_id == project.id,
                    ProjectRoleGrant.role == payload.project_role.value,
                )
            )
            if occupied is not None:
                raise ApiError(
                    "CHECKER_ROLE_OCCUPIED",
                    "该 Checker 职责槽已经被占用",
                    status_code=409,
                ) from exc
        raise ApiError("USERNAME_EXISTS", "用户名已存在", status_code=409) from exc
    await session.refresh(user)
    return user


@router.post("/login", response_model=TokenResponse)
async def login_user(
    payload: LoginRequest,
    request: Request,
    session: DbSession,
) -> TokenResponse:
    """校验本地账号密码并签发短时 JWT 访问令牌。"""
    user = await session.scalar(select(User).where(User.username == payload.username))
    if (
        user is None
        or not user.is_active
        or not verify_password(payload.password, user.password_hash)
    ):
        raise ApiError("AUTH_INVALID", "用户名或密码错误", status_code=401)
    token, expires_in = create_access_token(str(user.id), request.app.state.settings)
    return TokenResponse(access_token=token, expires_in=expires_in)


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: CurrentUser) -> User:
    """返回当前访问令牌对应的用户信息。"""
    return current_user
