"""End-to-end persistence, cross-page provenance, ChromaDB, and delete verification."""

from __future__ import annotations

import math
from datetime import UTC
from pathlib import Path

import chromadb
import httpx
from sqlalchemy import select
from tests.e2e.conftest import (
    assert_utc_iso_timestamp,
    poll_document_until_terminal,
    run_live_backend,
)
from tests.fixtures import make_multipage_crosspage_pdf_bytes

from app.db.session import create_db_engine, create_session_factory
from app.models.document import Document, DocumentChunk
from app.repositories.vector_store import ChromaVectorStore


def _euclidean_norm(vec: list[float]) -> float:
    return math.sqrt(sum(float(x) * float(x) for x in vec))


def test_multipage_crosspage_ingestion_persistence_and_delete_e2e(tmp_path: Path) -> None:
    """Verify live upload -> ready -> SQLite + ChromaDB provenance -> 409 reprocess -> 204."""
    pdf_bytes = make_multipage_crosspage_pdf_bytes()
    doc_id: str
    chunk_count: int

    with run_live_backend(tmp_path) as server:
        with httpx.Client(base_url=server.base_url, timeout=15.0) as client:
            health_resp = client.get("/api/v1/health")
            assert health_resp.status_code == 200
            health = health_resp.json()
            assert health["status"] == "ok"
            assert health["database"] == "ok"
            assert health["vector_store"] == "ok"
            assert health["embedding_model_loaded"] is True
            assert_utc_iso_timestamp(health["timestamp"])

            upload_resp = client.post(
                "/api/v1/documents",
                files={"file": ("cross_page_report.pdf", pdf_bytes, "application/pdf")},
            )
            assert upload_resp.status_code == 202
            accepted = upload_resp.json()
            doc_id = accepted["id"]
            assert accepted["filename"] == "cross_page_report.pdf"
            assert accepted["content_type"] == "application/pdf"
            assert accepted["file_size_bytes"] == len(pdf_bytes)
            assert accepted["status"] == "pending"
            assert accepted["page_count"] == 3
            assert accepted["chunk_count"] == 0
            assert accepted["error_message"] is None
            assert_utc_iso_timestamp(accepted["created_at"])
            assert_utc_iso_timestamp(accepted["updated_at"])

            stored_pdf = server.upload_dir / f"{doc_id}.pdf"
            assert stored_pdf.is_file()

            ready = poll_document_until_terminal(client, doc_id, timeout=30.0)
            assert ready["status"] == "ready"
            assert ready["page_count"] == 3
            assert ready["chunk_count"] >= 2
            assert ready["error_message"] is None
            assert_utc_iso_timestamp(ready["created_at"])
            assert_utc_iso_timestamp(ready["updated_at"])
            chunk_count = int(ready["chunk_count"])

            # Reprocess on a ready document must return 409 DOCUMENT_CONFLICT.
            reprocess_conflict = client.post(f"/api/v1/documents/{doc_id}/reprocess")
            assert reprocess_conflict.status_code == 409
            assert reprocess_conflict.json()["error"]["code"] == "DOCUMENT_CONFLICT"

        exit_code, _output = server.stop()
        assert exit_code == 0

    # Inspect real SQLite DB and real ChromaDB PersistentClient directly after clean shutdown.
    expected_ids = [f"{doc_id}:{idx}" for idx in range(chunk_count)]
    engine = create_db_engine(f"sqlite:///{(tmp_path / 'db' / 'docsense.db').as_posix()}")
    try:
        session_factory = create_session_factory(engine)
        with session_factory() as session:
            doc_row = session.get(Document, doc_id)
            assert doc_row is not None
            assert doc_row.status == "ready"
            assert doc_row.page_count == 3
            assert doc_row.chunk_count == chunk_count
            assert doc_row.created_at.tzinfo == UTC
            assert doc_row.updated_at.tzinfo == UTC

            chunks = list(
                session.execute(
                    select(DocumentChunk)
                    .where(DocumentChunk.document_id == doc_id)
                    .order_by(DocumentChunk.chunk_index.asc())
                )
                .scalars()
                .all()
            )
            assert len(chunks) == chunk_count
            assert [c.chunk_index for c in chunks] == list(range(chunk_count))
            assert [c.chroma_id for c in chunks] == expected_ids
            assert all(0 < c.token_count <= 254 for c in chunks)
            assert all(c.created_at.tzinfo == UTC for c in chunks)
            assert all(1 <= c.page_number <= c.page_end <= 3 for c in chunks)

            cross_page_chunks = [
                c
                for c in chunks
                if c.page_number == 1 and c.page_end == 3 and c.page_numbers_csv == "1,3"
            ]
            assert cross_page_chunks, (
                f"Expected cross-page chunk spanning pages 1 and 3, got: "
                f"{[(c.page_number, c.page_end, c.page_numbers_csv) for c in chunks]}"
            )
    finally:
        engine.dispose()

    chroma_client = chromadb.PersistentClient(path=str(tmp_path / "chroma"))
    try:
        collection = chroma_client.get_collection(name="docsense_chunks")
        assert collection.metadata is not None
        assert collection.metadata.get("hnsw:space") == "cosine"

        records = collection.get(
            where={"document_id": doc_id},
            include=["metadatas", "embeddings", "documents"],
        )
        assert len(records["ids"]) == chunk_count
        assert sorted(records["ids"]) == sorted(expected_ids)

        expected_meta_keys = {
            "document_id",
            "filename",
            "page_number",
            "page_end",
            "page_numbers_csv",
            "chunk_index",
            "token_count",
        }
        for rec_id, metadata, embedding in zip(
            records["ids"],
            records["metadatas"],
            records["embeddings"],
            strict=True,
        ):
            assert set(metadata.keys()) == expected_meta_keys
            assert metadata["document_id"] == doc_id
            assert metadata["filename"] == "cross_page_report.pdf"
            expected_index = int(rec_id.split(":")[1])
            assert metadata["chunk_index"] == expected_index
            assert 1 <= int(metadata["page_number"]) <= int(metadata["page_end"]) <= 3
            assert 0 < int(metadata["token_count"]) <= 254
            assert len(embedding) == 384
            assert abs(_euclidean_norm(list(embedding)) - 1.0) < 1e-4
    finally:
        ChromaVectorStore._release_client_handles(chroma_client)

    # Restart against same storage and delete the document completely.
    with run_live_backend(tmp_path) as server2:
        with httpx.Client(base_url=server2.base_url, timeout=15.0) as client:
            del_resp = client.delete(f"/api/v1/documents/{doc_id}")
            assert del_resp.status_code == 204
            assert del_resp.content == b""

            get_deleted = client.get(f"/api/v1/documents/{doc_id}")
            assert get_deleted.status_code == 404
            assert get_deleted.json()["error"]["code"] == "DOCUMENT_NOT_FOUND"

        exit_code, _output = server2.stop()
        assert exit_code == 0

    assert not (tmp_path / "uploads" / f"{doc_id}.pdf").exists()
    assert list((tmp_path / "uploads").iterdir()) == []

    engine_after = create_db_engine(f"sqlite:///{(tmp_path / 'db' / 'docsense.db').as_posix()}")
    try:
        session_factory_after = create_session_factory(engine_after)
        with session_factory_after() as session:
            assert session.get(Document, doc_id) is None
            assert (
                session.execute(
                    select(DocumentChunk).where(DocumentChunk.document_id == doc_id)
                )
                .scalars()
                .all()
                == []
            )
    finally:
        engine_after.dispose()

    chroma_after = chromadb.PersistentClient(path=str(tmp_path / "chroma"))
    try:
        collection_after = chroma_after.get_collection(name="docsense_chunks")
        remaining = collection_after.get(where={"document_id": doc_id}, include=[])
        assert remaining["ids"] == []
    finally:
        ChromaVectorStore._release_client_handles(chroma_after)
