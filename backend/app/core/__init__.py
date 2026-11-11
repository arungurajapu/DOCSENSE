"""Core configuration and exception primitives for DocSense."""

from app.core.config import Settings, get_settings, settings
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
    "BadRequestError",
    "DocSenseError",
    "DocumentConflictError",
    "DocumentNotFoundError",
    "EmbeddingError",
    "EmptyPDFError",
    "EncryptedPDFError",
    "ExtractionError",
    "FileTooLargeError",
    "ServiceUnavailableError",
    "Settings",
    "StorageError",
    "UnsupportedMediaTypeError",
    "ValidationError",
    "VectorStoreError",
    "get_settings",
    "settings",
]
