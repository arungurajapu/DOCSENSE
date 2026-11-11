"""FastAPI application factory, lifespan resource management, and entrypoint."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi

from app.api.errors import register_exception_handlers
from app.api.v1.router import api_router
from app.core.config import Settings, get_settings
from app.core.exceptions import EmbeddingError
from app.db.session import create_db_engine, create_session_factory, init_db
from app.repositories.document_repo import DocumentRepository
from app.repositories.vector_store import ChromaVectorStore
from app.services.chunker import TokenChunker
from app.services.embeddings import EmbeddingService
from app.services.extractor import PDFExtractor
from app.services.ingestion import IngestionService
from app.services.interfaces import (
    EmbeddingService as EmbeddingServiceProtocol,
)
from app.services.interfaces import (
    PDFExtractor as PDFExtractorProtocol,
)
from app.services.interfaces import (
    StorageService as StorageServiceProtocol,
)
from app.services.interfaces import (
    TokenChunker as TokenChunkerProtocol,
)
from app.services.interfaces import (
    VectorStore as VectorStoreProtocol,
)
from app.services.storage import StorageService

logger = logging.getLogger(__name__)


def create_app(
    settings: Settings | None = None,
    *,
    eager_load_embeddings: bool = True,
    storage: StorageServiceProtocol | None = None,
    extractor: PDFExtractorProtocol | None = None,
    chunker: TokenChunkerProtocol | None = None,
    embedding_service: EmbeddingServiceProtocol | None = None,
    vector_store: VectorStoreProtocol | None = None,
) -> FastAPI:
    """Construct the DocSense FastAPI app without initializing stores outside lifespan."""
    cfg = settings or get_settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        engine = create_db_engine(cfg.DATABASE_URL)
        active_vector_store: VectorStoreProtocol | None = None
        ingestion: IngestionService | None = None

        try:
            await asyncio.to_thread(init_db, engine)
            session_factory = create_session_factory(engine)
            repo = DocumentRepository(session_factory)

            active_storage = storage if storage is not None else StorageService(cfg)
            active_extractor = extractor if extractor is not None else PDFExtractor()
            active_chunker = (
                chunker
                if chunker is not None
                else await asyncio.to_thread(TokenChunker, cfg)
            )
            active_embeddings = (
                embedding_service
                if embedding_service is not None
                else EmbeddingService(settings=cfg, eager_load=False)
            )
            active_vector_store = (
                vector_store
                if vector_store is not None
                else await asyncio.to_thread(ChromaVectorStore, settings=cfg)
            )
            ingestion = IngestionService(
                repo=repo,
                storage=active_storage,
                extractor=active_extractor,
                chunker=active_chunker,
                embeddings=active_embeddings,
                vector_store=active_vector_store,
            )

            application.state.settings = cfg
            application.state.engine = engine
            application.state.session_factory = session_factory
            application.state.document_repo = repo
            application.state.storage_service = active_storage
            application.state.extractor = active_extractor
            application.state.chunker = active_chunker
            application.state.embedding_service = active_embeddings
            application.state.vector_store = active_vector_store
            application.state.ingestion_service = ingestion

            if eager_load_embeddings:
                load_fn = getattr(active_embeddings, "load", None)
                if callable(load_fn):
                    try:
                        await asyncio.to_thread(load_fn)
                    except EmbeddingError as exc:
                        logger.warning(
                            "Eager loading of embedding model failed; starting degraded: %s",
                            exc,
                        )
                    except Exception as exc:
                        logger.warning(
                            "Unexpected error loading embedding model; starting degraded: %s",
                            exc,
                        )

            await ingestion.recover_incomplete_documents()
            yield
        finally:
            if ingestion is not None:
                await ingestion.shutdown()
            if active_vector_store is not None:
                await asyncio.to_thread(active_vector_store.close)
            await asyncio.to_thread(engine.dispose)

    application = FastAPI(
        title="DocSense API",
        version=cfg.APP_VERSION,
        description="DocSense Document Ingestion API contract.",
        lifespan=lifespan,
    )

    application.add_middleware(
        CORSMiddleware,
        allow_origins=cfg.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_exception_handlers(application)
    application.include_router(api_router, prefix=cfg.API_V1_PREFIX)

    def _custom_openapi() -> dict[str, Any]:
        if application.openapi_schema is not None:
            return application.openapi_schema
        schema = get_openapi(
            title=application.title,
            version=application.version,
            description=application.description,
            routes=application.routes,
        )
        components = schema.get("components", {})
        schemas = components.get("schemas", {})
        schemas.pop("HTTPValidationError", None)
        schemas.pop("ValidationError", None)
        application.openapi_schema = schema
        return application.openapi_schema

    application.openapi = _custom_openapi  # type: ignore[method-assign]
    return application


app = create_app()

__all__ = ["app", "create_app"]
