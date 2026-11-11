"""Local filesystem PDF storage with streaming validation and Windows-safe cleanup."""

import asyncio
import gc
import hashlib
import os
import re
import time
import traceback
import uuid
from pathlib import Path

import pymupdf
from fastapi import UploadFile

from app.core.config import Settings, get_settings
from app.core.exceptions import (
    BadRequestError,
    EmptyPDFError,
    EncryptedPDFError,
    FileTooLargeError,
    StorageError,
    UnsupportedMediaTypeError,
)
from app.services.interfaces import SavedFileResult

MAX_FILENAME_LENGTH = 255
PDF_SIGNATURE = b"%PDF-"
PDF_HEADER_SCAN_BYTES = 1024
STREAM_CHUNK_SIZE = 64 * 1024
DELETE_RETRY_ATTEMPTS = 5
DELETE_RETRY_DELAY_SECONDS = 0.05

_SAFE_DOCUMENT_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_WINDOWS_DRIVE_PREFIX_RE = re.compile(r"^[A-Za-z]:")


def _sanitize_filename(raw_filename: str | None) -> str:
    """Strip directory traversal parts, enforce .pdf suffix, and bound length to 255 chars."""
    if raw_filename is None:
        raise BadRequestError("Missing upload filename.")

    leaf = raw_filename.replace("\\", "/").split("/")[-1]
    leaf = _WINDOWS_DRIVE_PREFIX_RE.sub("", leaf)
    cleaned = "".join(ch for ch in leaf if ord(ch) >= 32 and ord(ch) != 127).strip()

    if not cleaned or cleaned in {".", ".."}:
        raise BadRequestError("Upload filename must not be empty.")

    if not cleaned.lower().endswith(".pdf"):
        raise UnsupportedMediaTypeError("Only PDF files (.pdf) are supported.")

    stem = cleaned[:-4]
    if not stem.strip():
        raise BadRequestError("Upload filename must not be empty.")

    if len(cleaned) > MAX_FILENAME_LENGTH:
        suffix = cleaned[-4:]
        cleaned = f"{stem[: MAX_FILENAME_LENGTH - len(suffix)]}{suffix}"

    return cleaned


def _validate_document_id(document_id: str) -> str:
    """Ensure document_id is a safe flat filename token with no path traversal."""
    if not document_id or not _SAFE_DOCUMENT_ID_RE.fullmatch(document_id):
        raise StorageError(f"Invalid document identifier for storage: {document_id!r}")
    return document_id


def _release_exception_handles(exc: BaseException) -> None:
    """Clear traceback frames on failed PyMuPDF open calls so C-level file handles close."""
    if exc.__traceback__ is not None:
        traceback.clear_frames(exc.__traceback__)
    if exc.__cause__ is not None and exc.__cause__.__traceback__ is not None:
        traceback.clear_frames(exc.__cause__.__traceback__)
    gc.collect()


def _inspect_pdf_file(file_path: Path) -> int:
    """Verify PDF readability, reject encrypted/0-page files, and return page count."""
    try:
        with pymupdf.open(str(file_path)) as doc:
            if doc.is_encrypted or doc.needs_pass:
                raise EncryptedPDFError(
                    "Encrypted or password-protected PDF files are not supported."
                )
            page_count = len(doc)
            if page_count == 0:
                raise EmptyPDFError("PDF document contains zero pages.")
            return page_count
    except (EncryptedPDFError, EmptyPDFError):
        raise
    except Exception as exc:
        _release_exception_handles(exc)
        del exc
        gc.collect()
        raise UnsupportedMediaTypeError(
            "Uploaded file is corrupt or not a readable PDF."
        ) from None


def _unlink_with_retry(path: Path) -> None:
    """Delete a file with bounded backoff retries for transient Windows sharing locks."""
    for attempt in range(DELETE_RETRY_ATTEMPTS):
        try:
            path.unlink(missing_ok=True)
            return
        except PermissionError as exc:
            if attempt == DELETE_RETRY_ATTEMPTS - 1:
                raise StorageError(f"Unable to remove stored file '{path}': {exc}") from exc
            time.sleep(DELETE_RETRY_DELAY_SECONDS * (attempt + 1))
        except OSError as exc:
            raise StorageError(f"Failed to remove stored file '{path}': {exc}") from exc


def _best_effort_remove(path: Path) -> None:
    """Remove temporary upload artifacts without masking the primary validation error."""
    for attempt in range(DELETE_RETRY_ATTEMPTS):
        try:
            path.unlink(missing_ok=True)
            return
        except PermissionError:
            if attempt == DELETE_RETRY_ATTEMPTS - 1:
                return
            time.sleep(DELETE_RETRY_DELAY_SECONDS * (attempt + 1))
        except OSError:
            return


class StorageService:
    """Local filesystem storage implementing streaming PDF validation and safe cleanup."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        chunk_size: int = STREAM_CHUNK_SIZE,
    ) -> None:
        if chunk_size <= 0:
            raise ValueError("chunk_size must be a positive integer.")
        self._settings = settings or get_settings()
        self._chunk_size = chunk_size

    def _get_upload_dir(self) -> Path:
        return Path(self._settings.UPLOAD_DIR).resolve()

    async def save_validated_pdf(
        self,
        upload_file: UploadFile,
        document_id: str,
    ) -> SavedFileResult:
        """Stream, validate, hash, inspect via PyMuPDF, and atomically persist an uploaded PDF."""
        sanitized_filename = _sanitize_filename(upload_file.filename if upload_file else None)
        safe_doc_id = _validate_document_id(document_id)

        upload_dir = self._get_upload_dir()
        upload_dir.mkdir(parents=True, exist_ok=True)
        final_path = self.resolve_path(f"{safe_doc_id}.pdf")
        temp_path = upload_dir / f".tmp_{safe_doc_id}_{uuid.uuid4().hex}.pdf"

        max_bytes = self._settings.max_upload_size_bytes
        hasher = hashlib.sha256()
        total_bytes = 0
        header_buffer = bytearray()
        promoted = False

        try:
            with open(temp_path, "wb") as out_file:
                while True:
                    chunk = await upload_file.read(self._chunk_size)
                    if not chunk:
                        break
                    total_bytes += len(chunk)
                    if total_bytes > max_bytes:
                        raise FileTooLargeError(
                            "Uploaded PDF exceeds maximum allowed size of "
                            f"{self._settings.MAX_UPLOAD_SIZE_MB} MB."
                        )
                    if len(header_buffer) < PDF_HEADER_SCAN_BYTES:
                        needed = PDF_HEADER_SCAN_BYTES - len(header_buffer)
                        header_buffer.extend(chunk[:needed])
                    hasher.update(chunk)
                    out_file.write(chunk)

            if total_bytes == 0:
                raise BadRequestError("Uploaded PDF file is empty (0 bytes).")

            if PDF_SIGNATURE not in bytes(header_buffer):
                raise UnsupportedMediaTypeError(
                    "Uploaded file is missing a valid %PDF- header signature."
                )

            page_count = await asyncio.to_thread(_inspect_pdf_file, temp_path)

            os.replace(temp_path, final_path)
            promoted = True

            return SavedFileResult(
                filename=sanitized_filename,
                storage_path=str(final_path),
                sha256_hash=hasher.hexdigest(),
                file_size_bytes=total_bytes,
                content_type="application/pdf",
                page_count=page_count,
            )
        except (
            BadRequestError,
            FileTooLargeError,
            UnsupportedMediaTypeError,
            EncryptedPDFError,
            EmptyPDFError,
            StorageError,
        ):
            raise
        except OSError as exc:
            raise StorageError(f"Failed to persist uploaded PDF: {exc}") from exc
        finally:
            if not promoted:
                _best_effort_remove(temp_path)

    def resolve_path(self, storage_path: str) -> Path:
        """Resolve a stored file path safely inside the configured UPLOAD_DIR."""
        if not storage_path or not storage_path.strip() or "\x00" in storage_path:
            raise StorageError("Storage path must not be empty.")

        upload_dir = self._get_upload_dir()
        candidate = Path(storage_path)

        if candidate.is_absolute() or candidate.drive or storage_path.startswith(("/", "\\")):
            resolved = candidate.resolve()
        else:
            resolved = (upload_dir / candidate).resolve()

        if resolved == upload_dir or not resolved.is_relative_to(upload_dir):
            raise StorageError(
                f"Storage path escapes configured upload directory: {storage_path!r}"
            )

        return resolved

    def delete_file(self, storage_path: str) -> None:
        """Idempotently remove a stored PDF inside UPLOAD_DIR with Windows retry cleanup."""
        resolved = self.resolve_path(storage_path)
        if resolved.is_dir():
            raise StorageError(f"Refusing to delete directory path: {resolved}")
        _unlink_with_retry(resolved)
