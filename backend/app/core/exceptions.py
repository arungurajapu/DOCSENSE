"""Domain exception hierarchy mapped to unified API error responses."""


class DocSenseError(Exception):
    """Base exception for all DocSense domain and infrastructure errors."""

    status_code: int = 500
    code: str = "INTERNAL_ERROR"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        if status_code is not None:
            self.status_code = status_code

    def to_error_payload(self) -> dict[str, dict[str, str]]:
        """Serialize exception into the unified ErrorResponse JSON structure."""
        return {"error": {"code": self.code, "message": self.message}}


class BadRequestError(DocSenseError):
    """Raised when an HTTP request violates basic input requirements."""

    status_code = 400
    code = "BAD_REQUEST"


class ValidationError(DocSenseError):
    """Raised when domain or semantic input validation fails."""

    status_code = 422
    code = "VALIDATION_ERROR"


class DocumentNotFoundError(DocSenseError):
    """Raised when a requested document record does not exist."""

    status_code = 404
    code = "DOCUMENT_NOT_FOUND"


class DocumentConflictError(DocSenseError):
    """Raised on invalid document state transitions or concurrent conflicts."""

    status_code = 409
    code = "DOCUMENT_CONFLICT"


class FileTooLargeError(DocSenseError):
    """Raised when an uploaded PDF exceeds MAX_UPLOAD_SIZE_MB."""

    status_code = 413
    code = "FILE_TOO_LARGE"


class UnsupportedMediaTypeError(DocSenseError):
    """Raised when an uploaded file is not a valid PDF."""

    status_code = 415
    code = "UNSUPPORTED_MEDIA_TYPE"


class EncryptedPDFError(DocSenseError):
    """Raised when an uploaded PDF requires a password or is encrypted."""

    status_code = 422
    code = "ENCRYPTED_PDF_NOT_SUPPORTED"


class EmptyPDFError(DocSenseError):
    """Raised when a PDF has zero pages or zero extractable text."""

    status_code = 422
    code = "EMPTY_PDF_NOT_SUPPORTED"


class StorageError(DocSenseError):
    """Raised on local file storage failures."""

    status_code = 500
    code = "STORAGE_ERROR"


class ExtractionError(DocSenseError):
    """Raised when PDF text extraction fails."""

    status_code = 422
    code = "EXTRACTION_ERROR"


class EmbeddingError(DocSenseError):
    """Raised when text embedding generation fails."""

    status_code = 500
    code = "EMBEDDING_ERROR"


class VectorStoreError(DocSenseError):
    """Raised when ChromaDB operations fail."""

    status_code = 500
    code = "VECTOR_STORE_ERROR"


class ServiceUnavailableError(DocSenseError):
    """Raised when a required backend dependency fails health checks."""

    status_code = 503
    code = "SERVICE_UNAVAILABLE"
