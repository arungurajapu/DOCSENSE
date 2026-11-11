"""FastAPI request dependencies reading lifespan singletons from ``app.state``."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from app.core.config import Settings
from app.repositories.document_repo import DocumentRepository
from app.services.ingestion import IngestionService
from app.services.interfaces import EmbeddingService, VectorStore


def get_app_settings(request: Request) -> Settings:
    """Return active runtime Settings attached during app lifespan."""
    return request.app.state.settings  # type: ignore[no-any-return]


def get_document_repo(request: Request) -> DocumentRepository:
    """Return active DocumentRepository attached during app lifespan."""
    return request.app.state.document_repo  # type: ignore[no-any-return]


def get_embedding_service_dep(request: Request) -> EmbeddingService:
    """Return active EmbeddingService attached during app lifespan."""
    return request.app.state.embedding_service  # type: ignore[no-any-return]


def get_vector_store(request: Request) -> VectorStore:
    """Return active VectorStore attached during app lifespan."""
    return request.app.state.vector_store  # type: ignore[no-any-return]


def get_ingestion_service(request: Request) -> IngestionService:
    """Return active IngestionService attached during app lifespan."""
    return request.app.state.ingestion_service  # type: ignore[no-any-return]


SettingsDep = Annotated[Settings, Depends(get_app_settings)]
DocumentRepoDep = Annotated[DocumentRepository, Depends(get_document_repo)]
EmbeddingServiceDep = Annotated[EmbeddingService, Depends(get_embedding_service_dep)]
VectorStoreDep = Annotated[VectorStore, Depends(get_vector_store)]
IngestionServiceDep = Annotated[IngestionService, Depends(get_ingestion_service)]

__all__ = [
    "DocumentRepoDep",
    "EmbeddingServiceDep",
    "IngestionServiceDep",
    "SettingsDep",
    "VectorStoreDep",
    "get_app_settings",
    "get_document_repo",
    "get_embedding_service_dep",
    "get_ingestion_service",
    "get_vector_store",
]
