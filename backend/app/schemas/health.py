"""Health check response schema."""

from typing import Literal

from pydantic import BaseModel

from app.schemas.types import UTCDatetime


class HealthResponse(BaseModel):
    """Service readiness and dependency status summary."""

    status: Literal["ok", "degraded"]
    version: str
    database: Literal["ok", "error"]
    vector_store: Literal["ok", "error"]
    embedding_model_loaded: bool
    timestamp: UTCDatetime
