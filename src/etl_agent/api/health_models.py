"""Public health response models."""

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field


class DependencyHealth(BaseModel):
    status: Literal["ok", "degraded", "down", "optional"]
    detail: str
    latency_ms: int | None = None


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded", "down"]
    app: str
    environment: str
    checked_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    request_id: str
    dependencies: dict[str, DependencyHealth]
