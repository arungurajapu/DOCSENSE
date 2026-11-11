"""Health check endpoint for DocSense API v1."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter
from fastapi import status as http_status

from app.api.deps import (
    DocumentRepoDep,
    EmbeddingServiceDep,
    IngestionServiceDep,
    SettingsDep,
    VectorStoreDep,
)
from app.core.exceptions import ServiceUnavailableError
from app.schemas import ErrorResponse, HealthResponse

logger = logging.getLogger(__name__)

router = APIRouter()


def _safe_health_check(name: str, check_fn: Callable[[], bool]) -> bool:
    """Execute a synchronous dependency check safely, treating raised errors as unhealthy."""
    try:
        return bool(check_fn())
    except Exception as exc:
        logger.warning("%s health check raised an error: %s", name, exc)
        return False


@router.get(
    "/health",
    tags=["health"],
    operation_id="health_check",
    summary="Service health check",
    status_code=http_status.HTTP_200_OK,
    response_model=HealthResponse,
    responses={
        503: {"model": ErrorResponse, "description": "Required dependency unavailable"},
    },
)
async def health_check(
    settings: SettingsDep,
    repo: DocumentRepoDep,
    vector_store: VectorStoreDep,
    embeddings: EmbeddingServiceDep,
    ingestion: IngestionServiceDep,
) -> HealthResponse:
    """Verify database and vector store health and report embedding model readiness."""
    db_ok = await ingestion.run_in_thread(_safe_health_check, "Database", repo.health_check)
    vector_ok = await ingestion.run_in_thread(
        _safe_health_check,
        "Vector store",
        vector_store.health_check,
    )

    if not db_ok or not vector_ok:
        failing: list[str] = []
        if not db_ok:
            failing.append("database")
        if not vector_ok:
            failing.append("vector store")
        raise ServiceUnavailableError(
            f"Required dependency check failed ({', '.join(failing)})."
        )

    model_loaded = bool(embeddings.is_loaded())
    overall_status: Literal["ok", "degraded"] = "ok" if model_loaded else "degraded"
    return HealthResponse(
        status=overall_status,
        version=settings.APP_VERSION,
        database="ok",
        vector_store="ok",
        embedding_model_loaded=model_loaded,
        timestamp=datetime.now(UTC),
    )


__all__ = ["router"]
