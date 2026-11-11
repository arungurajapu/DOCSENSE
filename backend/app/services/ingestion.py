"""Asynchronous PDF ingestion coordinator with single-process concurrency guards.

Concurrency boundary:
    DocSense Milestone 1 coordinates per-document ingestion, reprocessing,
    deletion, and lifecycle shutdown strictly **in-process** using
    ``WeakValueDictionary[str, asyncio.Lock]``, ``threading.Event`` cancellation
    flags, SQLite WAL mode, and embedded ChromaDB serialized behind its
    ``chroma_io_lock``. Deployment MUST run single-process
    (``uvicorn app.main:app --workers 1``).
"""

from __future__ import annotations

import asyncio
import logging
import threading
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, ParamSpec, TypeVar
from weakref import WeakValueDictionary

from fastapi import UploadFile

from app.core.exceptions import (
    DocSenseError,
    DocumentConflictError,
    DocumentNotFoundError,
    EmptyPDFError,
)
from app.models.document import DocumentStatus
from app.repositories.document_repo import DocumentRepository
from app.schemas import DocumentListResponse, DocumentResponse
from app.services.interfaces import (
    EmbeddingService,
    PDFExtractor,
    StorageService,
    TokenChunker,
    VectorStore,
)

logger = logging.getLogger(__name__)

_P = ParamSpec("_P")
_R = TypeVar("_R")

_INTERRUPTED_ERROR_MESSAGE = "Ingestion interrupted by server restart. Please retry reprocessing."


@dataclass
class _ActiveRun:
    """In-flight background ingestion state tracked per document."""

    task: asyncio.Task[None]
    cancel_event: threading.Event
    extraction_done: asyncio.Event


class IngestionService:
    """Coordinates upload validation, background ingestion, deletion, recovery, and shutdown."""

    def __init__(
        self,
        *,
        repo: DocumentRepository,
        storage: StorageService,
        extractor: PDFExtractor,
        chunker: TokenChunker,
        embeddings: EmbeddingService,
        vector_store: VectorStore,
    ) -> None:
        self._repo = repo
        self._storage = storage
        self._extractor = extractor
        self._chunker = chunker
        self._embeddings = embeddings
        self._vector_store = vector_store

        self._doc_locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()
        self._active_runs: dict[str, _ActiveRun] = {}
        self._active_tasks: set[asyncio.Task[None]] = set()
        self._inflight_events: set[asyncio.Event] = set()
        self._shutting_down = False

    def _get_doc_lock(self, document_id: str) -> asyncio.Lock:
        """Return per-document ``asyncio.Lock``; callers must keep a strong local reference."""
        lock = self._doc_locks.get(document_id)
        if lock is None:
            lock = asyncio.Lock()
            self._doc_locks[document_id] = lock
        return lock

    @staticmethod
    def _consume_background_exception(task: asyncio.Task[Any]) -> None:
        """Retrieve shielded background task exceptions to suppress unretrieved-task warnings."""
        if not task.cancelled():
            exc = task.exception()
            if exc is not None:
                logger.debug("Shielded background operation ended with %r", exc)

    async def _to_thread(
        self,
        func: Callable[_P, _R],
        *args: _P.args,
        **kwargs: _P.kwargs,
    ) -> _R:
        """Offload blocking sync work to a worker thread tracked until completion on shutdown."""
        loop = asyncio.get_running_loop()
        done = asyncio.Event()
        self._inflight_events.add(done)

        def _complete_event() -> None:
            done.set()
            self._inflight_events.discard(done)

        def _runner() -> _R:
            try:
                return func(*args, **kwargs)
            finally:
                try:
                    loop.call_soon_threadsafe(_complete_event)
                except RuntimeError:
                    pass

        coro = asyncio.to_thread(_runner)
        try:
            inner_task = asyncio.create_task(coro)
        except BaseException:
            coro.close()
            self._inflight_events.discard(done)
            raise
        inner_task.add_done_callback(self._consume_background_exception)
        return await asyncio.shield(inner_task)

    async def _track_async(self, awaitable: Awaitable[_R]) -> _R:
        """Track an external async operation (e.g. ``embed_texts_async``) across shutdown."""
        done = asyncio.Event()
        self._inflight_events.add(done)

        async def _runner() -> _R:
            try:
                return await awaitable
            finally:
                done.set()
                self._inflight_events.discard(done)

        coro = _runner()
        try:
            inner_task = asyncio.create_task(coro)
        except BaseException:
            coro.close()
            self._inflight_events.discard(done)
            raise
        inner_task.add_done_callback(self._consume_background_exception)
        return await asyncio.shield(inner_task)

    async def run_in_thread(
        self,
        func: Callable[_P, _R],
        *args: _P.args,
        **kwargs: _P.kwargs,
    ) -> _R:
        """Run a synchronous dependency call off the event loop with shutdown tracking."""
        return await self._to_thread(func, *args, **kwargs)

    async def upload_document(self, upload_file: UploadFile) -> DocumentResponse:
        """Validate/store an uploaded PDF, persist its DB row, and schedule ingestion."""
        doc_id = str(uuid.uuid4())
        saved = await self._storage.save_validated_pdf(upload_file, doc_id)

        try:
            doc = await self._to_thread(
                self._repo.create_document,
                document_id=doc_id,
                filename=saved.filename,
                content_type=saved.content_type,
                file_size_bytes=saved.file_size_bytes,
                sha256_hash=saved.sha256_hash,
                storage_path=saved.storage_path,
                page_count=saved.page_count,
            )
        except Exception:
            try:
                await self._to_thread(self._storage.delete_file, saved.storage_path)
            except Exception:
                logger.exception(
                    "Failed to delete orphan PDF '%s' after DB creation error for document %s",
                    saved.storage_path,
                    doc_id,
                )
            raise

        self.schedule_ingestion(doc.id)
        return DocumentResponse.model_validate(doc)

    async def get_document(self, document_id: str) -> DocumentResponse:
        """Fetch a single document by UUID or raise :class:`DocumentNotFoundError`."""
        doc = await self._to_thread(self._repo.get_document, document_id)
        if doc is None:
            raise DocumentNotFoundError(f"Document '{document_id}' not found.")
        return DocumentResponse.model_validate(doc)

    async def list_documents(
        self,
        *,
        status: DocumentStatus | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> DocumentListResponse:
        """Return paginated documents filtered optionally by lifecycle status."""
        docs, total = await self._to_thread(self._repo.list_documents, status, limit, offset)
        return DocumentListResponse(
            items=[DocumentResponse.model_validate(doc) for doc in docs],
            total=total,
            limit=limit,
            offset=offset,
        )

    def schedule_ingestion(self, document_id: str) -> asyncio.Task[None] | None:
        """Schedule background ingestion for a ``pending`` document."""
        if self._shutting_down:
            return None

        existing = self._active_runs.get(document_id)
        if existing is not None and not existing.task.done():
            return existing.task

        cancel_event = threading.Event()
        extraction_done = asyncio.Event()
        # CRITICAL SAFEGUARD #1: Initialize set(); clear ONLY around extractor.extract().
        extraction_done.set()

        task = asyncio.create_task(
            self._run_ingestion(document_id, cancel_event, extraction_done),
            name=f"ingest-{document_id}",
        )
        run = _ActiveRun(
            task=task,
            cancel_event=cancel_event,
            extraction_done=extraction_done,
        )
        self._active_runs[document_id] = run
        self._active_tasks.add(task)

        def _on_done(finished: asyncio.Task[None]) -> None:
            self._active_tasks.discard(finished)
            current = self._active_runs.get(document_id)
            if current is not None and current.task is finished:
                self._active_runs.pop(document_id, None)
            if not finished.cancelled():
                exc = finished.exception()
                if exc is not None:
                    logger.error(
                        "Unhandled background ingestion exception for document %s",
                        document_id,
                        exc_info=exc,
                    )

        task.add_done_callback(_on_done)
        return task

    async def _compensating_vector_delete(self, document_id: str) -> None:
        """Best-effort removal of ChromaDB vectors for ``document_id``."""
        try:
            await self._to_thread(self._vector_store.delete_document, document_id)
        except Exception:
            logger.exception(
                "Best-effort ChromaDB vector cleanup failed for document %s",
                document_id,
            )

    async def _handle_ingestion_failure(
        self,
        document_id: str,
        cancel_event: threading.Event,
        error_message: str,
    ) -> None:
        """Clean up partial vectors and mark ``processing -> failed`` unless canceled/stopping."""
        if cancel_event.is_set() or self._shutting_down:
            return

        doc_lock = self._get_doc_lock(document_id)
        async with doc_lock:
            if cancel_event.is_set() or self._shutting_down:
                return
            await self._compensating_vector_delete(document_id)
            await self._to_thread(self._repo.mark_failed, document_id, error_message)

    async def _run_ingestion(
        self,
        document_id: str,
        cancel_event: threading.Event,
        extraction_done: asyncio.Event,
    ) -> None:
        """Execute extract -> chunk -> embed -> locked vector write + SQLite commit."""
        try:
            if cancel_event.is_set() or self._shutting_down:
                return

            claimed = await self._to_thread(self._repo.claim_for_processing, document_id)
            if not claimed:
                return

            doc = await self._to_thread(self._repo.get_document, document_id)
            if (
                doc is None
                or doc.status != DocumentStatus.PROCESSING.value
                or cancel_event.is_set()
                or self._shutting_down
            ):
                return

            pdf_path = await self._to_thread(self._storage.resolve_path, doc.storage_path)
            if cancel_event.is_set() or self._shutting_down:
                return

            # CRITICAL SAFEGUARD #1: Never hold doc_lock while extraction_done is cleared.
            extraction_done.clear()
            try:
                pages = await self._to_thread(self._extractor.extract, pdf_path)
            finally:
                extraction_done.set()

            if cancel_event.is_set() or self._shutting_down:
                return

            chunks = await self._to_thread(self._chunker.chunk, pages, document_id)
            if not chunks:
                raise EmptyPDFError("PDF contains no chunkable text.")

            if cancel_event.is_set() or self._shutting_down:
                return

            texts = [chunk.text for chunk in chunks]
            vectors = await self._track_async(self._embeddings.embed_texts_async(texts))

            if cancel_event.is_set() or self._shutting_down:
                return

            doc_lock = self._get_doc_lock(document_id)
            async with doc_lock:
                if cancel_event.is_set() or self._shutting_down:
                    return

                current_doc = await self._to_thread(self._repo.get_document, document_id)
                if (
                    current_doc is None
                    or current_doc.status != DocumentStatus.PROCESSING.value
                ):
                    return

                await self._to_thread(
                    self._vector_store.upsert_chunks,
                    document_id,
                    current_doc.filename,
                    chunks,
                    vectors,
                )

                if cancel_event.is_set() or self._shutting_down:
                    await self._compensating_vector_delete(document_id)
                    return

                rechecked_doc = await self._to_thread(self._repo.get_document, document_id)
                if (
                    rechecked_doc is None
                    or rechecked_doc.status != DocumentStatus.PROCESSING.value
                ):
                    await self._compensating_vector_delete(document_id)
                    return

                page_count = (
                    current_doc.page_count
                    if current_doc.page_count is not None
                    else max(page.page_number for page in pages)
                )
                completed = await self._to_thread(
                    self._repo.complete_ingestion,
                    document_id,
                    page_count,
                    chunks,
                )
                if completed is None:
                    await self._compensating_vector_delete(document_id)

        except asyncio.CancelledError:
            raise
        except DocSenseError as exc:
            logger.warning(
                "Ingestion failed for document %s (%s): %s",
                document_id,
                exc.code,
                exc.message,
            )
            await self._handle_ingestion_failure(
                document_id,
                cancel_event,
                f"{exc.code}: {exc.message}",
            )
        except Exception as exc:
            logger.exception("Unexpected error during ingestion of document %s", document_id)
            detail = str(exc).strip()
            msg = (
                f"INTERNAL_ERROR: Unexpected ingestion failure ({type(exc).__name__}): {detail}"
                if detail
                else f"INTERNAL_ERROR: Unexpected ingestion failure ({type(exc).__name__})."
            )
            await self._handle_ingestion_failure(document_id, cancel_event, msg)

    async def delete_document(self, document_id: str) -> None:
        """Cancel any active ingestion, wait for PDF handle release, and delete all state."""
        active = self._active_runs.get(document_id)
        if active is not None:
            active.cancel_event.set()

        doc_lock = self._get_doc_lock(document_id)
        async with doc_lock:
            active = self._active_runs.get(document_id)
            if active is not None:
                active.cancel_event.set()
                await active.extraction_done.wait()

            doc = await self._to_thread(self._repo.mark_deleting, document_id)
            await self._to_thread(self._vector_store.delete_document, document_id)
            await self._to_thread(self._storage.delete_file, doc.storage_path)
            await self._to_thread(self._repo.delete_document_row, document_id)

    async def reprocess_document(self, document_id: str) -> DocumentResponse:
        """Reset a ``failed`` document to ``pending`` and schedule fresh ingestion."""
        doc_lock = self._get_doc_lock(document_id)
        async with doc_lock:
            doc = await self._to_thread(self._repo.get_document, document_id)
            if doc is None:
                raise DocumentNotFoundError(f"Document '{document_id}' not found.")
            if doc.status != DocumentStatus.FAILED.value:
                raise DocumentConflictError(
                    f"Document '{document_id}' cannot be reprocessed in status '{doc.status}'."
                )

            # Deadlock-free: mark_failed transitions processing -> failed inside doc_lock near
            # _run_ingestion exit, so once DB status is failed here any active prior.task has
            # already released doc_lock and is merely winding down.
            prior = self._active_runs.get(document_id)
            if prior is not None and not prior.task.done():
                await asyncio.gather(prior.task, return_exceptions=True)

            await self._to_thread(self._vector_store.delete_document, document_id)
            updated = await self._to_thread(
                self._repo.reset_failed_for_reprocess,
                document_id,
            )
            self.schedule_ingestion(document_id)
            return DocumentResponse.model_validate(updated)

    async def recover_incomplete_documents(self) -> None:
        """Reconcile pre-restart ``deleting``, ``processing``, and ``pending`` rows on startup."""
        incomplete = await self._to_thread(self._repo.list_incomplete_documents)

        for doc in incomplete[DocumentStatus.DELETING]:
            logger.info("Recovering interrupted deletion for document %s", doc.id)
            await self._to_thread(self._vector_store.delete_document, doc.id)
            await self._to_thread(self._storage.delete_file, doc.storage_path)
            await self._to_thread(self._repo.delete_document_row, doc.id)

        for doc in incomplete[DocumentStatus.PROCESSING]:
            logger.info("Marking interrupted processing document %s as failed", doc.id)
            await self._to_thread(self._vector_store.delete_document, doc.id)
            await self._to_thread(
                self._repo.mark_failed,
                doc.id,
                _INTERRUPTED_ERROR_MESSAGE,
            )

        for doc in incomplete[DocumentStatus.PENDING]:
            logger.info("Rescheduling pending document %s on startup", doc.id)
            self.schedule_ingestion(doc.id)

    async def shutdown(self) -> None:
        """Cancel active runs and drain all tracked worker-thread operations before teardown."""
        self._shutting_down = True

        for run in list(self._active_runs.values()):
            run.cancel_event.set()
            run.task.cancel()

        while True:
            pending_tasks = [task for task in self._active_tasks if not task.done()]
            if pending_tasks:
                await asyncio.gather(*pending_tasks, return_exceptions=True)

            pending_events = [event for event in self._inflight_events if not event.is_set()]
            if pending_events:
                await asyncio.gather(*(event.wait() for event in pending_events))

            await asyncio.sleep(0)
            if not any(not task.done() for task in self._active_tasks) and not any(
                not event.is_set() for event in self._inflight_events
            ):
                break

        self._inflight_events.clear()


__all__ = ["IngestionService"]
