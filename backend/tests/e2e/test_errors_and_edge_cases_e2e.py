"""End-to-end backend error handling, boundary checks, and degraded startup E2E tests."""

from __future__ import annotations

from pathlib import Path

import chromadb
import httpx
from sqlalchemy import select
from tests.e2e.conftest import poll_document_until_terminal, run_live_backend
from tests.fixtures import (
    make_corrupt_pdf_bytes,
    make_encrypted_pdf_bytes,
    make_scanned_pdf_bytes,
    make_spoofed_pdf_bytes,
    make_zero_page_pdf_bytes,
)

from app.db.session import create_db_engine, create_session_factory
from app.models.document import Document, DocumentChunk
from app.repositories.vector_store import ChromaVectorStore


def _assert_error(resp: httpx.Response, expected_status: int, expected_code: str) -> None:
    assert resp.status_code == expected_status, (
        f"Expected HTTP {expected_status}, got {resp.status_code}: {resp.text}"
    )
    body = resp.json()
    assert set(body.keys()) == {"error"}
    assert body["error"]["code"] == expected_code
    assert isinstance(body["error"]["message"], str) and body["error"]["message"].strip()


def test_upload_error_boundaries_oversize_cutoff_and_scanned_pdf_e2e(tmp_path: Path) -> None:
    """Verify 400/413/415/422 error codes, zero residue, and scanned PDF background failure."""
    with run_live_backend(tmp_path) as server:
        with httpx.Client(base_url=server.base_url, timeout=30.0) as client:
            # 1. Empty 0-byte PDF -> 400 BAD_REQUEST
            _assert_error(
                client.post(
                    "/api/v1/documents",
                    files={"file": ("empty.pdf", b"", "application/pdf")},
                ),
                400,
                "BAD_REQUEST",
            )

            # 2. Stemless ".pdf" -> 400 BAD_REQUEST
            _assert_error(
                client.post(
                    "/api/v1/documents",
                    files={"file": (".pdf", b"%PDF-1.4\n", "application/pdf")},
                ),
                400,
                "BAD_REQUEST",
            )

            # 3. Spoofed .pdf (plain text bytes) -> 415 UNSUPPORTED_MEDIA_TYPE
            _assert_error(
                client.post(
                    "/api/v1/documents",
                    files={"file": ("spoofed.pdf", make_spoofed_pdf_bytes(), "application/pdf")},
                ),
                415,
                "UNSUPPORTED_MEDIA_TYPE",
            )

            # 4. Corrupt .pdf -> 415 UNSUPPORTED_MEDIA_TYPE
            _assert_error(
                client.post(
                    "/api/v1/documents",
                    files={"file": ("corrupt.pdf", make_corrupt_pdf_bytes(), "application/pdf")},
                ),
                415,
                "UNSUPPORTED_MEDIA_TYPE",
            )

            # 5. Non-.pdf extension -> 415 UNSUPPORTED_MEDIA_TYPE
            _assert_error(
                client.post(
                    "/api/v1/documents",
                    files={"file": ("notes.txt", b"hello", "text/plain")},
                ),
                415,
                "UNSUPPORTED_MEDIA_TYPE",
            )

            # 6. Encrypted PDF -> 422 ENCRYPTED_PDF_NOT_SUPPORTED
            _assert_error(
                client.post(
                    "/api/v1/documents",
                    files={
                        "file": ("encrypted.pdf", make_encrypted_pdf_bytes(), "application/pdf")
                    },
                ),
                422,
                "ENCRYPTED_PDF_NOT_SUPPORTED",
            )

            # 7. Zero-page PDF -> 422 EMPTY_PDF_NOT_SUPPORTED
            _assert_error(
                client.post(
                    "/api/v1/documents",
                    files={
                        "file": ("zeropage.pdf", make_zero_page_pdf_bytes(), "application/pdf")
                    },
                ),
                422,
                "EMPTY_PDF_NOT_SUPPORTED",
            )

            # 8. Oversize 25 MiB + 1 byte against default MAX_UPLOAD_SIZE_MB=25 -> 413
            max_bytes = 25 * 1024 * 1024
            oversize_payload = b"%PDF-1.4\n" + (b"A" * (max_bytes + 1 - 9))
            assert len(oversize_payload) == max_bytes + 1
            _assert_error(
                client.post(
                    "/api/v1/documents",
                    files={"file": ("too_large.pdf", oversize_payload, "application/pdf")},
                ),
                413,
                "FILE_TOO_LARGE",
            )

            # Zero residue in UPLOAD_DIR after all rejected uploads.
            assert not server.upload_dir.exists() or list(server.upload_dir.iterdir()) == []

            # 9. Scanned zero-text PDF -> 202 pending -> background failed EMPTY_PDF_NOT_SUPPORTED
            scanned_resp = client.post(
                "/api/v1/documents",
                files={"file": ("scanned.pdf", make_scanned_pdf_bytes(2), "application/pdf")},
            )
            assert scanned_resp.status_code == 202
            scanned_id = scanned_resp.json()["id"]

            failed_doc = poll_document_until_terminal(client, scanned_id, timeout=30.0)
            assert failed_doc["status"] == "failed"
            assert failed_doc["chunk_count"] == 0
            assert failed_doc["page_count"] == 2
            assert "EMPTY_PDF_NOT_SUPPORTED" in (failed_doc["error_message"] or "")

            # Delete scanned failed document cleanly.
            del_resp = client.delete(f"/api/v1/documents/{scanned_id}")
            assert del_resp.status_code == 204
            _assert_error(
                client.get(f"/api/v1/documents/{scanned_id}"),
                404,
                "DOCUMENT_NOT_FOUND",
            )

        exit_code, _output = server.stop()
        assert exit_code == 0

    assert list((tmp_path / "uploads").iterdir()) == []

    engine = create_db_engine(f"sqlite:///{(tmp_path / 'db' / 'docsense.db').as_posix()}")
    try:
        with create_session_factory(engine)() as session:
            assert session.execute(select(Document)).scalars().all() == []
            assert session.execute(select(DocumentChunk)).scalars().all() == []
    finally:
        engine.dispose()

    chroma = chromadb.PersistentClient(path=str(tmp_path / "chroma"))
    try:
        collection = chroma.get_collection("docsense_chunks")
        assert collection.count() == 0
    finally:
        ChromaVectorStore._release_client_handles(chroma)


def test_invalid_embedding_model_degrades_health_cleanly_e2e(tmp_path: Path) -> None:
    """Setting EMBEDDING_MODEL to an invalid identifier must boot degraded with 200 health."""
    with run_live_backend(
        tmp_path,
        env_overrides={
            "EMBEDDING_MODEL": "invalid/nonexistent-docsense-model-e2e",
            "HF_HUB_OFFLINE": "1",
        },
        startup_timeout=20.0,
    ) as server:
        with httpx.Client(base_url=server.base_url, timeout=5.0) as client:
            resp = client.get("/api/v1/health")
            assert resp.status_code == 200
            body = resp.json()
            assert body["status"] == "degraded"
            assert body["database"] == "ok"
            assert body["vector_store"] == "ok"
            assert body["embedding_model_loaded"] is False
