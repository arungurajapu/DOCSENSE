"""Live Uvicorn restart recovery and cross-origin CORS E2E tests."""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

import chromadb
import httpx
from sqlalchemy import select
from tests.e2e.conftest import poll_document_until_terminal, run_live_backend
from tests.fixtures import make_text_pdf_bytes

from app.core.config import Settings
from app.db.session import create_db_engine, create_session_factory, init_db
from app.models.document import Document, DocumentChunk
from app.repositories.document_repo import DocumentRepository
from app.repositories.vector_store import ChromaVectorStore
from app.services.interfaces import TextChunk


def test_live_restart_startup_recovery_reprocess_and_cors_e2e(tmp_path: Path) -> None:
    """Verify live restart recovery across ready/pending/processing/deleting and live CORS."""
    db_path = tmp_path / "db" / "docsense.db"
    chroma_dir = tmp_path / "chroma"
    upload_dir = tmp_path / "uploads"

    # Phase 1: Start live server, upload a ready document, then stop cleanly.
    ready_pdf_bytes = make_text_pdf_bytes(["Persisted ready document text across restart."])
    ready_id: str
    ready_chunks: int

    with run_live_backend(tmp_path) as server1:
        with httpx.Client(base_url=server1.base_url, timeout=15.0) as client:
            upload_resp = client.post(
                "/api/v1/documents",
                files={"file": ("persisted_ready.pdf", ready_pdf_bytes, "application/pdf")},
            )
            assert upload_resp.status_code == 202
            ready_id = upload_resp.json()["id"]
            ready_doc = poll_document_until_terminal(client, ready_id, timeout=30.0)
            assert ready_doc["status"] == "ready"
            assert ready_doc["chunk_count"] >= 1
            ready_chunks = int(ready_doc["chunk_count"])

        exit_code, _ = server1.stop()
        assert exit_code == 0

    # Phase 2: Seed pre-restart pending, processing (with partial chunk/vector), and deleting data.
    pending_id = str(uuid.uuid4())
    processing_id = str(uuid.uuid4())
    deleting_id = str(uuid.uuid4())

    pending_pdf = upload_dir / f"{pending_id}.pdf"
    processing_pdf = upload_dir / f"{processing_id}.pdf"
    deleting_pdf = upload_dir / f"{deleting_id}.pdf"

    pending_bytes = make_text_pdf_bytes(["Pre-restart pending document to resume automatically."])
    processing_bytes = make_text_pdf_bytes(
        ["Pre-restart interrupted processing document ready to reprocess cleanly."]
    )
    deleting_bytes = make_text_pdf_bytes(["Pre-restart deleting document to purge on boot."])

    pending_pdf.write_bytes(pending_bytes)
    processing_pdf.write_bytes(processing_bytes)
    deleting_pdf.write_bytes(deleting_bytes)

    seed_settings = Settings(
        _env_file=None,
        DATABASE_URL=f"sqlite:///{db_path.as_posix()}",
        CHROMA_PERSIST_DIR=str(chroma_dir),
        UPLOAD_DIR=str(upload_dir),
    )
    seed_engine = create_db_engine(seed_settings.DATABASE_URL)
    seed_store = ChromaVectorStore(settings=seed_settings)
    try:
        init_db(seed_engine)
        session_factory = create_session_factory(seed_engine)
        repo = DocumentRepository(session_factory)

        repo.create_document(
            document_id=pending_id,
            filename="seeded_pending.pdf",
            content_type="application/pdf",
            file_size_bytes=len(pending_bytes),
            sha256_hash=hashlib.sha256(pending_bytes).hexdigest(),
            storage_path=str(pending_pdf),
            page_count=1,
        )

        repo.create_document(
            document_id=processing_id,
            filename="seeded_processing.pdf",
            content_type="application/pdf",
            file_size_bytes=len(processing_bytes),
            sha256_hash=hashlib.sha256(processing_bytes).hexdigest(),
            storage_path=str(processing_pdf),
            page_count=1,
        )
        assert repo.claim_for_processing(processing_id) is True

        repo.create_document(
            document_id=deleting_id,
            filename="seeded_deleting.pdf",
            content_type="application/pdf",
            file_size_bytes=len(deleting_bytes),
            sha256_hash=hashlib.sha256(deleting_bytes).hexdigest(),
            storage_path=str(deleting_pdf),
            page_count=1,
        )
        repo.mark_deleting(deleting_id)

        partial_chunk = TextChunk(
            chunk_index=0,
            text="Partial interrupted chunk",
            page_number=1,
            page_end=1,
            page_numbers_csv="1",
            token_count=3,
            char_start=0,
            char_end=25,
            content_preview="Partial interrupted chunk",
        )
        with session_factory() as session:
            session.add_all(
                [
                    DocumentChunk(
                        document_id=processing_id,
                        chunk_index=0,
                        page_number=1,
                        page_end=1,
                        page_numbers_csv="1",
                        token_count=3,
                        char_start=0,
                        char_end=25,
                        content_preview="Partial interrupted chunk",
                        chroma_id=f"{processing_id}:0",
                    ),
                    DocumentChunk(
                        document_id=deleting_id,
                        chunk_index=0,
                        page_number=1,
                        page_end=1,
                        page_numbers_csv="1",
                        token_count=3,
                        char_start=0,
                        char_end=25,
                        content_preview="Deleting chunk",
                        chroma_id=f"{deleting_id}:0",
                    ),
                ]
            )
            session.commit()

        unit_vec = [1.0] + [0.0] * 383
        seed_store.upsert_chunks(
            processing_id,
            "seeded_processing.pdf",
            [partial_chunk],
            [unit_vec],
        )
        seed_store.upsert_chunks(
            deleting_id,
            "seeded_deleting.pdf",
            [partial_chunk],
            [unit_vec],
        )
    finally:
        seed_store.close()
        seed_engine.dispose()

    # Phase 3: Start fresh live server against same persistence directories.
    with run_live_backend(tmp_path) as server2:
        with httpx.Client(base_url=server2.base_url, timeout=15.0) as client:
            # 1. Existing ready doc persisted cleanly.
            ready_get = client.get(f"/api/v1/documents/{ready_id}")
            assert ready_get.status_code == 200
            assert ready_get.json()["status"] == "ready"
            assert ready_get.json()["chunk_count"] == ready_chunks

            # 2. Deleting doc purged on startup.
            assert client.get(f"/api/v1/documents/{deleting_id}").status_code == 404
            assert not deleting_pdf.exists()

            # 3. Interrupted processing doc marked failed with partial chunks cleared.
            proc_get = client.get(f"/api/v1/documents/{processing_id}")
            assert proc_get.status_code == 200
            proc_body = proc_get.json()
            assert proc_body["status"] == "failed"
            assert proc_body["chunk_count"] == 0
            assert (
                proc_body["error_message"]
                == "Ingestion interrupted by server restart. Please retry reprocessing."
            )

            # 4. Pending doc automatically resumed to ready.
            recovered_pending = poll_document_until_terminal(client, pending_id, timeout=30.0)
            assert recovered_pending["status"] == "ready"
            assert recovered_pending["chunk_count"] >= 1

            # 5. Reprocess interrupted processing doc via HTTP -> ready.
            reproc_resp = client.post(
                f"/api/v1/documents/{processing_id}/reprocess",
                headers={"Origin": "http://localhost:3100"},
            )
            assert reproc_resp.status_code == 202
            assert reproc_resp.json()["status"] == "pending"
            assert reproc_resp.headers.get("access-control-allow-origin") == "http://localhost:3100"
            assert reproc_resp.headers.get("access-control-allow-credentials") == "true"

            recovered_proc = poll_document_until_terminal(client, processing_id, timeout=30.0)
            assert recovered_proc["status"] == "ready"
            assert recovered_proc["chunk_count"] >= 1
            proc_chunks = int(recovered_proc["chunk_count"])

            # 6. Live OPTIONS preflight & GET/POST CORS verification (allowed vs disallowed).
            opt_allowed = client.options(
                "/api/v1/documents",
                headers={
                    "Origin": "http://localhost:3100",
                    "Access-Control-Request-Method": "POST",
                    "Access-Control-Request-Headers": "content-type",
                },
            )
            assert opt_allowed.status_code == 200
            assert opt_allowed.headers.get("access-control-allow-origin") == "http://localhost:3100"
            assert opt_allowed.headers.get("access-control-allow-credentials") == "true"
            assert "POST" in (opt_allowed.headers.get("access-control-allow-methods") or "")

            get_allowed = client.get(
                "/api/v1/health",
                headers={"Origin": "http://localhost:3100"},
            )
            assert get_allowed.status_code == 200
            assert get_allowed.headers.get("access-control-allow-origin") == "http://localhost:3100"
            assert get_allowed.headers.get("access-control-allow-credentials") == "true"

            post_allowed = client.post(
                "/api/v1/documents",
                headers={"Origin": "http://localhost:3100"},
                files={
                    "file": (
                        "cors_upload.pdf",
                        make_text_pdf_bytes(["CORS live upload check."]),
                        "application/pdf",
                    )
                },
            )
            assert post_allowed.status_code == 202
            assert post_allowed.headers.get("access-control-allow-origin") == "http://localhost:3100"
            assert post_allowed.headers.get("access-control-allow-credentials") == "true"
            cors_id = post_allowed.json()["id"]
            assert poll_document_until_terminal(client, cors_id, timeout=30.0)["status"] == "ready"

            opt_disallowed = client.options(
                "/api/v1/documents",
                headers={
                    "Origin": "http://evil.example.com",
                    "Access-Control-Request-Method": "POST",
                },
            )
            assert opt_disallowed.status_code == 400
            assert "access-control-allow-origin" not in opt_disallowed.headers

            get_disallowed = client.get(
                "/api/v1/health",
                headers={"Origin": "http://evil.example.com"},
            )
            assert get_disallowed.status_code == 200
            assert "access-control-allow-origin" not in get_disallowed.headers

            post_disallowed = client.post(
                "/api/v1/documents",
                headers={"Origin": "http://evil.example.com"},
                files={"file": (".pdf", b"%PDF-1.4\n", "application/pdf")},
            )
            assert post_disallowed.status_code == 400
            assert "access-control-allow-origin" not in post_disallowed.headers

        exit_code, _ = server2.stop()
        assert exit_code == 0

    # Verify final SQLite & ChromaDB state after clean shutdown.
    verify_engine = create_db_engine(f"sqlite:///{db_path.as_posix()}")
    try:
        with create_session_factory(verify_engine)() as session:
            assert session.get(Document, deleting_id) is None
            assert (
                session.execute(
                    select(DocumentChunk).where(DocumentChunk.document_id == deleting_id)
                )
                .scalars()
                .all()
                == []
            )
            proc_row = session.get(Document, processing_id)
            assert proc_row is not None and proc_row.status == "ready"
            proc_db_chunks = (
                session.execute(
                    select(DocumentChunk).where(DocumentChunk.document_id == processing_id)
                )
                .scalars()
                .all()
            )
            assert len(proc_db_chunks) == proc_chunks
    finally:
        verify_engine.dispose()

    chroma = chromadb.PersistentClient(path=str(chroma_dir))
    try:
        collection = chroma.get_collection("docsense_chunks")
        assert collection.get(where={"document_id": deleting_id}, include=[])["ids"] == []
        assert len(collection.get(where={"document_id": processing_id}, include=[])["ids"]) == (
            proc_chunks
        )
        assert len(collection.get(where={"document_id": ready_id}, include=[])["ids"]) == (
            ready_chunks
        )
    finally:
        ChromaVectorStore._release_client_handles(chroma)
