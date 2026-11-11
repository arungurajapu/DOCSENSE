"""Unit tests for CPU-only SentenceTransformer EmbeddingService."""

from __future__ import annotations

import asyncio
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest

from app.core.config import Settings
from app.core.exceptions import EmbeddingError
from app.services import embeddings as embeddings_module
from app.services.embeddings import (
    EmbeddingService,
    get_embedding_service,
    reset_embedding_service,
)
from app.services.interfaces import EmbeddingService as EmbeddingServiceProtocol


def _vector_norm(vec: list[float]) -> float:
    return math.sqrt(sum(x * x for x in vec))


def test_embedding_service_implements_protocol_and_lazy_loads_cpu_model() -> None:
    """Verify protocol compliance, lazy init, CPU-only (N, 384) normalized output, and ['']."""
    service = EmbeddingService()
    assert isinstance(service, EmbeddingServiceProtocol)
    assert service.device == "cpu"
    assert service.is_loaded() is False

    # Empty input returns [] without loading model weights.
    assert service.embed_texts([]) == []
    assert service.is_loaded() is False

    # Non-empty batch triggers lazy load and outputs normalized native Python list[list[float]].
    vectors = service.embed_texts(["DocSense ingests PDF files.", "ChromaDB stores vectors."])
    assert service.is_loaded() is True
    assert isinstance(vectors, list)
    assert len(vectors) == 2

    for vec in vectors:
        assert isinstance(vec, list)
        assert len(vec) == 384
        assert all(type(val) is float for val in vec)
        assert _vector_norm(vec) == pytest.approx(1.0, rel=1e-5, abs=1e-5)

    # List containing an empty string produces 1 normalized 384-d vector.
    empty_str_vectors = service.embed_texts([""])
    assert len(empty_str_vectors) == 1
    assert len(empty_str_vectors[0]) == 384
    assert _vector_norm(empty_str_vectors[0]) == pytest.approx(1.0, rel=1e-5, abs=1e-5)


def test_embedding_service_eager_load_and_settings_override(
    monkeypatch: pytest.MonkeyPatch,
    test_settings: Settings,
) -> None:
    """Verify eager_load=True loads immediately using configured model name and device='cpu'."""
    captured_calls: list[tuple[str, str | None]] = []

    class FakeModel:
        def encode(self, texts: list[str], **_kwargs: Any) -> list[list[float]]:
            return [[1.0] + [0.0] * 383 for _ in texts]

    def fake_st(model_name: str, *, device: str | None = None, **_kwargs: Any) -> FakeModel:
        captured_calls.append((model_name, device))
        return FakeModel()

    monkeypatch.setattr(embeddings_module, "SentenceTransformer", fake_st)

    custom_settings = test_settings.model_copy(update={"EMBEDDING_MODEL": "custom/test-minilm"})
    service = EmbeddingService(settings=custom_settings, eager_load=True)

    assert service.is_loaded() is True
    assert captured_calls == [("custom/test-minilm", "cpu")]
    assert len(service.embed_texts(["hello"])[0]) == 384


def test_embedding_service_concurrent_initialization_is_thread_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify concurrent threads load SentenceTransformer at most once and report is_loaded()."""
    load_count = 0
    count_lock = threading.Lock()
    barrier = threading.Barrier(8)

    class FakeModel:
        def encode(self, texts: list[str], **_kwargs: Any) -> list[list[float]]:
            return [[0.5] + [0.0] * 383 for _ in texts]

    def slow_st(*_args: Any, **_kwargs: Any) -> FakeModel:
        nonlocal load_count
        time.sleep(0.03)
        with count_lock:
            load_count += 1
        return FakeModel()

    monkeypatch.setattr(embeddings_module, "SentenceTransformer", slow_st)

    service = EmbeddingService()
    assert service.is_loaded() is False

    def worker(idx: int) -> tuple[list[list[float]], bool]:
        barrier.wait(timeout=5.0)
        vecs = service.embed_texts([f"chunk-{idx}"])
        return vecs, service.is_loaded()

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(worker, range(8)))

    assert load_count == 1
    assert service.is_loaded() is True
    for vecs, loaded_flag in results:
        assert loaded_flag is True
        assert len(vecs) == 1
        assert len(vecs[0]) == 384


def test_embedding_service_local_files_only_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify offline fallback retries with local_files_only=True when initial load fails."""
    calls: list[dict[str, Any]] = []

    class FakeModel:
        def encode(self, texts: list[str], **_kwargs: Any) -> list[list[float]]:
            return [[1.0] + [0.0] * 383 for _ in texts]

    def flaky_st(
        model_name: str,
        *,
        device: str | None = None,
        local_files_only: bool = False,
    ) -> FakeModel:
        calls.append(
            {
                "model_name": model_name,
                "device": device,
                "local_files_only": local_files_only,
            }
        )
        if not local_files_only:
            raise OSError("HuggingFace Hub unreachable offline")
        return FakeModel()

    monkeypatch.setattr(embeddings_module, "SentenceTransformer", flaky_st)

    service = EmbeddingService()
    vectors = service.embed_texts(["offline cached embedding"])

    assert service.is_loaded() is True
    assert len(vectors) == 1
    assert len(vectors[0]) == 384
    assert calls == [
        {
            "model_name": "sentence-transformers/all-MiniLM-L6-v2",
            "device": "cpu",
            "local_files_only": False,
        },
        {
            "model_name": "sentence-transformers/all-MiniLM-L6-v2",
            "device": "cpu",
            "local_files_only": True,
        },
    ]


def test_embedding_service_error_wrapping_and_input_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify load failures, encode runtime errors, and bad inputs raise EmbeddingError."""
    with pytest.raises(EmbeddingError):
        EmbeddingService(model_name="   ")

    def failing_st(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("corrupt checkpoint")

    monkeypatch.setattr(embeddings_module, "SentenceTransformer", failing_st)
    broken_load_service = EmbeddingService()
    with pytest.raises(EmbeddingError, match="Failed to load embedding model"):
        broken_load_service.embed_texts(["test"])
    assert broken_load_service.is_loaded() is False

    class BrokenEncodeModel:
        def encode(self, _texts: list[str], **_kwargs: Any) -> Any:
            raise RuntimeError("inference failure")

    monkeypatch.setattr(
        embeddings_module,
        "SentenceTransformer",
        lambda *_a, **_kw: BrokenEncodeModel(),
    )
    service = EmbeddingService()
    with pytest.raises(EmbeddingError, match="Failed to generate embeddings"):
        service.embed_texts(["trigger encode failure"])

    with pytest.raises(EmbeddingError):
        service.embed_texts("not-a-sequence-of-texts")  # type: ignore[arg-type]

    with pytest.raises(EmbeddingError):
        service.embed_texts(["ok", 123])  # type: ignore[list-item]


def test_embedding_service_async_uses_to_thread_without_blocking_event_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify embed_texts_async runs in a worker thread while other async tasks progress."""
    loop_thread_id: int | None = None
    worker_thread_id: int | None = None

    class SlowFakeModel:
        def encode(self, texts: list[str], **_kwargs: Any) -> list[list[float]]:
            nonlocal worker_thread_id
            worker_thread_id = threading.get_ident()
            time.sleep(0.08)
            return [[1.0] + [0.0] * 383 for _ in texts]

    monkeypatch.setattr(
        embeddings_module,
        "SentenceTransformer",
        lambda *_a, **_kw: SlowFakeModel(),
    )

    service = EmbeddingService()

    async def scenario() -> tuple[list[list[float]], list[list[float]], int]:
        nonlocal loop_thread_id
        loop_thread_id = threading.get_ident()

        empty_res = await service.embed_texts_async([])
        assert empty_res == []

        ticks = 0

        async def ticker(stop_event: asyncio.Event) -> None:
            nonlocal ticks
            while not stop_event.is_set():
                ticks += 1
                await asyncio.sleep(0.01)

        stop_event = asyncio.Event()
        tick_task = asyncio.create_task(ticker(stop_event))
        try:
            vectors = await service.embed_texts_async(["async non-blocking chunk"])
        finally:
            stop_event.set()
            await tick_task

        return empty_res, vectors, ticks

    empty_res, vectors, ticks = asyncio.run(scenario())

    assert empty_res == []
    assert len(vectors) == 1
    assert len(vectors[0]) == 384
    assert loop_thread_id is not None
    assert worker_thread_id is not None
    assert worker_thread_id != loop_thread_id
    assert ticks >= 2


def test_embedding_service_async_real_model_roundtrip() -> None:
    """Verify embed_texts_async returns real (N, 384) normalized vectors end-to-end."""
    service = EmbeddingService()
    vectors = asyncio.run(
        service.embed_texts_async(["DocSense async vector", "Second sentence"])
    )
    assert service.is_loaded() is True
    assert len(vectors) == 2
    assert all(len(vec) == 384 for vec in vectors)
    assert all(_vector_norm(vec) == pytest.approx(1.0, rel=1e-5, abs=1e-5) for vec in vectors)


def test_get_embedding_service_singleton_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify get_embedding_service returns a thread-safe singleton and resets cleanly."""
    reset_embedding_service()
    monkeypatch.setattr(
        embeddings_module,
        "SentenceTransformer",
        lambda *_a, **_kw: object(),
    )
    try:
        with ThreadPoolExecutor(max_workers=6) as executor:
            instances = list(
                executor.map(lambda _i: get_embedding_service(eager_load=True), range(6))
            )
        first = instances[0]
        assert all(inst is first for inst in instances)
        assert first.is_loaded() is True
    finally:
        reset_embedding_service()
