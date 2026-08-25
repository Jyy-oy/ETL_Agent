"""项目文件资产上传、查询和脱敏 Profile API。"""

import asyncio
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, File, Form, Request, UploadFile, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from etl_agent.api.auth_dependencies import CurrentUser, DbSession, require_project_role
from etl_agent.api.errors import ApiError
from etl_agent.api.file_asset_models import FileAssetResponse
from etl_agent.infrastructure.file_profiling import FileProfileError, inspect_upload
from etl_agent.infrastructure.models import FileAsset, ProjectRole
from etl_agent.infrastructure.object_store import ObjectStoreError

router = APIRouter(prefix="/api/v1", tags=["file-assets"])
ProjectIdForm = Annotated[UUID, Form()]
Upload = Annotated[UploadFile, File()]


@router.post("/file-assets", response_model=FileAssetResponse, status_code=status.HTTP_201_CREATED)
async def upload_file_asset(
    project_id: ProjectIdForm,
    file: Upload,
    request: Request,
    current_user: CurrentUser,
    session: DbSession,
) -> FileAsset:
    """校验并上传文件，保存对象引用、哈希和脱敏文件 Profile。"""
    await require_project_role(
        project_id,
        current_user,
        session,
        {ProjectRole.MAKER, ProjectRole.OPERATOR},
    )
    try:
        inspection = await asyncio.to_thread(
            inspect_upload,
            file.file,
            file.filename or "upload",
            file.content_type,
            request.app.state.settings.max_upload_size_bytes,
        )
    except FileProfileError as exc:
        raise ApiError("FILE_INVALID", str(exc), status_code=422) from exc
    asset_id = uuid4()
    suffix = "." + inspection.file_format
    object_key = f"{project_id}/file-assets/{asset_id}{suffix}"
    uploaded = False
    persisted = False
    try:
        inspection.fileobj.seek(0)
        await request.app.state.object_store.put(
            inspection.fileobj,
            object_key,
            inspection.size_bytes,
            inspection.content_type,
        )
        uploaded = True
        asset = FileAsset(
            id=asset_id,
            project_id=project_id,
            uploaded_by=current_user.id,
            bucket=request.app.state.settings.minio_bucket,
            object_key=object_key,
            original_filename=inspection.original_filename,
            content_type=inspection.content_type,
            file_format=inspection.file_format,
            size_bytes=inspection.size_bytes,
            sha256=inspection.sha256,
            schema_snapshot=inspection.schema_snapshot,
        )
        session.add(asset)
        await session.commit()
        await session.refresh(asset)
        persisted = True
        return asset
    except ObjectStoreError as exc:
        raise ApiError("FILE_STORAGE_FAILED", "文件对象存储失败", status_code=503) from exc
    except IntegrityError as exc:
        await session.rollback()
        raise ApiError("FILE_ASSET_CONFLICT", "文件资产登记冲突", status_code=409) from exc
    finally:
        inspection.fileobj.close()
        if uploaded and not persisted:
            try:
                await request.app.state.object_store.delete(object_key)
            except ObjectStoreError:
                pass


@router.get("/projects/{project_id}/file-assets", response_model=list[FileAssetResponse])
async def list_file_assets(
    project_id: UUID,
    current_user: CurrentUser,
    session: DbSession,
) -> list[FileAsset]:
    """按项目成员边界查询文件资产元数据和脱敏 Profile。"""
    await require_project_role(
        project_id,
        current_user,
        session,
        {
            ProjectRole.MAKER,
            ProjectRole.CHECKER_1,
            ProjectRole.CHECKER_2,
            ProjectRole.OPERATOR,
            ProjectRole.AUDITOR,
        },
    )
    result = await session.scalars(
        select(FileAsset)
        .where(FileAsset.project_id == project_id)
        .order_by(FileAsset.created_at.desc())
    )
    return list(result.all())


@router.get("/file-assets/{asset_id}", response_model=FileAssetResponse)
async def get_file_asset(
    asset_id: UUID,
    current_user: CurrentUser,
    session: DbSession,
) -> FileAsset:
    """查询单个文件资产并校验当前用户对项目的访问权限。"""
    asset = await session.get(FileAsset, asset_id)
    if asset is None:
        raise ApiError("FILE_ASSET_NOT_FOUND", "文件资产不存在", status_code=404)
    await require_project_role(
        asset.project_id,
        current_user,
        session,
        {
            ProjectRole.MAKER,
            ProjectRole.CHECKER_1,
            ProjectRole.CHECKER_2,
            ProjectRole.OPERATOR,
            ProjectRole.AUDITOR,
        },
    )
    return asset
