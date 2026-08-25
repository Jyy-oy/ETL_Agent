"""连接测试和 Profile 请求的 API 模型。"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class ConnectionTestResponse(BaseModel):
    """连接测试的稳定脱敏响应。"""

    status: Literal["passed", "failed", "unsupported"]
    detail: str
    latency_ms: int
    checked_at: datetime


class ProfileCreateRequest(BaseModel):
    """只读 Profile 请求的资源范围和样本预算。"""

    table_names: list[str] = Field(default_factory=list, max_length=50)
    sample_rows: int = Field(default=5, ge=0, le=20)
