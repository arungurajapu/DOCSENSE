"""Document API request/response schemas."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.document import DocumentStatus
from app.schemas.types import UTCDatetime


class DocumentResponse(BaseModel):
    """Serialized document metadata and ingestion status."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    filename: str
    content_type: str
    file_size_bytes: int = Field(..., ge=0)
    sha256_hash: str
    status: DocumentStatus
    error_message: str | None = Field(...)
    page_count: int | None = Field(..., ge=0)
    chunk_count: int = Field(..., ge=0)
    created_at: UTCDatetime
    updated_at: UTCDatetime


class DocumentListResponse(BaseModel):
    """Paginated list of documents."""

    items: list[DocumentResponse]
    total: int = Field(..., ge=0)
    limit: int = Field(..., ge=1, le=100)
    offset: int = Field(..., ge=0)
