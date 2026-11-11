"""Unit tests for thread-safe persistent ChromaVectorStore."""

from __future__ import annotations

import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

from app.core.config import Settings
from app.core.exceptions import VectorStoreError
from app.repositories import ChromaVectorStore
from app.services.interfaces import TextChunk
from app.services.interfaces import VectorStore as VectorStoreProtocol


def _make_chunk(
    chunk_index: int,
    *,
    text: str | None = None,
    page_number: int = 1,
    page_end: int = 1,
    page_numbers_csv: str | None = None,
    token_count: int = 12,
) -> TextChunk:
    resolved_text = text if text is not None else f"Chunk {chunk_index} content"
    resolved_csv = page_numbers_csv if page_numbers_csv is not None else str(page_number)
    return TextChunk(
        chunk_index=chunk_index,
        text=resolved_text,
        page_number=page_number,
        page_end=page_end,
        page_numbers_csv=resolved_csv,
        token_count=token_count,
        char_start=chunk_index * 50,
        char_end=chunk_index * 50 + len(resolved_text),
        content_preview=resolved_text[:40],
    )


def _basis_embedding(dim_index: int, dim: int = 384) -> list[float]:
    vec = [0.0] * dim
    vec[dim_index % dim] = 1.0
    return vec


def test_vector_store_protocol_cosine_collection_and_scalar_metadata(
    test_settings: Settings,
) -> None:
    """Verify protocol, cosine collection metadata, deterministic IDs, and scalar metadata."""
    store = ChromaVectorStore(settings=test_settings)
    try:
        assert isinstance(store, VectorStoreProtocol)
        assert isinstance(store.chroma_io_lock, type(threading.Lock()))
        assert store.collection_name == test_settings.CHROMA_COLLECTION_NAME
        assert store.health_check() is True

        assert store._collection is not None
        assert store._collection.metadata is not None
        assert store._collection.metadata.get("hnsw:space") == "cosine"

        doc_id = "11111111-1111-1111-1111-111111111111"
        chunks = [
            _make_chunk(0, text="Page one intro", page_number=1, page_end=1, token_count=15),
            _make_chunk(
                1,
                text="Cross-page paragraph",
                page_number=1,
                page_end=3,
                page_numbers_csv="1,2,3",
                token_count=42,
            ),
        ]
        embeddings = [_basis_embedding(0), _basis_embedding(1)]

        store.upsert_chunks(doc_id, "quarterly_report.pdf", chunks, embeddings)
        assert store.count_by_document(doc_id) == 2

        stored = store._collection.get(
            where={"document_id": doc_id},
            include=["metadatas", "documents"],
        )
        assert set(stored["ids"]) == {f"{doc_id}:0", f"{doc_id}:1"}

        meta_by_id = dict(zip(stored["ids"], stored["metadatas"], strict=True))
        expected_meta_0 = {
            "document_id": doc_id,
            "filename": "quarterly_report.pdf",
            "page_number": 1,
            "page_end": 1,
            "page_numbers_csv": "1",
            "chunk_index": 0,
            "token_count": 15,
        }
        expected_meta_1 = {
            "document_id": doc_id,
            "filename": "quarterly_report.pdf",
            "page_number": 1,
            "page_end": 3,
            "page_numbers_csv": "1,2,3",
            "chunk_index": 1,
            "token_count": 42,
        }
        assert meta_by_id[f"{doc_id}:0"] == expected_meta_0
        assert meta_by_id[f"{doc_id}:1"] == expected_meta_1

        for meta in meta_by_id.values():
            for value in meta.values():
                assert isinstance(value, (str, int, float, bool))
                assert not isinstance(value, (list, dict, tuple, set))
    finally:
        store.close()


def test_vector_store_restart_persistence_and_idempotent_upsert(tmp_path: Path) -> None:
    """Verify persistence across close()+reopen and idempotent re-upsert without duplicates."""
    chroma_dir = tmp_path / "chroma_persist"
    doc_id = "22222222-2222-2222-2222-222222222222"

    store = ChromaVectorStore(persist_dir=chroma_dir)
    store.upsert_chunks(
        doc_id,
        "manual.pdf",
        [_make_chunk(0, text="v1 chunk 0"), _make_chunk(1, text="v1 chunk 1")],
        [_basis_embedding(0), _basis_embedding(1)],
    )
    assert store.count_by_document(doc_id) == 2
    store.close()
    assert store.health_check() is False

    # Reopen from same persistent directory.
    reopened = ChromaVectorStore(persist_dir=chroma_dir)
    try:
        assert reopened.health_check() is True
        assert reopened.count_by_document(doc_id) == 2

        # Re-upsert deterministic IDs with updated content -> count stays 2.
        reopened.upsert_chunks(
            doc_id,
            "manual_v2.pdf",
            [_make_chunk(0, text="v2 chunk 0"), _make_chunk(1, text="v2 chunk 1")],
            [_basis_embedding(2), _basis_embedding(3)],
        )
        assert reopened.count_by_document(doc_id) == 2

        stored = reopened._collection.get(
            ids=[f"{doc_id}:0", f"{doc_id}:1"],
            include=["documents", "metadatas"],
        )
        docs_by_id = dict(zip(stored["ids"], stored["documents"], strict=True))
        metas_by_id = dict(zip(stored["ids"], stored["metadatas"], strict=True))
        assert docs_by_id[f"{doc_id}:0"] == "v2 chunk 0"
        assert docs_by_id[f"{doc_id}:1"] == "v2 chunk 1"
        assert metas_by_id[f"{doc_id}:0"]["filename"] == "manual_v2.pdf"
    finally:
        reopened.close()


def test_vector_store_upsert_guards_and_error_paths(tmp_path: Path) -> None:
    """Verify empty no-op, length mismatches, bad inputs, and post-close guards."""
    store = ChromaVectorStore(persist_dir=tmp_path / "chroma_guards")
    doc_id = "33333333-3333-3333-3333-333333333333"

    try:
        # Empty chunks & embeddings is a clean no-op.
        store.upsert_chunks(doc_id, "empty.pdf", [], [])
        assert store.count_by_document(doc_id) == 0

        # Length mismatches raise VectorStoreError.
        with pytest.raises(VectorStoreError, match="does not match"):
            store.upsert_chunks(doc_id, "bad.pdf", [_make_chunk(0)], [])

        with pytest.raises(VectorStoreError, match="does not match"):
            store.upsert_chunks(doc_id, "bad.pdf", [], [_basis_embedding(0)])

        with pytest.raises(VectorStoreError, match="does not match"):
            store.upsert_chunks(
                doc_id,
                "bad.pdf",
                [_make_chunk(0), _make_chunk(1)],
                [_basis_embedding(0)],
            )

        # Duplicate chunk_index in a single call raises VectorStoreError.
        with pytest.raises(VectorStoreError, match="Duplicate chunk_index"):
            store.upsert_chunks(
                doc_id,
                "dup.pdf",
                [_make_chunk(0), _make_chunk(0)],
                [_basis_embedding(0), _basis_embedding(1)],
            )

        # Empty embedding vector or empty document_id raises VectorStoreError.
        with pytest.raises(VectorStoreError, match="non-empty"):
            store.upsert_chunks(doc_id, "bad_vec.pdf", [_make_chunk(0)], [[]])

        with pytest.raises(VectorStoreError, match="document_id"):
            store.upsert_chunks("   ", "bad_id.pdf", [], [])
    finally:
        store.close()

    with pytest.raises(VectorStoreError, match="closed"):
        store.upsert_chunks(doc_id, "closed.pdf", [], [])
    with pytest.raises(VectorStoreError, match="closed"):
        store.count_by_document(doc_id)
    with pytest.raises(VectorStoreError, match="closed"):
        store.delete_document(doc_id)


def test_vector_store_isolated_delete_and_missing_document_safety(tmp_path: Path) -> None:
    """Verify delete_document isolates target doc and safely ignores missing IDs."""
    store = ChromaVectorStore(persist_dir=tmp_path / "chroma_delete")
    try:
        doc_a = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        doc_b = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"

        store.upsert_chunks(
            doc_a,
            "a.pdf",
            [_make_chunk(0), _make_chunk(1)],
            [_basis_embedding(0), _basis_embedding(1)],
        )
        store.upsert_chunks(
            doc_b,
            "b.pdf",
            [_make_chunk(0), _make_chunk(1), _make_chunk(2)],
            [_basis_embedding(2), _basis_embedding(3), _basis_embedding(4)],
        )

        # Deleting a non-existent document is a safe no-op.
        store.delete_document("99999999-9999-9999-9999-999999999999")
        assert store.count_by_document(doc_a) == 2
        assert store.count_by_document(doc_b) == 3

        # Deleting doc_a leaves doc_b intact.
        store.delete_document(doc_a)
        assert store.count_by_document(doc_a) == 0
        assert store.count_by_document(doc_b) == 3

        # Repeated delete of doc_a remains safe.
        store.delete_document(doc_a)
        assert store.count_by_document(doc_b) == 3
    finally:
        store.close()


def test_vector_store_concurrent_upsert_and_delete_serialized_by_lock(
    tmp_path: Path,
) -> None:
    """Verify multi-threaded upserts/deletes serialize cleanly under chroma_io_lock."""
    store = ChromaVectorStore(persist_dir=tmp_path / "chroma_concurrent")
    try:
        active_ops = 0
        max_concurrent_ops = 0
        tracker_lock = threading.Lock()
        real_collection = store._collection
        assert real_collection is not None

        class TrackingCollectionProxy:
            def __getattr__(self, name: str) -> Any:
                return getattr(real_collection, name)

            def _track(self, fn: Any, *args: Any, **kwargs: Any) -> Any:
                nonlocal active_ops, max_concurrent_ops
                with tracker_lock:
                    active_ops += 1
                    max_concurrent_ops = max(max_concurrent_ops, active_ops)
                try:
                    time.sleep(0.002)
                    return fn(*args, **kwargs)
                finally:
                    with tracker_lock:
                        active_ops -= 1

            def upsert(self, *args: Any, **kwargs: Any) -> Any:
                return self._track(real_collection.upsert, *args, **kwargs)

            def delete(self, *args: Any, **kwargs: Any) -> Any:
                return self._track(real_collection.delete, *args, **kwargs)

            def get(self, *args: Any, **kwargs: Any) -> Any:
                return self._track(real_collection.get, *args, **kwargs)

        store._collection = TrackingCollectionProxy()

        def worker(idx: int) -> None:
            doc_id = f"doc-{idx:02d}"
            chunks = [_make_chunk(c, text=f"{doc_id}:{c}") for c in range(3)]
            embeddings = [_basis_embedding(c) for c in range(3)]
            store.upsert_chunks(doc_id, f"{doc_id}.pdf", chunks, embeddings)
            if idx % 2 == 1:
                store.delete_document(doc_id)
            else:
                assert store.count_by_document(doc_id) == 3

        with ThreadPoolExecutor(max_workers=8) as executor:
            list(executor.map(worker, range(12)))

        assert max_concurrent_ops == 1
        for idx in range(12):
            expected = 0 if idx % 2 == 1 else 3
            assert store.count_by_document(f"doc-{idx:02d}") == expected
    finally:
        store.close()


def test_vector_store_health_check_idempotent_close_and_windows_rmtree(
    tmp_path: Path,
) -> None:
    """Verify health_check open/error/closed, idempotent close, and immediate rmtree."""
    chroma_dir_a = tmp_path / "chroma_ntfs_a"
    chroma_dir_b = tmp_path / "chroma_ntfs_b"

    store_a = ChromaVectorStore(persist_dir=chroma_dir_a)
    store_b = ChromaVectorStore(persist_dir=chroma_dir_b)
    try:
        assert store_a.health_check() is True
        assert store_b.health_check() is True

        store_a.upsert_chunks(
            "doc-ntfs",
            "ntfs.pdf",
            [_make_chunk(0)],
            [_basis_embedding(0)],
        )
        store_b.upsert_chunks(
            "doc-other",
            "other.pdf",
            [_make_chunk(0)],
            [_basis_embedding(1)],
        )

        # Simulate transient collection/client error -> health_check returns False cleanly.
        orig_client = store_a._client

        class BrokenClient:
            def heartbeat(self) -> int:
                raise RuntimeError("client unavailable")

            def close(self) -> None:
                return None

        store_a._client = BrokenClient()
        assert store_a.health_check() is False
        store_a._client = orig_client
        assert store_a.health_check() is True

        # Idempotent close() releases Windows NTFS handles cleanly without affecting store_b.
        store_a.close()
        store_a.close()
        assert store_a.health_check() is False

        shutil.rmtree(chroma_dir_a)
        assert not chroma_dir_a.exists()

        # Store B at an independent directory remains healthy after Store A closes.
        assert store_b.health_check() is True
        assert store_b.count_by_document("doc-other") == 1
    finally:
        store_b.close()

    shutil.rmtree(chroma_dir_b)
    assert not chroma_dir_b.exists()


def test_vector_store_context_manager_releases_handles(tmp_path: Path) -> None:
    """Verify context manager closes store on exit allowing immediate directory removal."""
    chroma_dir = tmp_path / "chroma_ctx"
    with ChromaVectorStore(persist_dir=chroma_dir) as store:
        assert store.health_check() is True
        store.upsert_chunks(
            "doc-ctx",
            "ctx.pdf",
            [_make_chunk(0)],
            [_basis_embedding(0)],
        )
        assert store.count_by_document("doc-ctx") == 1

    assert store.health_check() is False
    shutil.rmtree(chroma_dir)
    assert not chroma_dir.exists()
