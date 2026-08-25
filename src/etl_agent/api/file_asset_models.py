"""文件资产 API 契约模型。"""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class FileAssetResponse(BaseModel):
    """文件对象登记和脱敏 Profile 响应。"""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    uploaded_by: UUID
    bucket: str
    object_key: str
    original_filename: str
    content_type: str
    file_format: str
    size_bytes: int
    sha256: str
    schema_snapshot: dict[str, Any]
    status: str
    created_at: datetime
    updated_at: datetime
