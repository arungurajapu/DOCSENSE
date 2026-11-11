"""Deterministic OpenAPI 3.1 contract exporter for the DocSense v1 API."""

import json
import sys
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, FastAPI, File, Query, Response, UploadFile
from fastapi import Path as ApiPath
from fastapi import status as http_status

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent
DEFAULT_OUTPUT_PATH = REPO_ROOT / "contracts" / "openapi.json"

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.schemas import (  # noqa: E402
    DocumentListResponse,
    DocumentResponse,
    DocumentStatus,
    ErrorResponse,
    HealthResponse,
)

_ERROR_VALIDATION_RESPONSE: dict[str, Any] = {
    "model": ErrorResponse,
    "description": "Validation or unprocessable entity error",
}
_ERROR_NOT_FOUND_RESPONSE: dict[str, Any] = {
    "model": ErrorResponse,
    "description": "Document not found",
}
_ERROR_CONFLICT_RESPONSE: dict[str, Any] = {
    "model": ErrorResponse,
    "description": "Document state conflict",
}


def build_contract_app() -> FastAPI:
    """Build a contract-only FastAPI app defining all frozen /api/v1 endpoints."""
    app = FastAPI(
        title="DocSense API",
        version="0.1.0",
        description="DocSense Document Ingestion API contract.",
    )
    router = APIRouter(prefix="/api/v1")

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
    async def health_check() -> HealthResponse:
        raise NotImplementedError("Contract-only stub")

    @router.post(
        "/documents",
        tags=["documents"],
        operation_id="upload_document",
        summary="Upload a PDF document for ingestion",
        status_code=http_status.HTTP_202_ACCEPTED,
        response_model=DocumentResponse,
        responses={
            400: {"model": ErrorResponse, "description": "Malformed upload request"},
            413: {"model": ErrorResponse, "description": "Uploaded file exceeds max size limit"},
            415: {"model": ErrorResponse, "description": "Unsupported or corrupt PDF media"},
            422: _ERROR_VALIDATION_RESPONSE,
        },
    )
    async def upload_document(
        file: Annotated[UploadFile, File(description="PDF file to upload and ingest")],
    ) -> DocumentResponse:
        raise NotImplementedError("Contract-only stub")

    @router.get(
        "/documents",
        tags=["documents"],
        operation_id="list_documents",
        summary="List documents with optional status filtering and pagination",
        status_code=http_status.HTTP_200_OK,
        response_model=DocumentListResponse,
        responses={
            422: _ERROR_VALIDATION_RESPONSE,
        },
    )
    async def list_documents(
        status: Annotated[
            DocumentStatus | None,
            Query(description="Filter by lifecycle status"),
        ] = None,
        limit: Annotated[
            int,
            Query(ge=1, le=100, description="Maximum items to return"),
        ] = 20,
        offset: Annotated[
            int,
            Query(ge=0, description="Pagination offset"),
        ] = 0,
    ) -> DocumentListResponse:
        raise NotImplementedError("Contract-only stub")

    @router.get(
        "/documents/{document_id}",
        tags=["documents"],
        operation_id="get_document",
        summary="Get a document by ID",
        status_code=http_status.HTTP_200_OK,
        response_model=DocumentResponse,
        responses={
            404: _ERROR_NOT_FOUND_RESPONSE,
            422: _ERROR_VALIDATION_RESPONSE,
        },
    )
    async def get_document(
        document_id: Annotated[
            UUID,
            ApiPath(description="Document UUID"),
        ],
    ) -> DocumentResponse:
        raise NotImplementedError("Contract-only stub")

    @router.post(
        "/documents/{document_id}/reprocess",
        tags=["documents"],
        operation_id="reprocess_document",
        summary="Reprocess a failed document",
        status_code=http_status.HTTP_202_ACCEPTED,
        response_model=DocumentResponse,
        responses={
            404: _ERROR_NOT_FOUND_RESPONSE,
            409: _ERROR_CONFLICT_RESPONSE,
            422: _ERROR_VALIDATION_RESPONSE,
        },
    )
    async def reprocess_document(
        document_id: Annotated[
            UUID,
            ApiPath(description="Document UUID"),
        ],
    ) -> DocumentResponse:
        raise NotImplementedError("Contract-only stub")

    @router.delete(
        "/documents/{document_id}",
        tags=["documents"],
        operation_id="delete_document",
        summary="Delete a document and its indexed chunks",
        status_code=http_status.HTTP_204_NO_CONTENT,
        response_model=None,
        response_class=Response,
        responses={
            404: _ERROR_NOT_FOUND_RESPONSE,
            409: _ERROR_CONFLICT_RESPONSE,
            422: _ERROR_VALIDATION_RESPONSE,
        },
    )
    async def delete_document(
        document_id: Annotated[
            UUID,
            ApiPath(description="Document UUID"),
        ],
    ) -> Response:
        raise NotImplementedError("Contract-only stub")

    app.include_router(router)
    return app


def export_openapi(output_path: Path | None = None) -> dict[str, Any]:
    """Generate deterministic OpenAPI schema JSON and write it to ``output_path``."""
    target_path = output_path or DEFAULT_OUTPUT_PATH
    app = build_contract_app()
    schema = app.openapi()

    # Ensure FastAPI's default HTTPValidationError / ValidationError schemas are never emitted.
    components = schema.get("components", {})
    schemas = components.get("schemas", {})
    schemas.pop("HTTPValidationError", None)
    schemas.pop("ValidationError", None)

    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(
        json.dumps(schema, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return schema


if __name__ == "__main__":
    exported = export_openapi()
    paths = exported.get("paths", {})
    operation_count = sum(len(methods) for methods in paths.values())
    print(
        f"Exported OpenAPI contract ({operation_count} operations across {len(paths)} paths) "
        f"to {DEFAULT_OUTPUT_PATH}"
    )
