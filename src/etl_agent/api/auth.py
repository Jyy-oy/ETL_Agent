"""本地开发账号注册、登录和当前用户 API。"""

from fastapi import APIRouter, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from etl_agent.api.auth_dependencies import CurrentUser, DbSession
from etl_agent.api.auth_models import LoginRequest, RegisterRequest, TokenResponse, UserResponse
from etl_agent.api.errors import ApiError
from etl_agent.infrastructure.models import User
from etl_agent.infrastructure.security import create_access_token, hash_password, verify_password

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register_user(
    payload: RegisterRequest,
    request: Request,
    session: DbSession,
) -> User:
    """仅在 development 环境提供本地用户注册，生产环境须接入受管身份系统。"""
    if request.app.state.settings.app_env != "development":
        raise ApiError("REGISTRATION_DISABLED", "当前环境不允许本地注册", status_code=403)
    user = User(
        username=payload.username,
        display_name=payload.display_name,
        password_hash=hash_password(payload.password),
    )
    session.add(user)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
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
