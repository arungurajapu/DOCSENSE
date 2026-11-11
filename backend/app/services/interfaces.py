"""Internal service/repository protocols and data transfer objects."""

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from fastapi import UploadFile


@dataclass(frozen=True)
class SavedFileResult:
    """Metadata returned after validating and persisting an uploaded PDF."""

    storage_path: str
    sha256_hash: str
    file_size_bytes: int
    content_type: str
    page_count: int


@dataclass(frozen=True)
class ExtractedPage:
    """Single extracted PDF page with 1-based page number."""

    page_number: int
    text: str


@dataclass(frozen=True)
class TextChunk:
    """Token-bounded text chunk with cross-page provenance."""

    chunk_index: int
    text: str
    page_number: int
    page_end: int
    page_numbers_csv: str
    token_count: int
    char_start: int
    char_end: int
    content_preview: str


@runtime_checkable
class StorageService(Protocol):
    """Contract for streaming upload validation and safe local PDF storage."""

    async def save_validated_pdf(
        self,
        upload_file: UploadFile,
        document_id: str,
    ) -> SavedFileResult: ...

    def delete_file(self, storage_path: str) -> None: ...

    def resolve_path(self, storage_path: str) -> Path: ...


@runtime_checkable
class PDFExtractor(Protocol):
    """Contract isolating PDF parsing libraries (e.g., PyMuPDF) behind a domain boundary."""

    def extract(self, file_path: str | Path) -> list[ExtractedPage]: ...


@runtime_checkable
class TokenChunker(Protocol):
    """Contract for model-aligned overlapping token chunking."""

    def chunk(self, pages: Sequence[ExtractedPage], document_id: str) -> list[TextChunk]: ...


@runtime_checkable
class EmbeddingService(Protocol):
    """Contract for dense vector generation using the configured embedding model."""

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]: ...

    async def embed_texts_async(self, texts: Sequence[str]) -> list[list[float]]: ...

    def is_loaded(self) -> bool: ...


@runtime_checkable
class VectorStore(Protocol):
    """Contract for persistent vector indexing and document-scoped cleanup."""

    def upsert_chunks(
        self,
        document_id: str,
        filename: str,
        chunks: Sequence[TextChunk],
        embeddings: Sequence[Sequence[float]],
    ) -> None: ...

    def delete_document(self, document_id: str) -> None: ...

    def count_by_document(self, document_id: str) -> int: ...

    def health_check(self) -> bool: ...
