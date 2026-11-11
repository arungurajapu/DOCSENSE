"""SQLAlchemy 2.x repository for document metadata and chunk provenance."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import delete, func, insert, select, text, update
from sqlalchemy.orm import Session, sessionmaker

from app.core.exceptions import DocumentConflictError, DocumentNotFoundError
from app.models.document import Document, DocumentChunk, DocumentStatus
from app.services.interfaces import TextChunk


class DocumentRepository:
    """Short-lived-session repository for :class:`Document` and :class:`DocumentChunk`.

    Each method opens, commits or rolls back, and closes its own SQLAlchemy
    ``Session`` so SQLite connections and locks are never held across CPU,
    embedding, vector-store, or async lock waits.
    """

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def create_document(
        self,
        *,
        document_id: str,
        filename: str,
        content_type: str,
        file_size_bytes: int,
        sha256_hash: str,
        storage_path: str,
        page_count: int | None = None,
    ) -> Document:
        """Persist a new document row in ``pending`` status."""
        now = datetime.now(UTC)
        doc = Document(
            id=document_id,
            filename=filename,
            content_type=content_type,
            file_size_bytes=file_size_bytes,
            sha256_hash=sha256_hash,
            storage_path=storage_path,
            status=DocumentStatus.PENDING.value,
            error_message=None,
            page_count=page_count,
            chunk_count=0,
            created_at=now,
            updated_at=now,
        )
        with self._session_factory() as session:
            session.add(doc)
            session.commit()
            session.refresh(doc)
            session.expunge(doc)
            return doc

    def get_document(self, document_id: str) -> Document | None:
        """Fetch a single document by ID, or ``None`` if not found."""
        with self._session_factory() as session:
            doc = session.get(Document, document_id)
            if doc is not None:
                session.expunge(doc)
            return doc

    def list_documents(
        self,
        status: DocumentStatus | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[Document], int]:
        """Return ``(items, total)`` ordered newest-first with deterministic tie-breaking."""
        with self._session_factory() as session:
            count_stmt = select(func.count()).select_from(Document)
            items_stmt = select(Document)
            if status is not None:
                count_stmt = count_stmt.where(Document.status == status.value)
                items_stmt = items_stmt.where(Document.status == status.value)

            total = int(session.execute(count_stmt).scalar_one())
            items = list(
                session.execute(
                    items_stmt.order_by(Document.created_at.desc(), Document.id.desc())
                    .limit(limit)
                    .offset(offset)
                )
                .scalars()
                .all()
            )
            for doc in items:
                session.expunge(doc)
            return items, total

    def claim_for_processing(self, document_id: str) -> bool:
        """Atomically transition ``pending -> processing``; returns ``True`` if claimed."""
        stmt = (
            update(Document)
            .where(
                Document.id == document_id,
                Document.status == DocumentStatus.PENDING.value,
            )
            .values(
                status=DocumentStatus.PROCESSING.value,
                error_message=None,
                chunk_count=0,
                updated_at=datetime.now(UTC),
            )
        )
        with self._session_factory() as session:
            result = session.execute(stmt)
            session.commit()
            return result.rowcount == 1

    def complete_ingestion(
        self,
        document_id: str,
        page_count: int,
        chunks: Sequence[TextChunk],
    ) -> Document | None:
        """Atomically persist chunk provenance and mark ``processing -> ready``.

        If the document is missing or no longer in ``processing`` status when the
        conditional update runs, rolls back the chunk inserts and returns ``None``.
        """
        now = datetime.now(UTC)
        chunk_list = list(chunks)

        with self._session_factory() as session:
            session.execute(
                delete(DocumentChunk).where(DocumentChunk.document_id == document_id)
            )
            exists = session.execute(
                select(Document.id).where(Document.id == document_id)
            ).scalar_one_or_none()
            if exists is None:
                session.rollback()
                return None

            if chunk_list:
                chunk_mappings = [
                    {
                        "id": str(uuid.uuid4()),
                        "document_id": document_id,
                        "chunk_index": int(chunk.chunk_index),
                        "page_number": int(chunk.page_number),
                        "page_end": int(chunk.page_end),
                        "page_numbers_csv": str(chunk.page_numbers_csv),
                        "token_count": int(chunk.token_count),
                        "char_start": int(chunk.char_start),
                        "char_end": int(chunk.char_end),
                        "content_preview": str(chunk.content_preview),
                        "chroma_id": f"{document_id}:{int(chunk.chunk_index)}",
                        "created_at": now,
                    }
                    for chunk in chunk_list
                ]
                session.execute(insert(DocumentChunk), chunk_mappings)

            result = session.execute(
                update(Document)
                .where(
                    Document.id == document_id,
                    Document.status == DocumentStatus.PROCESSING.value,
                )
                .values(
                    status=DocumentStatus.READY.value,
                    page_count=page_count,
                    chunk_count=len(chunk_list),
                    error_message=None,
                    updated_at=now,
                )
            )
            if result.rowcount != 1:
                session.rollback()
                return None

            session.commit()
            doc = session.get(Document, document_id)
            if doc is not None:
                session.refresh(doc)
                session.expunge(doc)
            return doc

    def mark_failed(self, document_id: str, error_message: str) -> bool:
        """Clear partial chunks and transition ``processing -> failed`` atomically."""
        with self._session_factory() as session:
            session.execute(
                delete(DocumentChunk).where(DocumentChunk.document_id == document_id)
            )
            result = session.execute(
                update(Document)
                .where(
                    Document.id == document_id,
                    Document.status == DocumentStatus.PROCESSING.value,
                )
                .values(
                    status=DocumentStatus.FAILED.value,
                    chunk_count=0,
                    error_message=error_message,
                    updated_at=datetime.now(UTC),
                )
            )
            if result.rowcount != 1:
                session.rollback()
                return False

            session.commit()
            return True

    def reset_failed_for_reprocess(self, document_id: str) -> Document:
        """Validate ``failed`` state, purge chunks, and reset ``failed -> pending``."""
        with self._session_factory() as session:
            doc = session.get(Document, document_id)
            if doc is None:
                raise DocumentNotFoundError(f"Document '{document_id}' not found.")
            if doc.status != DocumentStatus.FAILED.value:
                raise DocumentConflictError(
                    f"Document '{document_id}' cannot be reprocessed in status '{doc.status}'."
                )

            session.execute(
                delete(DocumentChunk).where(DocumentChunk.document_id == document_id)
            )
            result = session.execute(
                update(Document)
                .where(
                    Document.id == document_id,
                    Document.status == DocumentStatus.FAILED.value,
                )
                .values(
                    status=DocumentStatus.PENDING.value,
                    error_message=None,
                    chunk_count=0,
                    updated_at=datetime.now(UTC),
                )
            )
            if result.rowcount != 1:
                session.rollback()
                raise DocumentConflictError(
                    f"Document '{document_id}' state changed concurrently; cannot reprocess."
                )

            session.commit()
            session.refresh(doc)
            session.expunge(doc)
            return doc

    def mark_deleting(self, document_id: str) -> Document:
        """Transition a document row to ``deleting`` unless missing or already deleting."""
        with self._session_factory() as session:
            doc = session.get(Document, document_id)
            if doc is None:
                raise DocumentNotFoundError(f"Document '{document_id}' not found.")
            if doc.status == DocumentStatus.DELETING.value:
                raise DocumentConflictError(
                    f"Document '{document_id}' is already being deleted."
                )

            result = session.execute(
                update(Document)
                .where(
                    Document.id == document_id,
                    Document.status != DocumentStatus.DELETING.value,
                )
                .values(
                    status=DocumentStatus.DELETING.value,
                    updated_at=datetime.now(UTC),
                )
            )
            if result.rowcount != 1:
                session.rollback()
                raise DocumentConflictError(
                    f"Document '{document_id}' is already being deleted."
                )

            session.commit()
            session.refresh(doc)
            session.expunge(doc)
            return doc

    def delete_document_row(self, document_id: str) -> bool:
        """Delete a document row (cascading to ``document_chunks`` via FK pragma)."""
        with self._session_factory() as session:
            result = session.execute(
                delete(Document).where(Document.id == document_id)
            )
            session.commit()
            return result.rowcount == 1

    def list_incomplete_documents(self) -> dict[DocumentStatus, list[Document]]:
        """Return documents in ``pending``, ``processing``, or ``deleting`` states."""
        grouped: dict[DocumentStatus, list[Document]] = {
            DocumentStatus.PENDING: [],
            DocumentStatus.PROCESSING: [],
            DocumentStatus.DELETING: [],
        }
        target_statuses = [status.value for status in grouped]

        with self._session_factory() as session:
            rows = list(
                session.execute(
                    select(Document)
                    .where(Document.status.in_(target_statuses))
                    .order_by(Document.created_at.asc(), Document.id.asc())
                )
                .scalars()
                .all()
            )
            for doc in rows:
                session.expunge(doc)
                grouped[DocumentStatus(doc.status)].append(doc)
        return grouped

    def health_check(self) -> bool:
        """Verify SQLite connectivity via ``SELECT 1``."""
        try:
            with self._session_factory() as session:
                return session.execute(text("SELECT 1")).scalar() == 1
        except Exception:
            return False


__all__ = ["DocumentRepository"]
