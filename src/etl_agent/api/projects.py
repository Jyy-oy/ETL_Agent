"""项目、成员和职责槽管理 API。"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from etl_agent.api.auth_dependencies import (
    CurrentUser,
    DbSession,
    require_project_membership,
    require_project_role,
)
from etl_agent.api.auth_models import (
    MemberCreate,
    MemberResponse,
    ProjectCreate,
    ProjectResponse,
    UserResponse,
)
from etl_agent.api.errors import ApiError
from etl_agent.infrastructure.models import (
    Project,
    ProjectMembership,
    ProjectRole,
    ProjectRoleGrant,
    User,
)

router = APIRouter(prefix="/api/v1", tags=["projects"])
Membership = Annotated[ProjectMembership, Depends(require_project_membership)]


def validate_role_assignment(existing_roles: set[ProjectRole], requested_role: ProjectRole) -> None:
    """校验新增职责槽不会重复占用或跨越 Maker/Checker 边界。"""
    if requested_role in existing_roles:
        raise ApiError("ROLE_EXISTS", "用户已拥有该职责槽", status_code=409)
    checker_roles = {ProjectRole.CHECKER_1, ProjectRole.CHECKER_2}
    execution_roles = {ProjectRole.MAKER, ProjectRole.OPERATOR}
    if requested_role in checker_roles and existing_roles & execution_roles:
        raise ApiError("ROLE_CONFLICT", "Maker/Operator 不得兼任 Checker", status_code=409)
    if requested_role in execution_roles and existing_roles & checker_roles:
        raise ApiError("ROLE_CONFLICT", "Checker 不得兼任 Maker/Operator", status_code=409)


@router.post("/projects", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
async def create_project(
    payload: ProjectCreate,
    current_user: CurrentUser,
    session: DbSession,
) -> Project:
    """创建项目并为创建者建立 Maker 与 Operator 初始职责槽。"""
    project = Project(code=payload.code, name=payload.name)
    session.add(project)
    try:
        await session.flush()
        session.add(ProjectMembership(project_id=project.id, user_id=current_user.id))
        session.add_all(
            [
                ProjectRoleGrant(
                    project_id=project.id, user_id=current_user.id, role=ProjectRole.MAKER
                ),
                ProjectRoleGrant(
                    project_id=project.id, user_id=current_user.id, role=ProjectRole.OPERATOR
                ),
            ]
        )
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ApiError("PROJECT_CODE_EXISTS", "项目编码已存在", status_code=409) from exc
    await session.refresh(project)
    return project


@router.get("/projects", response_model=list[ProjectResponse])
async def list_projects(current_user: CurrentUser, session: DbSession) -> list[Project]:
    """只查询当前用户作为有效成员加入的项目，避免跨项目泄露。"""
    result = await session.scalars(
        select(Project)
        .join(ProjectMembership, ProjectMembership.project_id == Project.id)
        .where(
            ProjectMembership.user_id == current_user.id,
            ProjectMembership.status == "active",
        )
        .order_by(Project.created_at)
    )
    return list(result.all())


@router.get("/projects/{project_id}/members", response_model=list[MemberResponse])
async def list_project_members(
    project_id: UUID,
    membership: Membership,
    session: DbSession,
) -> list[MemberResponse]:
    """查询项目成员及职责槽，前提是当前用户属于该项目。"""
    del membership
    rows = await session.execute(
        select(ProjectMembership, User, ProjectRoleGrant)
        .join(User, User.id == ProjectMembership.user_id)
        .join(
            ProjectRoleGrant,
            (ProjectRoleGrant.project_id == ProjectMembership.project_id)
            & (ProjectRoleGrant.user_id == ProjectMembership.user_id),
        )
        .where(ProjectMembership.project_id == project_id)
        .order_by(User.username, ProjectRoleGrant.role)
    )
    return [
        MemberResponse(
            user=UserResponse.model_validate(user),
            role=ProjectRole(role.role),
            membership_status=membership.status,
        )
        for membership, user, role in rows.all()
    ]


@router.post("/projects/{project_id}/members", response_model=MemberResponse, status_code=201)
async def add_project_member(
    project_id: UUID,
    payload: MemberCreate,
    current_user: CurrentUser,
    session: DbSession,
) -> MemberResponse:
    """由项目 Operator 添加成员并分配未占用且不冲突的职责槽。"""
    await require_project_role(project_id, current_user, session, {ProjectRole.OPERATOR})
    user = await session.get(User, payload.user_id)
    if user is None or not user.is_active:
        raise ApiError("USER_NOT_FOUND", "待加入的用户不存在或已停用", status_code=404)
    existing_grants = await session.scalars(
        select(ProjectRoleGrant.role).where(
            ProjectRoleGrant.project_id == project_id,
            ProjectRoleGrant.user_id == payload.user_id,
        )
    )
    roles = {ProjectRole(value) for value in existing_grants.all()}
    validate_role_assignment(roles, payload.role)
    membership = await session.scalar(
        select(ProjectMembership).where(
            ProjectMembership.project_id == project_id,
            ProjectMembership.user_id == payload.user_id,
        )
    )
    if membership is None:
        membership = ProjectMembership(project_id=project_id, user_id=payload.user_id)
        session.add(membership)
    membership.status = "active"
    session.add(ProjectRoleGrant(project_id=project_id, user_id=payload.user_id, role=payload.role))
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ApiError("ROLE_SLOT_EXISTS", "该职责槽已被其他用户占用", status_code=409) from exc
    return MemberResponse(
        user=UserResponse.model_validate(user),
        role=payload.role,
        membership_status=membership.status,
    )
