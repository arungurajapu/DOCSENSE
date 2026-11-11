"""Health check response schema."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class HealthResponse(BaseModel):
    """Service readiness and dependency status summary."""

    status: Literal["ok", "degraded"]
    version: str
    database: Literal["ok", "error"]
    vector_store: Literal["ok", "error"]
    embedding_model_loaded: bool
    timestamp: datetime
