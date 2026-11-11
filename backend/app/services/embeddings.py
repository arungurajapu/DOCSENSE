"""Thread-safe SentenceTransformer embedding service implementation."""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Sequence

from sentence_transformers import SentenceTransformer

from app.core.config import Settings
from app.core.config import settings as app_settings
from app.core.exceptions import EmbeddingError

logger = logging.getLogger(__name__)


class EmbeddingService:
    """Dense text embedding service backed by a CPU SentenceTransformer model.

    Implements :class:`app.services.interfaces.EmbeddingService` with thread-safe
    lazy or eager model initialization, local-cache fallback, native Python
    normalized vector output, and non-blocking async execution via
    :func:`asyncio.to_thread`.
    """

    def __init__(
        self,
        model_name: str | None = None,
        *,
        device: str = "cpu",
        eager_load: bool = False,
        settings: Settings | None = None,
    ) -> None:
        cfg = settings or app_settings
        resolved_model = model_name if model_name is not None else cfg.EMBEDDING_MODEL
        if not str(resolved_model).strip():
            raise EmbeddingError("model_name must not be empty.")
        self._model_name = str(resolved_model).strip()
        self._device = device
        self._model: SentenceTransformer | None = None
        self._load_lock = threading.Lock()

        if eager_load:
            self.load()

    @property
    def model_name(self) -> str:
        """Configured SentenceTransformer model identifier or local path."""
        return self._model_name

    @property
    def device(self) -> str:
        """Target inference device (CPU-only in DocSense MVP)."""
        return self._device

    def is_loaded(self) -> bool:
        """Return whether the embedding model weights are currently loaded."""
        return self._model is not None

    def load(self) -> None:
        """Eagerly load the embedding model weights in a thread-safe manner."""
        self._get_or_load_model()

    def _load_model(self) -> SentenceTransformer:
        """Load SentenceTransformer with local-cache fallback when offline."""
        try:
            return SentenceTransformer(self._model_name, device=self._device)
        except Exception as exc:
            logger.warning(
                "Primary load for embedding model '%s' failed (%s); "
                "retrying with local_files_only=True.",
                self._model_name,
                exc,
            )
            try:
                return SentenceTransformer(
                    self._model_name,
                    device=self._device,
                    local_files_only=True,
                )
            except TypeError:
                raise EmbeddingError(
                    f"Failed to load embedding model '{self._model_name}': {exc}"
                ) from exc
            except Exception as fallback_exc:
                raise EmbeddingError(
                    f"Failed to load embedding model '{self._model_name}': {fallback_exc}"
                ) from fallback_exc

    def _get_or_load_model(self) -> SentenceTransformer:
        """Return the loaded model, initializing it at most once across threads."""
        model = self._model
        if model is not None:
            return model

        with self._load_lock:
            if self._model is None:
                self._model = self._load_model()
            return self._model

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        """Encode ``texts`` into L2-normalized native Python ``list[list[float]]``."""
        if isinstance(texts, (str, bytes)):
            raise EmbeddingError(
                "texts must be a sequence of strings, not a single string or bytes object."
            )

        text_list = list(texts)
        if not text_list:
            return []

        if not all(isinstance(item, str) for item in text_list):
            raise EmbeddingError("All items in texts must be strings.")

        model = self._get_or_load_model()
        try:
            encoded = model.encode(
                text_list,
                normalize_embeddings=True,
                show_progress_bar=False,
            )
            raw_rows = encoded.tolist() if hasattr(encoded, "tolist") else list(encoded)
            if len(raw_rows) != len(text_list):
                raise EmbeddingError(
                    f"Expected {len(text_list)} embeddings, got {len(raw_rows)}."
                )

            vectors = [[float(value) for value in row] for row in raw_rows]
            if any(len(vec) == 0 for vec in vectors):
                raise EmbeddingError("Embedding model returned an empty vector.")
            return vectors
        except EmbeddingError:
            raise
        except Exception as exc:
            raise EmbeddingError(f"Failed to generate embeddings: {exc}") from exc

    async def embed_texts_async(self, texts: Sequence[str]) -> list[list[float]]:
        """Encode ``texts`` asynchronously without blocking the asyncio event loop."""
        return await asyncio.to_thread(self.embed_texts, texts)


_singleton_lock = threading.Lock()
_default_embedding_service: EmbeddingService | None = None


def get_embedding_service(
    *,
    settings: Settings | None = None,
    eager_load: bool = False,
) -> EmbeddingService:
    """Return a process-wide thread-safe singleton :class:`EmbeddingService`."""
    global _default_embedding_service
    if _default_embedding_service is None:
        with _singleton_lock:
            if _default_embedding_service is None:
                _default_embedding_service = EmbeddingService(
                    settings=settings,
                    eager_load=eager_load,
                )
    elif eager_load and not _default_embedding_service.is_loaded():
        _default_embedding_service.load()
    return _default_embedding_service


def reset_embedding_service() -> None:
    """Clear the cached singleton :class:`EmbeddingService` (primarily for tests)."""
    global _default_embedding_service
    with _singleton_lock:
        _default_embedding_service = None


__all__ = [
    "EmbeddingService",
    "get_embedding_service",
    "reset_embedding_service",
]
