"""Document API request/response schemas."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.document import DocumentStatus


class DocumentResponse(BaseModel):
    """Serialized document metadata and ingestion status."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    filename: str
    content_type: str
    file_size_bytes: int = Field(..., ge=0)
    sha256_hash: str
    status: DocumentStatus
    error_message: str | None = None
    page_count: int | None = Field(default=None, ge=0)
    chunk_count: int = Field(default=0, ge=0)
    created_at: datetime
    updated_at: datetime


class DocumentListResponse(BaseModel):
    """Paginated list of documents."""

    items: list[DocumentResponse]
    total: int = Field(..., ge=0)
    limit: int = Field(..., ge=1, le=100)
    offset: int = Field(..., ge=0)
