"""Document ingestion lifecycle endpoints for DocSense API v1."""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, File, Query, Response, UploadFile
from fastapi import Path as ApiPath
from fastapi import status as http_status

from app.api.deps import IngestionServiceDep
from app.schemas import DocumentListResponse, DocumentResponse, DocumentStatus, ErrorResponse

router = APIRouter()

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
    ingestion: IngestionServiceDep,
) -> DocumentResponse:
    """Validate, store, and enqueue a PDF document for background ingestion."""
    try:
        return await ingestion.upload_document(file)
    finally:
        await file.close()


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
    ingestion: IngestionServiceDep,
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
    """List documents newest-first with optional status filter and pagination."""
    return await ingestion.list_documents(status=status, limit=limit, offset=offset)


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
    ingestion: IngestionServiceDep,
) -> DocumentResponse:
    """Return metadata and current ingestion status for a single document."""
    return await ingestion.get_document(str(document_id))


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
    ingestion: IngestionServiceDep,
) -> DocumentResponse:
    """Reset a failed document to pending and schedule fresh background ingestion."""
    return await ingestion.reprocess_document(str(document_id))


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
    ingestion: IngestionServiceDep,
) -> Response:
    """Delete a document, its indexed Chroma vectors, stored PDF file, and chunk rows."""
    await ingestion.delete_document(str(document_id))
    return Response(status_code=http_status.HTTP_204_NO_CONTENT)


__all__ = ["router"]
