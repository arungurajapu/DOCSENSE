"""Core configuration and exception primitives for DocSense."""

from app.core.config import BACKEND_DIR, REPO_ROOT, Settings, get_settings, settings
from app.core.exceptions import (
    BadRequestError,
    DocSenseError,
    DocumentConflictError,
    DocumentNotFoundError,
    EmbeddingError,
    EmptyPDFError,
    EncryptedPDFError,
    ExtractionError,
    FileTooLargeError,
    ServiceUnavailableError,
    StorageError,
    UnsupportedMediaTypeError,
    ValidationError,
    VectorStoreError,
)

__all__ = [
    "BACKEND_DIR",
    "BadRequestError",
    "DocSenseError",
    "DocumentConflictError",
    "DocumentNotFoundError",
    "EmbeddingError",
    "EmptyPDFError",
    "EncryptedPDFError",
    "ExtractionError",
    "FileTooLargeError",
    "REPO_ROOT",
    "ServiceUnavailableError",
    "Settings",
    "StorageError",
    "UnsupportedMediaTypeError",
    "ValidationError",
    "VectorStoreError",
    "get_settings",
    "settings",
]
