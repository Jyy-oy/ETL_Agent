"""项目连接登记、连接测试和 Profile 查询 API。"""

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from etl_agent.api.auth_dependencies import (
    CurrentUser,
    DbSession,
    require_project_membership,
    require_project_role,
)
from etl_agent.api.connection_models import (
    ConnectionCreate,
    ConnectionResponse,
    ConnectionUpdate,
    ProfileResponse,
)
from etl_agent.api.connection_test_models import ConnectionTestResponse, ProfileCreateRequest
from etl_agent.api.errors import ApiError
from etl_agent.infrastructure.connection_testing import run_connection_test
from etl_agent.infrastructure.models import Connection, MetadataProfile, Project, ProjectRole
from etl_agent.infrastructure.profiling import MetadataProfileService, ProfileError
from etl_agent.infrastructure.secrets import SecretProviderError

router = APIRouter(prefix="/api/v1", tags=["connections"])


async def _get_connection_for_user(
    connection_id: UUID,
    current_user: CurrentUser,
    session: DbSession,
) -> Connection:
    """读取连接并校验当前用户属于其项目，避免跨项目探查。"""
    connection = await session.get(Connection, connection_id)
    if connection is None:
        raise ApiError("CONNECTION_NOT_FOUND", "连接不存在", status_code=404)
    await require_project_membership(connection.project_id, current_user, session)
    return connection


@router.post(
    "/connections",
    response_model=ConnectionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_connection(
    payload: ConnectionCreate,
    current_user: CurrentUser,
    session: DbSession,
) -> Connection:
    """登记项目连接的非敏感信息，并仅保存 SecretRef 引用。"""
    project = await session.get(Project, payload.project_id)
    if project is None:
        raise ApiError("PROJECT_NOT_FOUND", "项目不存在", status_code=404)
    await require_project_role(
        payload.project_id,
        current_user,
        session,
        {ProjectRole.MAKER, ProjectRole.OPERATOR},
    )
    connection = Connection(**payload.model_dump())
    session.add(connection)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ApiError("CONNECTION_CODE_EXISTS", "项目内连接编码已存在", status_code=409) from exc
    await session.refresh(connection)
    return connection


@router.put("/connections/{connection_id}", response_model=ConnectionResponse)
async def update_connection(
    connection_id: UUID,
    payload: ConnectionUpdate,
    current_user: CurrentUser,
    session: DbSession,
) -> Connection:
    """更新连接地址、类型或 SecretRef，确保仍处于项目职责和唯一编码约束内。"""
    connection = await _get_connection_for_user(connection_id, current_user, session)
    await require_project_role(
        connection.project_id,
        current_user,
        session,
        {ProjectRole.MAKER, ProjectRole.OPERATOR},
    )
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(connection, field, value)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ApiError("CONNECTION_CODE_EXISTS", "项目内连接编码已存在", status_code=409) from exc
    await session.refresh(connection)
    return connection


@router.get("/projects/{project_id}/connections", response_model=list[ConnectionResponse])
async def list_connections(
    project_id: UUID,
    current_user: CurrentUser,
    session: DbSession,
) -> list[Connection]:
    """按项目边界查询连接，返回内容不包含 Secret 明文。"""
    await require_project_membership(project_id, current_user, session)
    result = await session.scalars(
        select(Connection)
        .where(Connection.project_id == project_id)
        .order_by(Connection.created_at)
    )
    return list(result.all())


@router.get("/connections/{connection_id}/profiles/latest", response_model=ProfileResponse)
async def get_latest_profile(
    connection_id: UUID,
    current_user: CurrentUser,
    session: DbSession,
) -> MetadataProfile:
    """返回连接最近一次 Profile 快照，不触发新的外部探查。"""
    connection = await _get_connection_for_user(connection_id, current_user, session)
    result = await session.scalars(
        select(MetadataProfile)
        .where(MetadataProfile.connection_id == connection.id)
        .order_by(MetadataProfile.created_at.desc())
        .limit(1)
    )
    profile = result.first()
    if profile is None:
        raise ApiError("PROFILE_NOT_FOUND", "该连接暂无元数据 Profile", status_code=404)
    return profile


@router.post("/connections/{connection_id}/tests", response_model=ConnectionTestResponse)
async def connection_test_endpoint(
    connection_id: UUID,
    request: Request,
    current_user: CurrentUser,
    session: DbSession,
) -> ConnectionTestResponse:
    """执行一次受限连接测试，只返回稳定状态和耗时，不返回底层异常。"""
    connection = await _get_connection_for_user(connection_id, current_user, session)
    result = await run_connection_test(
        connection,
        request.app.state.secret_provider,
        request.app.state.settings,
    )
    return ConnectionTestResponse(
        status=result.status,
        detail=result.detail,
        latency_ms=result.latency_ms,
        checked_at=datetime.now(UTC),
    )


@router.post(
    "/connections/{connection_id}/profiles",
    response_model=ProfileResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_profile(
    connection_id: UUID,
    payload: ProfileCreateRequest,
    request: Request,
    current_user: CurrentUser,
    session: DbSession,
) -> MetadataProfile:
    """执行只读 Profile 探查，脱敏后以不可变指纹快照写入 PostgreSQL。"""
    connection = await _get_connection_for_user(connection_id, current_user, session)
    try:
        result = await MetadataProfileService(
            request.app.state.secret_provider,
            request.app.state.settings,
        ).generate(connection, payload.table_names, payload.sample_rows)
    except SecretProviderError as exc:
        raise ApiError("SECRET_UNAVAILABLE", "连接凭据暂时不可用", status_code=503) from exc
    except ProfileError as exc:
        raise ApiError("PROFILE_FAILED", "只读 Profile 探查失败", status_code=422) from exc
    existing = await session.scalar(
        select(MetadataProfile).where(
            MetadataProfile.connection_id == connection.id,
            MetadataProfile.fingerprint == result.fingerprint,
        )
    )
    if existing is not None:
        return existing
    profile = MetadataProfile(
        connection_id=connection.id,
        fingerprint=result.fingerprint,
        schema_snapshot=result.schema_snapshot,
        redacted_sample=result.redacted_sample,
        estimated_row_count=result.estimated_row_count,
    )
    session.add(profile)
    await session.commit()
    await session.refresh(profile)
    return profile
