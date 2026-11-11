"""Unified API error response schemas."""

from pydantic import BaseModel, Field


class ErrorDetail(BaseModel):
    """Machine-readable error code and human-readable message."""

    code: str = Field(..., examples=["DOCUMENT_NOT_FOUND"])
    message: str = Field(..., examples=["Document not found."])


class ErrorResponse(BaseModel):
    """Unified error envelope returned for all 4xx/5xx responses including 422."""

    error: ErrorDetail
