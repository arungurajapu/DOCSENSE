"""Startup recovery integration tests across SQLite, ChromaDB, and local uploads."""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select
from tests.integration.conftest import (
    DeterministicEmbeddingService,
    make_isolated_settings,
    make_text_pdf_bytes,
    poll_until_terminal,
)

from app.db.session import create_db_engine, create_session_factory, init_db
from app.main import create_app
from app.models.document import Document, DocumentChunk, DocumentStatus
from app.repositories.document_repo import DocumentRepository
from app.repositories.vector_store import ChromaVectorStore
from app.services.interfaces import TextChunk


def test_startup_recovery_reconciles_pending_processing_and_deleting_rows(
    tmp_path: Path,
) -> None:
    """Pre-restart pending/processing/deleting rows reconcile cleanly on lifespan boot."""
    settings = make_isolated_settings(tmp_path)
    upload_dir = Path(settings.UPLOAD_DIR)
    upload_dir.mkdir(parents=True, exist_ok=True)

    pending_id = str(uuid.uuid4())
    processing_id = str(uuid.uuid4())
    deleting_id = str(uuid.uuid4())

    pending_pdf = upload_dir / f"{pending_id}.pdf"
    processing_pdf = upload_dir / f"{processing_id}.pdf"
    deleting_pdf = upload_dir / f"{deleting_id}.pdf"

    pending_pdf.write_bytes(
        make_text_pdf_bytes(["Pending page one text", "Pending page two text"])
    )
    processing_pdf.write_bytes(
        make_text_pdf_bytes(["Recovered processing PDF text ready for reprocess"])
    )
    deleting_pdf.write_bytes(make_text_pdf_bytes(["Deleting PDF should be removed"]))

    # Seed SQLite metadata + partial chunks and ChromaDB vectors before app boot.
    seed_engine = create_db_engine(settings.DATABASE_URL)
    seed_store = ChromaVectorStore(settings=settings)
    try:
        init_db(seed_engine)
        session_factory = create_session_factory(seed_engine)
        repo = DocumentRepository(session_factory)

        repo.create_document(
            document_id=pending_id,
            filename="pending.pdf",
            content_type="application/pdf",
            file_size_bytes=pending_pdf.stat().st_size,
            sha256_hash="a" * 64,
            storage_path=str(pending_pdf),
            page_count=2,
        )

        repo.create_document(
            document_id=processing_id,
            filename="processing.pdf",
            content_type="application/pdf",
            file_size_bytes=processing_pdf.stat().st_size,
            sha256_hash="b" * 64,
            storage_path=str(processing_pdf),
            page_count=1,
        )
        assert repo.claim_for_processing(processing_id) is True

        repo.create_document(
            document_id=deleting_id,
            filename="deleting.pdf",
            content_type="application/pdf",
            file_size_bytes=deleting_pdf.stat().st_size,
            sha256_hash="c" * 64,
            storage_path=str(deleting_pdf),
            page_count=1,
        )
        repo.mark_deleting(deleting_id)

        residual_chunk = TextChunk(
            chunk_index=0,
            text="Residual chunk from interrupted run",
            page_number=1,
            page_end=1,
            page_numbers_csv="1",
            token_count=6,
            char_start=0,
            char_end=35,
            content_preview="Residual chunk from interrupted run",
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
                        token_count=6,
                        char_start=0,
                        char_end=35,
                        content_preview="Residual chunk",
                        chroma_id=f"{processing_id}:0",
                    ),
                    DocumentChunk(
                        document_id=deleting_id,
                        chunk_index=0,
                        page_number=1,
                        page_end=1,
                        page_numbers_csv="1",
                        token_count=6,
                        char_start=0,
                        char_end=35,
                        content_preview="Deleting chunk",
                        chroma_id=f"{deleting_id}:0",
                    ),
                ]
            )
            session.commit()

        vec = [1.0] + [0.0] * 383
        seed_store.upsert_chunks(processing_id, "processing.pdf", [residual_chunk], [vec])
        seed_store.upsert_chunks(deleting_id, "deleting.pdf", [residual_chunk], [vec])
        assert seed_store.count_by_document(processing_id) == 1
        assert seed_store.count_by_document(deleting_id) == 1
    finally:
        seed_store.close()
        seed_engine.dispose()

    embeddings = DeterministicEmbeddingService()
    app = create_app(
        settings,
        eager_load_embeddings=True,
        embedding_service=embeddings,
    )

    with TestClient(app) as client:
        # 1. Deleting document must be completely purged prior to serving requests.
        del_resp = client.get(f"/api/v1/documents/{deleting_id}")
        assert del_resp.status_code == 404
        assert not deleting_pdf.exists()
        assert app.state.vector_store.count_by_document(deleting_id) == 0

        # 2. Processing document must be marked failed with partial chunks/vectors purged.
        proc_resp = client.get(f"/api/v1/documents/{processing_id}")
        assert proc_resp.status_code == 200
        proc_body = proc_resp.json()
        assert proc_body["status"] == DocumentStatus.FAILED.value
        assert (
            proc_body["error_message"]
            == "Ingestion interrupted by server restart. Please retry reprocessing."
        )
        assert proc_body["chunk_count"] == 0
        assert app.state.vector_store.count_by_document(processing_id) == 0
        with app.state.session_factory() as session:
            assert (
                session.execute(
                    select(DocumentChunk).where(DocumentChunk.document_id == processing_id)
                )
                .scalars()
                .all()
                == []
            )
            assert session.get(Document, deleting_id) is None

        # 3. Pending document must be rescheduled and complete to ready.
        ready_pending = poll_until_terminal(client, pending_id)
        assert ready_pending["status"] == DocumentStatus.READY.value
        assert ready_pending["chunk_count"] >= 1
        assert app.state.vector_store.count_by_document(pending_id) == ready_pending["chunk_count"]

        # 4. Interrupted processing document can be reprocessed cleanly to ready.
        reprocess_resp = client.post(f"/api/v1/documents/{processing_id}/reprocess")
        assert reprocess_resp.status_code == 202
        reprocess_body = reprocess_resp.json()
        assert reprocess_body["status"] == DocumentStatus.PENDING.value
        assert reprocess_body["error_message"] is None

        ready_proc = poll_until_terminal(client, processing_id)
        assert ready_proc["status"] == DocumentStatus.READY.value
        assert ready_proc["chunk_count"] >= 1
        assert (
            app.state.vector_store.count_by_document(processing_id)
            == ready_proc["chunk_count"]
        )
