"""Thread-safe persistent ChromaDB vector store implementation."""

from __future__ import annotations

import logging
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import chromadb

from app.core.config import Settings
from app.core.config import settings as app_settings
from app.core.exceptions import VectorStoreError
from app.services.interfaces import TextChunk

logger = logging.getLogger(__name__)

_DEFAULT_MAX_BATCH_SIZE = 5000


def _validate_document_id(document_id: str) -> str:
    """Normalize and validate a non-empty document identifier."""
    doc_id = str(document_id).strip()
    if not doc_id:
        raise VectorStoreError("document_id must not be empty.")
    return doc_id


class ChromaVectorStore:
    """Persistent ChromaDB vector repository implementing ``interfaces.VectorStore``.

    Serializes all ChromaDB client and collection operations behind an internal
    :class:`threading.Lock` (``chroma_io_lock``) for safe multi-threaded access
    within a single Uvicorn worker process, and cleanly releases embedded SQLite/HNSW
    file handles on :meth:`close`.
    """

    def __init__(
        self,
        persist_dir: str | Path | None = None,
        collection_name: str | None = None,
        *,
        settings: Settings | None = None,
    ) -> None:
        cfg = settings or app_settings
        resolved_dir = Path(persist_dir if persist_dir is not None else cfg.CHROMA_PERSIST_DIR)
        resolved_collection = (
            collection_name if collection_name is not None else cfg.CHROMA_COLLECTION_NAME
        )
        if not resolved_collection or not str(resolved_collection).strip():
            raise VectorStoreError("collection_name must not be empty.")

        self._persist_dir = resolved_dir
        self._collection_name = str(resolved_collection).strip()
        self.chroma_io_lock = threading.Lock()
        self._closed = False
        self._client: Any | None = None
        self._collection: Any | None = None

        try:
            self._persist_dir.mkdir(parents=True, exist_ok=True)
            self._client = chromadb.PersistentClient(path=str(self._persist_dir))
            self._collection = self._client.get_or_create_collection(
                name=self._collection_name,
                metadata={"hnsw:space": "cosine"},
            )
        except Exception as exc:
            self._cleanup_after_failed_init()
            raise VectorStoreError(
                f"Failed to initialize ChromaDB PersistentClient at '{self._persist_dir}': {exc}"
            ) from exc

    @property
    def persist_dir(self) -> Path:
        """Filesystem directory backing the persistent ChromaDB store."""
        return self._persist_dir

    @property
    def collection_name(self) -> str:
        """ChromaDB collection name used for document chunk vectors."""
        return self._collection_name

    def __enter__(self) -> ChromaVectorStore:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    def _cleanup_after_failed_init(self) -> None:
        """Best-effort release if client initialization fails mid-setup."""
        client = self._client
        self._collection = None
        self._client = None
        self._closed = True
        if client is not None:
            self._release_client_handles(client)

    def _require_open_collection(self) -> Any:
        """Return active collection handle; caller must hold ``self.chroma_io_lock``."""
        if self._closed or self._client is None or self._collection is None:
            raise VectorStoreError("ChromaVectorStore is closed.")
        return self._collection

    def _resolve_max_batch_size(self) -> int:
        """Return safe upsert batch size supported by the active ChromaDB client."""
        get_max_batch = getattr(self._client, "get_max_batch_size", None)
        if callable(get_max_batch):
            try:
                return max(1, int(get_max_batch()))
            except Exception:
                return _DEFAULT_MAX_BATCH_SIZE
        return _DEFAULT_MAX_BATCH_SIZE

    def upsert_chunks(
        self,
        document_id: str,
        filename: str,
        chunks: Sequence[TextChunk],
        embeddings: Sequence[Sequence[float]],
    ) -> None:
        """Upsert document chunks idempotently under ``"{document_id}:{chunk_index}"``."""
        doc_id = _validate_document_id(document_id)
        chunk_list = list(chunks)
        embedding_list = list(embeddings)

        if len(chunk_list) != len(embedding_list):
            raise VectorStoreError(
                f"Chunk count ({len(chunk_list)}) does not match "
                f"embedding count ({len(embedding_list)})."
            )

        if not chunk_list:
            with self.chroma_io_lock:
                self._require_open_collection()
            return

        ids: list[str] = []
        documents: list[str] = []
        metadatas: list[dict[str, str | int]] = []
        vectors: list[list[float]] = []

        filename_str = str(filename)
        for chunk, embedding in zip(chunk_list, embedding_list, strict=True):
            vector = [float(value) for value in embedding]
            if not vector:
                raise VectorStoreError("Embedding vectors must be non-empty.")

            chunk_index = int(chunk.chunk_index)
            ids.append(f"{doc_id}:{chunk_index}")
            documents.append(str(chunk.text))
            metadatas.append(
                {
                    "document_id": doc_id,
                    "filename": filename_str,
                    "page_number": int(chunk.page_number),
                    "page_end": int(chunk.page_end),
                    "page_numbers_csv": str(chunk.page_numbers_csv),
                    "chunk_index": chunk_index,
                    "token_count": int(chunk.token_count),
                }
            )
            vectors.append(vector)

        if len(set(ids)) != len(ids):
            raise VectorStoreError(
                f"Duplicate chunk_index detected while upserting document '{doc_id}'."
            )

        with self.chroma_io_lock:
            collection = self._require_open_collection()
            max_batch = self._resolve_max_batch_size()
            try:
                for start in range(0, len(ids), max_batch):
                    end = start + max_batch
                    collection.upsert(
                        ids=ids[start:end],
                        embeddings=vectors[start:end],
                        documents=documents[start:end],
                        metadatas=metadatas[start:end],
                    )
            except Exception as exc:
                raise VectorStoreError(
                    f"Failed to upsert chunks for document '{doc_id}': {exc}"
                ) from exc

    def delete_document(self, document_id: str) -> None:
        """Delete all vectors belonging to ``document_id`` cleanly."""
        doc_id = _validate_document_id(document_id)
        with self.chroma_io_lock:
            collection = self._require_open_collection()
            try:
                collection.delete(where={"document_id": doc_id})
            except Exception as exc:
                raise VectorStoreError(
                    f"Failed to delete vectors for document '{doc_id}': {exc}"
                ) from exc

    def count_by_document(self, document_id: str) -> int:
        """Return the number of stored vectors for ``document_id``."""
        doc_id = _validate_document_id(document_id)
        with self.chroma_io_lock:
            collection = self._require_open_collection()
            try:
                result = collection.get(where={"document_id": doc_id}, include=[])
            except Exception as exc:
                raise VectorStoreError(
                    f"Failed to count vectors for document '{doc_id}': {exc}"
                ) from exc
            ids = result.get("ids") if isinstance(result, dict) else None
            return len(ids or [])

    def health_check(self) -> bool:
        """Return ``True`` when the ChromaDB client and collection respond normally."""
        with self.chroma_io_lock:
            if self._closed or self._client is None or self._collection is None:
                return False
            try:
                self._client.heartbeat()
                self._collection.count()
                return True
            except Exception as exc:
                logger.warning("ChromaDB health check failed: %s", exc)
                return False

    @staticmethod
    def _release_client_handles(client: Any) -> None:
        """Release ChromaDB client/system resources and Windows file handles."""
        identifier = getattr(client, "_identifier", None)
        admin_client = getattr(client, "_admin_client", None)
        admin_identifier = (
            getattr(admin_client, "_identifier", None) if admin_client is not None else None
        )
        system = getattr(client, "_system", None)

        close_method = getattr(client, "close", None)
        used_close = False
        if callable(close_method):
            try:
                close_method()
                used_close = True
            except Exception as exc:
                logger.warning("Error calling ChromaDB client.close(): %s", exc)

        if not used_close and system is not None and hasattr(system, "stop"):
            try:
                system.stop()
            except Exception as exc:
                logger.warning("Error stopping ChromaDB system: %s", exc)

        # Remove only this client's identifier from SharedSystemClient cache if no refs remain
        # (preserves any independent ChromaVectorStore instances at other paths).
        shared_cls = getattr(getattr(chromadb, "api", None), "client", None)
        shared_system_cls = getattr(shared_cls, "SharedSystemClient", None)
        if shared_system_cls is None:
            return

        refcount_lock = getattr(shared_system_cls, "_refcount_lock", None)
        systems_map = getattr(shared_system_cls, "_identifier_to_system", None)
        refcount_map = getattr(shared_system_cls, "_identifier_to_refcount", None)

        def _evict_if_unreferenced(target_id: str | None) -> None:
            if not target_id or not isinstance(systems_map, dict):
                return
            remaining = refcount_map.get(target_id, 0) if isinstance(refcount_map, dict) else 0
            if not used_close or remaining <= 0:
                popped_system = systems_map.pop(target_id, None)
                if popped_system is not None and hasattr(popped_system, "stop") and not used_close:
                    try:
                        popped_system.stop()
                    except Exception:
                        pass
                if isinstance(refcount_map, dict):
                    refcount_map.pop(target_id, None)

        if refcount_lock is not None:
            with refcount_lock:
                _evict_if_unreferenced(admin_identifier)
                _evict_if_unreferenced(identifier)
        else:
            _evict_if_unreferenced(admin_identifier)
            _evict_if_unreferenced(identifier)

    def close(self) -> None:
        """Idempotently close ChromaDB and release SQLite/HNSW handles on Windows."""
        with self.chroma_io_lock:
            if self._closed:
                return
            self._closed = True
            client = self._client
            self._collection = None
            self._client = None
            if client is not None:
                self._release_client_handles(client)


__all__ = ["ChromaVectorStore"]
