"""End-to-end FastAPI integration tests for ingestion, health, errors, and cascade delete."""

from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select, text
from tests.integration.conftest import (
    DeterministicEmbeddingService,
    make_encrypted_pdf_bytes,
    make_isolated_settings,
    make_scanned_pdf_bytes,
    make_text_pdf_bytes,
    make_zero_page_pdf_bytes,
    poll_until_terminal,
)

from app.core.exceptions import ExtractionError
from app.main import create_app
from app.models.document import Document, DocumentChunk, DocumentStatus
from app.schemas import DocumentListResponse, DocumentResponse, ErrorResponse, HealthResponse
from app.services.extractor import PDFExtractor
from app.services.interfaces import ExtractedPage


def _assert_error_response(
    resp: Any,
    expected_status: int,
    expected_code: str | None = None,
) -> ErrorResponse:
    assert resp.status_code == expected_status, (
        f"Expected HTTP {expected_status}, got {resp.status_code}: {resp.text}"
    )
    parsed = ErrorResponse.model_validate(resp.json())
    if expected_code is not None:
        assert parsed.error.code == expected_code
    return parsed


def test_real_end_to_end_upload_ingestion_list_and_delete(tmp_path: Path) -> None:
    """Upload -> 202 pending -> ready with real MiniLM embeddings -> SQLite/Chroma parity -> 204."""
    settings = make_isolated_settings(tmp_path)
    app = create_app(settings, eager_load_embeddings=True)

    pdf_bytes = make_text_pdf_bytes(
        [
            "DocSense architecture isolates PyMuPDF behind PDFExtractor. " * 25,
            "ChromaDB persists dense embeddings normalized for cosine search. " * 25,
        ]
    )

    with TestClient(app) as client:
        # 1. Health is ok once real embedding model loaded.
        health_resp = client.get("/api/v1/health")
        assert health_resp.status_code == 200
        health = HealthResponse.model_validate(health_resp.json())
        assert health.status == "ok"
        assert health.database == "ok"
        assert health.vector_store == "ok"
        assert health.embedding_model_loaded is True

        # 2. Upload returns 202 with initial page_count and chunk_count=0.
        upload_resp = client.post(
            "/api/v1/documents",
            files={"file": ("quarterly_report.pdf", pdf_bytes, "application/pdf")},
        )
        assert upload_resp.status_code == 202
        uploaded = DocumentResponse.model_validate(upload_resp.json())
        doc_id = str(uploaded.id)
        assert uploaded.filename == "quarterly_report.pdf"
        assert uploaded.status == DocumentStatus.PENDING
        assert uploaded.page_count == 2
        assert uploaded.chunk_count == 0
        assert uploaded.error_message is None

        # Verify PDF was persisted to UPLOAD_DIR.
        stored_pdf = Path(settings.UPLOAD_DIR) / f"{doc_id}.pdf"
        assert stored_pdf.is_file()

        # 3. Poll until ingestion completes to ready.
        ready_payload = poll_until_terminal(client, doc_id, timeout=30.0)
        ready_doc = DocumentResponse.model_validate(ready_payload)
        assert ready_doc.status == DocumentStatus.READY
        assert ready_doc.page_count == 2
        assert ready_doc.chunk_count >= 2
        assert ready_doc.error_message is None

        # 4. Verify SQLite document_chunks and ChromaDB vectors match 1-to-1 with "{id}:{idx}".
        expected_ids = [f"{doc_id}:{idx}" for idx in range(ready_doc.chunk_count)]
        with app.state.session_factory() as session:
            db_chunks = list(
                session.execute(
                    select(DocumentChunk)
                    .where(DocumentChunk.document_id == doc_id)
                    .order_by(DocumentChunk.chunk_index.asc())
                )
                .scalars()
                .all()
            )
            assert len(db_chunks) == ready_doc.chunk_count
            assert [c.chroma_id for c in db_chunks] == expected_ids
            assert all(1 <= c.page_number <= c.page_end <= 2 for c in db_chunks)
            assert all(c.token_count > 0 for c in db_chunks)

        vector_store = app.state.vector_store
        assert vector_store.count_by_document(doc_id) == ready_doc.chunk_count
        chroma_records = vector_store._collection.get(
            where={"document_id": doc_id},
            include=["metadatas"],
        )
        assert sorted(chroma_records["ids"]) == sorted(expected_ids)

        # 5. Calling reprocess on a ready document returns 409 Conflict.
        conflict_resp = client.post(f"/api/v1/documents/{doc_id}/reprocess")
        _assert_error_response(conflict_resp, 409, "DOCUMENT_CONFLICT")

        # 6. Delete returns 204 and removes SQLite row, chunks, Chroma vectors, and PDF.
        delete_resp = client.delete(f"/api/v1/documents/{doc_id}")
        assert delete_resp.status_code == 204
        assert delete_resp.content == b""

        assert not stored_pdf.exists()
        assert vector_store.count_by_document(doc_id) == 0
        with app.state.session_factory() as session:
            assert session.get(Document, doc_id) is None
            assert (
                session.execute(
                    select(DocumentChunk).where(DocumentChunk.document_id == doc_id)
                )
                .scalars()
                .all()
                == []
            )
        _assert_error_response(client.get(f"/api/v1/documents/{doc_id}"), 404, "DOCUMENT_NOT_FOUND")


def test_list_filtering_pagination_scanned_pdf_and_all_error_codes(tmp_path: Path) -> None:
    """Verify listing filters/pagination, scanned PDF background failure, and 4xx errors."""
    settings = make_isolated_settings(tmp_path, MAX_UPLOAD_SIZE_MB=1)
    app = create_app(
        settings,
        eager_load_embeddings=True,
        embedding_service=DeterministicEmbeddingService(),
    )

    with TestClient(app) as client:
        # 400: empty 0-byte upload and missing/blank filename stem.
        err_empty_bytes = client.post(
            "/api/v1/documents",
            files={"file": ("empty.pdf", b"", "application/pdf")},
        )
        _assert_error_response(err_empty_bytes, 400, "BAD_REQUEST")

        err_blank_stem = client.post(
            "/api/v1/documents",
            files={"file": (".pdf", b"%PDF-1.4\n", "application/pdf")},
        )
        _assert_error_response(err_blank_stem, 400, "BAD_REQUEST")

        # 413: file larger than 1 MB limit.
        oversized = b"%PDF-1.4\n" + (b"A" * (1024 * 1024 + 128))
        err_large = client.post(
            "/api/v1/documents",
            files={"file": ("oversized.pdf", oversized, "application/pdf")},
        )
        _assert_error_response(err_large, 413, "FILE_TOO_LARGE")

        # 415: non-pdf extension and fake pdf content without valid PDF structure.
        err_ext = client.post(
            "/api/v1/documents",
            files={"file": ("notes.txt", b"plain text", "application/pdf")},
        )
        _assert_error_response(err_ext, 415, "UNSUPPORTED_MEDIA_TYPE")

        err_corrupt = client.post(
            "/api/v1/documents",
            files={"file": ("corrupt.pdf", b"%PDF-1.4\nnot valid pdf data", "application/pdf")},
        )
        _assert_error_response(err_corrupt, 415, "UNSUPPORTED_MEDIA_TYPE")

        # 422: encrypted PDF, zero-page PDF, missing file, bad UUID, bad pagination.
        err_enc = client.post(
            "/api/v1/documents",
            files={"file": ("locked.pdf", make_encrypted_pdf_bytes(), "application/pdf")},
        )
        _assert_error_response(err_enc, 422, "ENCRYPTED_PDF_NOT_SUPPORTED")

        err_zero = client.post(
            "/api/v1/documents",
            files={"file": ("zero.pdf", make_zero_page_pdf_bytes(), "application/pdf")},
        )
        _assert_error_response(err_zero, 422, "EMPTY_PDF_NOT_SUPPORTED")

        err_no_file = client.post("/api/v1/documents")
        _assert_error_response(err_no_file, 422, "VALIDATION_ERROR")

        err_bad_uuid = client.get("/api/v1/documents/not-a-uuid")
        _assert_error_response(err_bad_uuid, 422, "VALIDATION_ERROR")

        err_bad_query = client.get("/api/v1/documents?limit=200")
        _assert_error_response(err_bad_query, 422, "VALIDATION_ERROR")

        # 404: unknown document UUID on get, reprocess, and delete.
        missing_id = str(uuid.uuid4())
        _assert_error_response(
            client.get(f"/api/v1/documents/{missing_id}"),
            404,
            "DOCUMENT_NOT_FOUND",
        )
        _assert_error_response(
            client.post(f"/api/v1/documents/{missing_id}/reprocess"),
            404,
            "DOCUMENT_NOT_FOUND",
        )
        _assert_error_response(
            client.delete(f"/api/v1/documents/{missing_id}"),
            404,
            "DOCUMENT_NOT_FOUND",
        )

        # Upload 2 valid PDFs and 1 scanned image PDF (which fails in background).
        r1 = client.post(
            "/api/v1/documents",
            files={
                "file": (
                    "first.pdf",
                    make_text_pdf_bytes(["First doc text"]),
                    "application/pdf",
                )
            },
        )
        assert r1.status_code == 202
        id1 = r1.json()["id"]
        time.sleep(0.02)

        r2 = client.post(
            "/api/v1/documents",
            files={
                "file": (
                    "scanned.pdf",
                    make_scanned_pdf_bytes(page_count=2),
                    "application/pdf",
                )
            },
        )
        assert r2.status_code == 202
        id2 = r2.json()["id"]
        time.sleep(0.02)

        r3 = client.post(
            "/api/v1/documents",
            files={
                "file": (
                    "third.pdf",
                    make_text_pdf_bytes(["Third doc text"]),
                    "application/pdf",
                )
            },
        )
        assert r3.status_code == 202
        id3 = r3.json()["id"]

        d1 = poll_until_terminal(client, id1)
        d2 = poll_until_terminal(client, id2)
        d3 = poll_until_terminal(client, id3)

        assert d1["status"] == "ready"
        assert d3["status"] == "ready"
        assert d2["status"] == "failed"
        assert d2["chunk_count"] == 0
        assert "EMPTY_PDF_NOT_SUPPORTED" in (d2["error_message"] or "")
        assert app.state.vector_store.count_by_document(id2) == 0

        # List all newest-first and test pagination + status filtering.
        list_all = DocumentListResponse.model_validate(client.get("/api/v1/documents").json())
        assert list_all.total == 3
        assert [str(item.id) for item in list_all.items] == [id3, id2, id1]

        page_2 = DocumentListResponse.model_validate(
            client.get("/api/v1/documents?limit=1&offset=1").json()
        )
        assert page_2.total == 3
        assert page_2.limit == 1
        assert page_2.offset == 1
        assert [str(item.id) for item in page_2.items] == [id2]

        ready_only = DocumentListResponse.model_validate(
            client.get("/api/v1/documents?status=ready").json()
        )
        assert ready_only.total == 2
        assert [str(item.id) for item in ready_only.items] == [id3, id1]

        failed_only = DocumentListResponse.model_validate(
            client.get("/api/v1/documents?status=failed").json()
        )
        assert failed_only.total == 1
        assert [str(item.id) for item in failed_only.items] == [id2]


def test_reprocess_idempotency_and_raw_sql_cascade_delete(tmp_path: Path) -> None:
    """Verify reprocess overwrites idempotently without duplicates and raw SQL cascades chunks."""
    settings = make_isolated_settings(tmp_path)

    class ToggleFailureExtractor:
        def __init__(self) -> None:
            self.fail_next = True
            self._delegate = PDFExtractor()

        def extract(self, file_path: str | Path) -> list[ExtractedPage]:
            if self.fail_next:
                self.fail_next = False
                raise ExtractionError("Transient extraction error for test")
            return self._delegate.extract(file_path)

    app = create_app(
        settings,
        eager_load_embeddings=True,
        extractor=ToggleFailureExtractor(),
        embedding_service=DeterministicEmbeddingService(),
    )

    pdf_bytes = make_text_pdf_bytes(["Reprocess idempotency verification text " * 15])

    with TestClient(app) as client:
        resp = client.post(
            "/api/v1/documents",
            files={"file": ("retry.pdf", pdf_bytes, "application/pdf")},
        )
        assert resp.status_code == 202
        doc_id = resp.json()["id"]

        failed_doc = poll_until_terminal(client, doc_id)
        assert failed_doc["status"] == "failed"
        assert "EXTRACTION_ERROR" in failed_doc["error_message"]

        # Reprocess transitions failed -> pending (202) and runs to ready.
        reprocess_resp = client.post(f"/api/v1/documents/{doc_id}/reprocess")
        assert reprocess_resp.status_code == 202
        reprocessed = DocumentResponse.model_validate(reprocess_resp.json())
        assert reprocessed.status == DocumentStatus.PENDING
        assert reprocessed.error_message is None
        assert reprocessed.chunk_count == 0

        ready_doc = poll_until_terminal(client, doc_id)
        assert ready_doc["status"] == "ready"
        chunk_count = ready_doc["chunk_count"]
        assert chunk_count >= 1
        assert app.state.vector_store.count_by_document(doc_id) == chunk_count
        stored = app.state.vector_store._collection.get(
            where={"document_id": doc_id},
            include=[],
        )
        assert sorted(stored["ids"]) == sorted(f"{doc_id}:{idx}" for idx in range(chunk_count))
        _assert_error_response(
            client.post(f"/api/v1/documents/{doc_id}/reprocess"),
            409,
            "DOCUMENT_CONFLICT",
        )

        # Verify raw SQL DELETE cascades to document_chunks via PRAGMA foreign_keys=ON.
        with app.state.session_factory() as session:
            before_chunks = list(
                session.execute(
                    select(DocumentChunk).where(DocumentChunk.document_id == doc_id)
                )
                .scalars()
                .all()
            )
            assert len(before_chunks) == chunk_count

            session.execute(text("DELETE FROM documents WHERE id = :doc_id"), {"doc_id": doc_id})
            session.commit()

            after_chunks = list(
                session.execute(
                    select(DocumentChunk).where(DocumentChunk.document_id == doc_id)
                )
                .scalars()
                .all()
            )
            assert after_chunks == []


def test_upload_db_failure_cleans_up_orphan_pdf(tmp_path: Path, monkeypatch: Any) -> None:
    """If DB persistence fails after file validation, zero orphan PDFs remain in UPLOAD_DIR."""
    settings = make_isolated_settings(tmp_path)
    app = create_app(
        settings,
        eager_load_embeddings=False,
        embedding_service=DeterministicEmbeddingService(),
    )

    with TestClient(app, raise_server_exceptions=False) as client:
        def boom(*_args: Any, **_kwargs: Any) -> Any:
            raise RuntimeError("Simulated SQLite write failure")

        monkeypatch.setattr(app.state.document_repo, "create_document", boom)

        resp = client.post(
            "/api/v1/documents",
            files={"file": ("orphan_check.pdf", make_text_pdf_bytes(["Hello"]), "application/pdf")},
        )
        _assert_error_response(resp, 500, "INTERNAL_ERROR")
        upload_files = list(Path(settings.UPLOAD_DIR).glob("*.pdf"))
        assert upload_files == []


def test_health_degraded_and_503_dependency_failures(tmp_path: Path, monkeypatch: Any) -> None:
    """Verify /health returns 200 degraded on failed eager load and 503 on DB/Chroma errors."""
    settings = make_isolated_settings(tmp_path)
    failing_embeddings = DeterministicEmbeddingService(fail_load=True)
    app = create_app(
        settings,
        eager_load_embeddings=True,
        embedding_service=failing_embeddings,
    )

    with TestClient(app) as client:
        # Boot must succeed even when eager embedding load failed -> 200 degraded.
        resp = client.get("/api/v1/health")
        assert resp.status_code == 200
        health = HealthResponse.model_validate(resp.json())
        assert health.status == "degraded"
        assert health.database == "ok"
        assert health.vector_store == "ok"
        assert health.embedding_model_loaded is False

        # DB failure -> 503 ErrorResponse.
        monkeypatch.setattr(app.state.document_repo, "health_check", lambda: False)
        db_err = client.get("/api/v1/health")
        _assert_error_response(db_err, 503, "SERVICE_UNAVAILABLE")

        monkeypatch.undo()

        # Vector store exception -> 503 ErrorResponse.
        def broken_chroma() -> bool:
            raise RuntimeError("Chroma unreachable")

        monkeypatch.setattr(app.state.vector_store, "health_check", broken_chroma)
        chroma_err = client.get("/api/v1/health")
        _assert_error_response(chroma_err, 503, "SERVICE_UNAVAILABLE")
