"""Deterministic concurrency and shutdown race-condition integration tests."""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import select, update
from tests.integration.conftest import (
    DeterministicEmbeddingService,
    make_isolated_settings,
    make_text_pdf_bytes,
    poll_until_terminal_async,
)

from app.core.exceptions import ExtractionError
from app.main import create_app
from app.models.document import Document, DocumentChunk, DocumentStatus
from app.repositories.vector_store import ChromaVectorStore
from app.schemas import ErrorResponse
from app.services.chunker import TokenChunker
from app.services.extractor import PDFExtractor
from app.services.interfaces import ExtractedPage, TextChunk


def test_delete_waits_for_active_extraction_and_skips_downstream_stages(
    tmp_path: Path,
) -> None:
    """Safeguard #1: DELETE waits for active extraction without deadlock and skips downstream."""
    settings = make_isolated_settings(tmp_path)
    entered_extract = threading.Event()
    release_extract = threading.Event()
    downstream_calls: list[str] = []

    class BarrierExtractor:
        def __init__(self) -> None:
            self._delegate = PDFExtractor()

        def extract(self, file_path: str | Path) -> list[ExtractedPage]:
            entered_extract.set()
            assert release_extract.wait(timeout=5.0), "Timed out waiting to release extract"
            return self._delegate.extract(file_path)

    class SpyChunker:
        def __init__(self) -> None:
            self._delegate = TokenChunker(settings)

        def chunk(self, pages: Sequence[ExtractedPage], document_id: str) -> list[TextChunk]:
            downstream_calls.append("chunk")
            return self._delegate.chunk(pages, document_id)

    class SpyEmbeddings(DeterministicEmbeddingService):
        def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
            downstream_calls.append("embed")
            return super().embed_texts(texts)

    async def scenario() -> None:
        app = create_app(
            settings,
            eager_load_embeddings=True,
            extractor=BarrierExtractor(),
            chunker=SpyChunker(),
            embedding_service=SpyEmbeddings(loaded=True),
        )
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://testserver",
            ) as client:
                upload_resp = await client.post(
                    "/api/v1/documents",
                    files={
                        "file": (
                            "extract_barrier.pdf",
                            make_text_pdf_bytes(["Extraction barrier test text."]),
                            "application/pdf",
                        )
                    },
                )
                assert upload_resp.status_code == 202
                doc_id = upload_resp.json()["id"]
                pdf_path = Path(settings.UPLOAD_DIR) / f"{doc_id}.pdf"

                assert await asyncio.to_thread(entered_extract.wait, 5.0)
                assert pdf_path.is_file()

                # Fire concurrent DELETE while extraction holds the file.
                delete_task = asyncio.create_task(client.delete(f"/api/v1/documents/{doc_id}"))
                await asyncio.sleep(0.15)

                # DELETE must still be waiting on extraction_done; PDF & row must remain intact.
                assert not delete_task.done()
                assert pdf_path.is_file()
                with app.state.session_factory() as session:
                    doc = session.get(Document, doc_id)
                    assert doc is not None
                    assert doc.status == DocumentStatus.PROCESSING.value

                # Release extraction -> DELETE completes 204 and ingestion skips chunk/embed/commit.
                release_extract.set()
                del_resp = await asyncio.wait_for(delete_task, timeout=5.0)
                assert del_resp.status_code == 204

                await asyncio.sleep(0.05)
                assert not pdf_path.exists()
                assert app.state.vector_store.count_by_document(doc_id) == 0
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
                assert downstream_calls == []

    asyncio.run(scenario())


def test_delete_during_embedding_stage_before_vector_write_scenario_a(
    tmp_path: Path,
) -> None:
    """Scenario (a): DELETE during CPU/embedding returns 204 immediately and leaves 0 state."""
    settings = make_isolated_settings(tmp_path)
    entered_embed = threading.Event()
    release_embed = threading.Event()
    upsert_calls: list[str] = []

    class BarrierEmbeddings(DeterministicEmbeddingService):
        def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
            entered_embed.set()
            assert release_embed.wait(timeout=5.0), "Timed out waiting to release embed"
            return super().embed_texts(texts)

    async def scenario() -> None:
        store = ChromaVectorStore(settings=settings)
        orig_upsert = store.upsert_chunks

        def tracked_upsert(
            document_id: str,
            filename: str,
            chunks: Sequence[TextChunk],
            embeddings: Sequence[Sequence[float]],
        ) -> None:
            upsert_calls.append(document_id)
            orig_upsert(document_id, filename, chunks, embeddings)

        store.upsert_chunks = tracked_upsert  # type: ignore[method-assign]

        app = create_app(
            settings,
            eager_load_embeddings=True,
            embedding_service=BarrierEmbeddings(loaded=True),
            vector_store=store,
        )
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://testserver",
            ) as client:
                upload_resp = await client.post(
                    "/api/v1/documents",
                    files={
                        "file": (
                            "embed_barrier.pdf",
                            make_text_pdf_bytes(["Embedding stage cancellation test."]),
                            "application/pdf",
                        )
                    },
                )
                assert upload_resp.status_code == 202
                doc_id = upload_resp.json()["id"]
                pdf_path = Path(settings.UPLOAD_DIR) / f"{doc_id}.pdf"

                assert await asyncio.to_thread(entered_embed.wait, 5.0)

                # Extraction already finished -> DELETE must complete 204 immediately.
                del_resp = await asyncio.wait_for(
                    client.delete(f"/api/v1/documents/{doc_id}"),
                    timeout=2.0,
                )
                assert del_resp.status_code == 204
                assert not pdf_path.exists()

                release_embed.set()
                await asyncio.sleep(0.05)

                assert upsert_calls == []
                assert store.count_by_document(doc_id) == 0
                with app.state.session_factory() as session:
                    assert session.get(Document, doc_id) is None

    asyncio.run(scenario())


def test_delete_barrier_during_chroma_write_under_doc_lock_scenario_b(
    tmp_path: Path,
) -> None:
    """Scenario (b): DELETE during Chroma write sets cancel_event; ingestion rolls back vectors."""
    settings = make_isolated_settings(tmp_path)
    after_upsert_entered = threading.Event()
    after_upsert_release = threading.Event()
    complete_calls: list[str] = []

    async def scenario() -> None:
        store = ChromaVectorStore(settings=settings)
        orig_upsert = store.upsert_chunks

        def barrier_upsert(
            document_id: str,
            filename: str,
            chunks: Sequence[TextChunk],
            embeddings: Sequence[Sequence[float]],
        ) -> None:
            orig_upsert(document_id, filename, chunks, embeddings)
            after_upsert_entered.set()
            assert after_upsert_release.wait(timeout=5.0)

        store.upsert_chunks = barrier_upsert  # type: ignore[method-assign]

        app = create_app(
            settings,
            eager_load_embeddings=True,
            embedding_service=DeterministicEmbeddingService(loaded=True),
            vector_store=store,
        )
        async with app.router.lifespan_context(app):
            orig_complete = app.state.document_repo.complete_ingestion

            def spy_complete(*args: Any, **kwargs: Any) -> Any:
                complete_calls.append(str(args[0] if args else kwargs.get("document_id")))
                return orig_complete(*args, **kwargs)

            app.state.document_repo.complete_ingestion = spy_complete

            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://testserver",
            ) as client:
                upload_resp = await client.post(
                    "/api/v1/documents",
                    files={
                        "file": (
                            "chroma_barrier.pdf",
                            make_text_pdf_bytes(["Chroma write lock barrier test."]),
                            "application/pdf",
                        )
                    },
                )
                assert upload_resp.status_code == 202
                doc_id = upload_resp.json()["id"]

                assert await asyncio.to_thread(after_upsert_entered.wait, 5.0)
                assert store.count_by_document(doc_id) >= 1

                # Launch concurrent DELETE while run_ingestion holds doc_lock inside upsert_chunks.
                delete_task = asyncio.create_task(client.delete(f"/api/v1/documents/{doc_id}"))
                await asyncio.sleep(0.05)
                assert not delete_task.done()

                after_upsert_release.set()
                del_resp = await asyncio.wait_for(delete_task, timeout=5.0)
                assert del_resp.status_code == 204

                assert complete_calls == []
                assert store.count_by_document(doc_id) == 0
                assert not (Path(settings.UPLOAD_DIR) / f"{doc_id}.pdf").exists()
                with app.state.session_factory() as session:
                    assert session.get(Document, doc_id) is None

    asyncio.run(scenario())


def test_conditional_rowcount_rollback_and_compensating_chroma_delete_scenario_c(
    tmp_path: Path,
) -> None:
    """Scenario (c): rowcount != 1 rolls back SQLite and triggers Chroma compensating delete."""
    settings = make_isolated_settings(tmp_path)

    async def scenario() -> None:
        app = create_app(
            settings,
            eager_load_embeddings=True,
            embedding_service=DeterministicEmbeddingService(loaded=True),
        )
        async with app.router.lifespan_context(app):
            repo = app.state.document_repo
            store = app.state.vector_store
            orig_complete = repo.complete_ingestion

            # Mutate status to failed right before complete_ingestion runs its conditional UPDATE.
            def race_complete(
                document_id: str,
                page_count: int,
                chunks: Sequence[TextChunk],
            ) -> Any:
                with app.state.session_factory() as session:
                    session.execute(
                        update(Document)
                        .where(Document.id == document_id)
                        .values(status=DocumentStatus.FAILED.value)
                    )
                    session.commit()
                return orig_complete(document_id, page_count, chunks)

            repo.complete_ingestion = race_complete

            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://testserver",
            ) as client:
                upload_resp = await client.post(
                    "/api/v1/documents",
                    files={
                        "file": (
                            "rowcount_race.pdf",
                            make_text_pdf_bytes(["Rowcount conditional rollback check."]),
                            "application/pdf",
                        )
                    },
                )
                assert upload_resp.status_code == 202
                doc_id = upload_resp.json()["id"]

                terminal = await poll_until_terminal_async(client, doc_id)
                assert terminal["status"] == DocumentStatus.FAILED.value
                assert terminal["chunk_count"] == 0

                # SQLite chunks must have been rolled back and Chroma vectors deleted.
                assert store.count_by_document(doc_id) == 0
                with app.state.session_factory() as session:
                    assert (
                        session.execute(
                            select(DocumentChunk).where(DocumentChunk.document_id == doc_id)
                        )
                        .scalars()
                        .all()
                        == []
                    )

    asyncio.run(scenario())


def test_concurrent_delete_and_delete_returns_204_and_404_scenario_d(
    tmp_path: Path,
) -> None:
    """Scenario (d): Concurrent DELETE + DELETE yields exactly 204 and 404 ErrorResponse."""
    settings = make_isolated_settings(tmp_path)

    async def scenario() -> None:
        store = ChromaVectorStore(settings=settings)
        orig_delete = store.delete_document
        first_entered = threading.Event()

        def slow_first_delete(document_id: str) -> None:
            if not first_entered.is_set():
                first_entered.set()
                time.sleep(0.05)
            orig_delete(document_id)

        store.delete_document = slow_first_delete  # type: ignore[method-assign]

        app = create_app(
            settings,
            eager_load_embeddings=True,
            embedding_service=DeterministicEmbeddingService(loaded=True),
            vector_store=store,
        )
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://testserver",
            ) as client:
                upload_resp = await client.post(
                    "/api/v1/documents",
                    files={
                        "file": (
                            "double_delete.pdf",
                            make_text_pdf_bytes(["Concurrent double delete check."]),
                            "application/pdf",
                        )
                    },
                )
                assert upload_resp.status_code == 202
                doc_id = upload_resp.json()["id"]
                terminal = await poll_until_terminal_async(client, doc_id)
                assert terminal["status"] == "ready"

                r1, r2 = await asyncio.gather(
                    client.delete(f"/api/v1/documents/{doc_id}"),
                    client.delete(f"/api/v1/documents/{doc_id}"),
                )
                by_status = {r1.status_code: r1, r2.status_code: r2}
                assert set(by_status.keys()) == {204, 404}
                err = ErrorResponse.model_validate(by_status[404].json())
                assert err.error.code == "DOCUMENT_NOT_FOUND"

    asyncio.run(scenario())


def test_concurrent_reprocess_and_reprocess_returns_202_and_409(
    tmp_path: Path,
) -> None:
    """Concurrent reprocess + reprocess on failed document returns 202 and 409 Conflict."""
    settings = make_isolated_settings(tmp_path)

    class FailOnceExtractor:
        def __init__(self) -> None:
            self.fail = True
            self._delegate = PDFExtractor()

        def extract(self, file_path: str | Path) -> list[ExtractedPage]:
            if self.fail:
                self.fail = False
                raise ExtractionError("Initial failure")
            return self._delegate.extract(file_path)

    async def scenario() -> None:
        app = create_app(
            settings,
            eager_load_embeddings=True,
            extractor=FailOnceExtractor(),
            embedding_service=DeterministicEmbeddingService(loaded=True),
        )
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://testserver",
            ) as client:
                upload_resp = await client.post(
                    "/api/v1/documents",
                    files={
                        "file": (
                            "reprocess_race.pdf",
                            make_text_pdf_bytes(["Concurrent reprocess test content."]),
                            "application/pdf",
                        )
                    },
                )
                assert upload_resp.status_code == 202
                doc_id = upload_resp.json()["id"]
                failed = await poll_until_terminal_async(client, doc_id)
                assert failed["status"] == "failed"

                orig_reset = app.state.document_repo.reset_failed_for_reprocess

                def delayed_reset(target_id: str) -> Any:
                    time.sleep(0.05)
                    return orig_reset(target_id)

                app.state.document_repo.reset_failed_for_reprocess = delayed_reset

                r1, r2 = await asyncio.gather(
                    client.post(f"/api/v1/documents/{doc_id}/reprocess"),
                    client.post(f"/api/v1/documents/{doc_id}/reprocess"),
                )
                by_status = {r1.status_code: r1, r2.status_code: r2}
                assert set(by_status.keys()) == {202, 409}
                err = ErrorResponse.model_validate(by_status[409].json())
                assert err.error.code == "DOCUMENT_CONFLICT"

                ready = await poll_until_terminal_async(client, doc_id)
                assert ready["status"] == "ready"
                assert app.state.vector_store.count_by_document(doc_id) == ready["chunk_count"]

    asyncio.run(scenario())


def test_concurrent_reprocess_and_delete_both_orderings(tmp_path: Path) -> None:
    """Verify both reprocess-first (202+204) and delete-first (204+404) leave zero leftovers."""

    async def run_case(case_dir: Path, *, delete_first: bool) -> None:
        settings = make_isolated_settings(case_dir)

        class FailOnceExtractor:
            def __init__(self) -> None:
                self.fail = True
                self._delegate = PDFExtractor()

            def extract(self, file_path: str | Path) -> list[ExtractedPage]:
                if self.fail:
                    self.fail = False
                    raise ExtractionError("Fail once")
                return self._delegate.extract(file_path)

        app = create_app(
            settings,
            eager_load_embeddings=True,
            extractor=FailOnceExtractor(),
            embedding_service=DeterministicEmbeddingService(loaded=True),
        )
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://testserver",
            ) as client:
                upload_resp = await client.post(
                    "/api/v1/documents",
                    files={
                        "file": (
                            "reprocess_delete.pdf",
                            make_text_pdf_bytes(["Reprocess vs delete race check."]),
                            "application/pdf",
                        )
                    },
                )
                assert upload_resp.status_code == 202
                doc_id = upload_resp.json()["id"]
                failed = await poll_until_terminal_async(client, doc_id)
                assert failed["status"] == "failed"

                entered = threading.Event()
                release = threading.Event()

                if delete_first:
                    orig_mark_deleting = app.state.document_repo.mark_deleting

                    def slow_mark_deleting(target_id: str) -> Any:
                        entered.set()
                        assert release.wait(timeout=5.0)
                        return orig_mark_deleting(target_id)

                    app.state.document_repo.mark_deleting = slow_mark_deleting

                    del_task = asyncio.create_task(client.delete(f"/api/v1/documents/{doc_id}"))
                    assert await asyncio.to_thread(entered.wait, 5.0)
                    rep_task = asyncio.create_task(
                        client.post(f"/api/v1/documents/{doc_id}/reprocess")
                    )
                    await asyncio.sleep(0.05)
                    release.set()

                    del_resp, rep_resp = await asyncio.gather(del_task, rep_task)
                    assert del_resp.status_code == 204
                    assert rep_resp.status_code == 404
                    assert ErrorResponse.model_validate(rep_resp.json()).error.code == (
                        "DOCUMENT_NOT_FOUND"
                    )
                else:
                    orig_reset = app.state.document_repo.reset_failed_for_reprocess

                    def slow_reset(target_id: str) -> Any:
                        entered.set()
                        assert release.wait(timeout=5.0)
                        return orig_reset(target_id)

                    app.state.document_repo.reset_failed_for_reprocess = slow_reset

                    rep_task = asyncio.create_task(
                        client.post(f"/api/v1/documents/{doc_id}/reprocess")
                    )
                    assert await asyncio.to_thread(entered.wait, 5.0)
                    del_task = asyncio.create_task(client.delete(f"/api/v1/documents/{doc_id}"))
                    await asyncio.sleep(0.05)
                    release.set()

                    rep_resp, del_resp = await asyncio.gather(rep_task, del_task)
                    assert rep_resp.status_code == 202
                    assert del_resp.status_code == 204

                await asyncio.sleep(0.05)
                assert not (Path(settings.UPLOAD_DIR) / f"{doc_id}.pdf").exists()
                assert app.state.vector_store.count_by_document(doc_id) == 0
                with app.state.session_factory() as session:
                    assert session.get(Document, doc_id) is None

    asyncio.run(run_case(tmp_path / "delete_first", delete_first=True))
    asyncio.run(run_case(tmp_path / "reprocess_first", delete_first=False))


def test_shutdown_drains_outstanding_to_thread_before_close_and_dispose(
    tmp_path: Path,
) -> None:
    """Safeguard #2: lifespan shutdown waits for offloaded worker threads before closing stores."""
    settings = make_isolated_settings(tmp_path)
    events: list[str] = []
    entered_extract = threading.Event()
    release_extract = threading.Event()

    class SlowExtractAfterCancel:
        def extract(self, file_path: str | Path) -> list[ExtractedPage]:
            del file_path
            entered_extract.set()
            assert release_extract.wait(timeout=5.0)
            events.append("worker_finished")
            return [ExtractedPage(page_number=1, text="finished after shutdown signal")]

    async def scenario() -> None:
        store = ChromaVectorStore(settings=settings)
        orig_close = store.close

        def tracked_close() -> None:
            events.append("vector_store_close")
            orig_close()

        store.close = tracked_close  # type: ignore[method-assign]

        app = create_app(
            settings,
            eager_load_embeddings=True,
            extractor=SlowExtractAfterCancel(),
            embedding_service=DeterministicEmbeddingService(loaded=True),
            vector_store=store,
        )

        ctx = app.router.lifespan_context(app)
        await ctx.__aenter__()

        orig_dispose = app.state.engine.dispose

        def tracked_dispose() -> None:
            events.append("engine_dispose")
            orig_dispose()

        app.state.engine.dispose = tracked_dispose

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            upload_resp = await client.post(
                "/api/v1/documents",
                files={
                    "file": (
                        "shutdown_drain.pdf",
                        make_text_pdf_bytes(["Shutdown drain check"]),
                        "application/pdf",
                    )
                },
            )
            assert upload_resp.status_code == 202
            assert await asyncio.to_thread(entered_extract.wait, 5.0)

        # Trigger lifespan exit while worker thread is still blocked in extractor.extract().
        def release_later() -> None:
            time.sleep(0.15)
            # Confirm close() and dispose() have not fired while worker thread is still running.
            assert "vector_store_close" not in events
            assert "engine_dispose" not in events
            release_extract.set()

        releaser = threading.Thread(target=release_later, daemon=True)
        releaser.start()
        try:
            await ctx.__aexit__(None, None, None)
        finally:
            releaser.join(timeout=5.0)

        assert events == ["worker_finished", "vector_store_close", "engine_dispose"]

    asyncio.run(scenario())


def test_shutdown_drains_outstanding_embed_texts_async_before_close_and_dispose(
    tmp_path: Path,
) -> None:
    """Lifespan shutdown also drains active embed_texts_async worker threads before teardown."""
    settings = make_isolated_settings(tmp_path)
    events: list[str] = []
    entered_embed = threading.Event()
    release_embed = threading.Event()

    class SlowEmbedAfterCancel(DeterministicEmbeddingService):
        def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
            entered_embed.set()
            assert release_embed.wait(timeout=5.0)
            events.append("embed_worker_finished")
            return super().embed_texts(texts)

    async def scenario() -> None:
        store = ChromaVectorStore(settings=settings)
        orig_close = store.close

        def tracked_close() -> None:
            events.append("vector_store_close")
            orig_close()

        store.close = tracked_close  # type: ignore[method-assign]

        app = create_app(
            settings,
            eager_load_embeddings=True,
            embedding_service=SlowEmbedAfterCancel(loaded=True),
            vector_store=store,
        )

        ctx = app.router.lifespan_context(app)
        await ctx.__aenter__()

        orig_dispose = app.state.engine.dispose

        def tracked_dispose() -> None:
            events.append("engine_dispose")
            orig_dispose()

        app.state.engine.dispose = tracked_dispose

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            upload_resp = await client.post(
                "/api/v1/documents",
                files={
                    "file": (
                        "shutdown_embed_drain.pdf",
                        make_text_pdf_bytes(["Shutdown embed drain check"]),
                        "application/pdf",
                    )
                },
            )
            assert upload_resp.status_code == 202
            assert await asyncio.to_thread(entered_embed.wait, 5.0)

        def release_later() -> None:
            time.sleep(0.15)
            assert "vector_store_close" not in events
            assert "engine_dispose" not in events
            release_embed.set()

        releaser = threading.Thread(target=release_later, daemon=True)
        releaser.start()
        try:
            await ctx.__aexit__(None, None, None)
        finally:
            releaser.join(timeout=5.0)

        assert events == ["embed_worker_finished", "vector_store_close", "engine_dispose"]

    asyncio.run(scenario())
