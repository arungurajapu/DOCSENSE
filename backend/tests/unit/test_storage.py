"""Unit tests for local PDF storage validation, sanitization, and Windows cleanup."""

from __future__ import annotations

import asyncio
import hashlib
import io
import os
import uuid
from pathlib import Path

import pymupdf
import pytest
from fastapi import UploadFile
from starlette.datastructures import Headers

from app.core.config import Settings
from app.core.exceptions import (
    BadRequestError,
    EmptyPDFError,
    EncryptedPDFError,
    FileTooLargeError,
    StorageError,
    UnsupportedMediaTypeError,
)
from app.services.interfaces import StorageService as StorageServiceProtocol
from app.services.storage import StorageService


def _make_pdf_bytes(page_texts: list[str] | None = None, *, blank_pages: int = 0) -> bytes:
    """Create a valid PDF byte payload via PyMuPDF."""
    with pymupdf.open() as doc:
        for text in page_texts or []:
            page = doc.new_page()
            if text:
                page.insert_text((72, 72), text)
        for _ in range(blank_pages):
            page = doc.new_page()
            page.draw_rect(pymupdf.Rect(50, 50, 150, 150), color=(0, 0, 0), fill=(0.5, 0.5, 0.5))
        return doc.tobytes()


def _make_encrypted_pdf_bytes(
    *,
    encryption: int = pymupdf.PDF_ENCRYPT_AES_256,
    user_pw: str = "user-secret",
    owner_pw: str = "owner-secret",
) -> bytes:
    """Create a password-encrypted PDF byte payload via PyMuPDF."""
    with pymupdf.open() as doc:
        page = doc.new_page()
        page.insert_text((72, 72), "Confidential encrypted content")
        return doc.tobytes(
            encryption=encryption,
            owner_pw=owner_pw,
            user_pw=user_pw,
        )


def _make_zero_page_pdf_bytes() -> bytes:
    """Construct a syntactically valid PDF structure whose /Pages root declares /Count 0."""
    obj1 = b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
    obj2 = b"2 0 obj\n<< /Type /Pages /Kids [] /Count 0 >>\nendobj\n"
    header = b"%PDF-1.4\n"
    offset1 = len(header)
    offset2 = offset1 + len(obj1)
    xref_offset = offset2 + len(obj2)
    xref = (
        b"xref\n0 3\n"
        b"0000000000 65535 f \n"
        + f"{offset1:010d} 00000 n \n".encode("ascii")
        + f"{offset2:010d} 00000 n \n".encode("ascii")
    )
    trailer = (
        f"trailer\n<< /Size 3 /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n"
    ).encode("ascii")
    return header + obj1 + obj2 + xref + trailer


def _make_upload(
    payload: bytes,
    filename: str | None = "sample.pdf",
    content_type: str | None = "application/octet-stream",
) -> UploadFile:
    headers = Headers({"content-type": content_type}) if content_type is not None else Headers()
    return UploadFile(file=io.BytesIO(payload), filename=filename, headers=headers)


def test_storage_service_implements_protocol(test_settings: Settings) -> None:
    """StorageService conforms to the runtime-checkable StorageService protocol."""
    service = StorageService(test_settings)
    assert isinstance(service, StorageServiceProtocol)


def test_save_validated_pdf_success_untrusted_mime_and_uppercase_ext(
    test_settings: Settings,
) -> None:
    """Valid PDF succeeds regardless of spoofed MIME and supports immediate Windows os.remove."""
    pdf_bytes = _make_pdf_bytes(["Page one text", "Page two text"])
    expected_sha256 = hashlib.sha256(pdf_bytes).hexdigest()
    doc_id = str(uuid.uuid4())
    service = StorageService(test_settings)

    upload = _make_upload(
        pdf_bytes,
        filename="Quarterly_Report.PDF",
        content_type="text/plain",
    )
    result = asyncio.run(service.save_validated_pdf(upload, doc_id))

    assert result.filename == "Quarterly_Report.PDF"
    assert result.content_type == "application/pdf"
    assert result.sha256_hash == expected_sha256
    assert result.file_size_bytes == len(pdf_bytes)
    assert result.page_count == 2

    saved_path = Path(result.storage_path)
    assert saved_path.exists()
    assert saved_path == service.resolve_path(f"{doc_id}.pdf")
    assert saved_path.parent == Path(test_settings.UPLOAD_DIR).resolve()
    assert list(Path(test_settings.UPLOAD_DIR).iterdir()) == [saved_path]

    # Must close all handles deterministically so Windows os.remove succeeds immediately.
    os.remove(saved_path)
    assert not saved_path.exists()


def test_save_validated_pdf_accepts_single_page_scanned_image_pdf(
    test_settings: Settings,
) -> None:
    """1+ page PDFs with zero text (scanned PDFs) are accepted at upload time."""
    scanned_bytes = _make_pdf_bytes(blank_pages=1)
    doc_id = str(uuid.uuid4())
    service = StorageService(test_settings)

    result = asyncio.run(
        service.save_validated_pdf(_make_upload(scanned_bytes, filename="scan.pdf"), doc_id)
    )

    assert result.page_count == 1
    assert result.content_type == "application/pdf"
    os.remove(result.storage_path)


def test_save_validated_pdf_accepts_pdf_signature_within_first_1024_bytes(
    test_settings: Settings,
) -> None:
    """PDFs with %PDF- offset inside the first 1024 bytes pass signature validation."""
    base_pdf = _make_pdf_bytes(["Offset header check"])
    padded_pdf = (b" " * 256) + base_pdf
    doc_id = str(uuid.uuid4())
    service = StorageService(test_settings)

    result = asyncio.run(
        service.save_validated_pdf(_make_upload(padded_pdf, filename="offset.pdf"), doc_id)
    )
    assert result.page_count == 1
    os.remove(result.storage_path)


def test_save_validated_pdf_sanitizes_traversal_and_long_filenames(
    test_settings: Settings,
) -> None:
    """Traversal paths and >255-char filenames are sanitized while preserving .pdf suffix."""
    pdf_bytes = _make_pdf_bytes(["Sanitize filename"])
    service = StorageService(test_settings)

    traversal_cases = [
        ("../../etc/passwd/../../secret.pdf", "secret.pdf"),
        ("C:\\Users\\attacker\\..\\..\\report.PDF", "report.PDF"),
        ("D:drive_prefixed.pdf", "drive_prefixed.pdf"),
    ]
    for raw_name, expected_name in traversal_cases:
        doc_id = str(uuid.uuid4())
        res = asyncio.run(
            service.save_validated_pdf(_make_upload(pdf_bytes, filename=raw_name), doc_id)
        )
        assert res.filename == expected_name
        assert Path(res.storage_path).name == f"{doc_id}.pdf"
        service.delete_file(res.storage_path)

    long_lower = f"{'a' * 300}.pdf"
    res_lower = asyncio.run(
        service.save_validated_pdf(_make_upload(pdf_bytes, filename=long_lower), str(uuid.uuid4()))
    )
    assert len(res_lower.filename) == 255
    assert res_lower.filename.endswith(".pdf")
    service.delete_file(res_lower.storage_path)

    long_upper = f"{'B' * 300}.PDF"
    res_upper = asyncio.run(
        service.save_validated_pdf(_make_upload(pdf_bytes, filename=long_upper), str(uuid.uuid4()))
    )
    assert len(res_upper.filename) == 255
    assert res_upper.filename.endswith(".PDF")
    service.delete_file(res_upper.storage_path)


@pytest.mark.parametrize(
    "bad_filename",
    [None, "", "   ", "../../", "..", ".", ".pdf", "   .pdf", "\x00\n"],
)
def test_save_validated_pdf_rejects_missing_or_empty_filename(
    test_settings: Settings,
    bad_filename: str | None,
) -> None:
    """Missing or empty sanitized filenames raise BadRequestError (400)."""
    pdf_bytes = _make_pdf_bytes(["hello"])
    service = StorageService(test_settings)

    with pytest.raises(BadRequestError) as exc_info:
        asyncio.run(
            service.save_validated_pdf(
                _make_upload(pdf_bytes, filename=bad_filename),
                str(uuid.uuid4()),
            )
        )
    assert exc_info.value.status_code == 400


def test_save_validated_pdf_rejects_non_pdf_suffix_even_with_pdf_mime(
    test_settings: Settings,
) -> None:
    """Non-.pdf extensions are rejected with UnsupportedMediaTypeError (415) despite PDF MIME."""
    pdf_bytes = _make_pdf_bytes(["hello"])
    service = StorageService(test_settings)

    with pytest.raises(UnsupportedMediaTypeError) as exc_info:
        asyncio.run(
            service.save_validated_pdf(
                _make_upload(pdf_bytes, filename="spoofed.txt", content_type="application/pdf"),
                str(uuid.uuid4()),
            )
        )
    assert exc_info.value.status_code == 415


def test_save_validated_pdf_rejects_zero_byte_file_and_cleans_temp(
    test_settings: Settings,
) -> None:
    """Zero-byte uploads raise BadRequestError (400) and leave no temp artifacts."""
    service = StorageService(test_settings)

    with pytest.raises(BadRequestError) as exc_info:
        asyncio.run(
            service.save_validated_pdf(_make_upload(b"", filename="empty.pdf"), str(uuid.uuid4()))
        )
    assert exc_info.value.status_code == 400
    assert list(Path(test_settings.UPLOAD_DIR).iterdir()) == []


def test_save_validated_pdf_exact_max_size_vs_plus_one_byte(tmp_path: Path) -> None:
    """Exact max_upload_size_bytes succeeds; +1 byte fails with 413 and cleans temp file."""
    upload_dir = tmp_path / "uploads"
    cfg = Settings(
        _env_file=None,
        DATABASE_URL=f"sqlite:///{(tmp_path / 'test.db').as_posix()}",
        CHROMA_PERSIST_DIR=str(tmp_path / "chroma"),
        UPLOAD_DIR=str(upload_dir),
        MAX_UPLOAD_SIZE_MB=1,
    )
    service = StorageService(cfg, chunk_size=16 * 1024)
    limit = cfg.max_upload_size_bytes  # 1_048_576 bytes

    base_pdf = _make_pdf_bytes(["Exact size boundary test"])
    assert len(base_pdf) < limit
    exact_payload = base_pdf + b"\n%" + b"0" * (limit - len(base_pdf) - 2)
    assert len(exact_payload) == limit

    ok_id = str(uuid.uuid4())
    result = asyncio.run(
        service.save_validated_pdf(_make_upload(exact_payload, filename="exact.pdf"), ok_id)
    )
    assert result.file_size_bytes == limit
    assert result.page_count == 1
    os.remove(result.storage_path)

    oversize_payload = exact_payload + b"0"
    assert len(oversize_payload) == limit + 1
    fail_id = str(uuid.uuid4())

    with pytest.raises(FileTooLargeError) as exc_info:
        asyncio.run(
            service.save_validated_pdf(_make_upload(oversize_payload, filename="over.pdf"), fail_id)
        )
    assert exc_info.value.status_code == 413
    assert list(upload_dir.iterdir()) == []


def test_save_validated_pdf_rejects_missing_or_late_signature(
    test_settings: Settings,
) -> None:
    """Files without %PDF- in the first 1024 bytes raise UnsupportedMediaTypeError (415)."""
    service = StorageService(test_settings)

    for payload in [
        b"PK\x03\x04not a pdf file",
        (b"A" * 1025) + _make_pdf_bytes(["Too late"]),
    ]:
        with pytest.raises(UnsupportedMediaTypeError) as exc_info:
            asyncio.run(
                service.save_validated_pdf(
                    _make_upload(payload, filename="fake.pdf", content_type="application/pdf"),
                    str(uuid.uuid4()),
                )
            )
        assert exc_info.value.status_code == 415
        assert list(Path(test_settings.UPLOAD_DIR).iterdir()) == []


def test_save_validated_pdf_rejects_corrupt_pdf_and_releases_handles(
    test_settings: Settings,
    tmp_path: Path,
) -> None:
    """Corrupt PDFs with %PDF- prefix raise 415 and allow immediate Windows file removal."""
    corrupt_bytes = b"%PDF-1.7\n%corrupted-stream-data-not-valid-xref"
    source_path = tmp_path / "source_corrupt.pdf"
    source_path.write_bytes(corrupt_bytes)

    service = StorageService(test_settings)
    with open(source_path, "rb") as fh:
        upload = UploadFile(file=fh, filename="corrupt.pdf")
        with pytest.raises(UnsupportedMediaTypeError) as exc_info:
            asyncio.run(service.save_validated_pdf(upload, str(uuid.uuid4())))
    assert exc_info.value.status_code == 415
    assert list(Path(test_settings.UPLOAD_DIR).iterdir()) == []

    os.remove(source_path)
    assert not source_path.exists()


@pytest.mark.parametrize(
    "encryption",
    [pymupdf.PDF_ENCRYPT_AES_256, pymupdf.PDF_ENCRYPT_RC4_128],
)
def test_save_validated_pdf_rejects_encrypted_pdf(
    test_settings: Settings,
    encryption: int,
) -> None:
    """Encrypted/password-protected PDFs raise EncryptedPDFError (422) and clean temp files."""
    encrypted_bytes = _make_encrypted_pdf_bytes(encryption=encryption)
    service = StorageService(test_settings)

    with pytest.raises(EncryptedPDFError) as exc_info:
        asyncio.run(
            service.save_validated_pdf(
                _make_upload(encrypted_bytes, filename="locked.pdf"),
                str(uuid.uuid4()),
            )
        )
    assert exc_info.value.status_code == 422
    assert list(Path(test_settings.UPLOAD_DIR).iterdir()) == []


def test_save_validated_pdf_rejects_zero_page_pdf(test_settings: Settings) -> None:
    """PDFs with zero pages raise EmptyPDFError (422) and leave no temp artifacts."""
    zero_page_bytes = _make_zero_page_pdf_bytes()
    service = StorageService(test_settings)

    with pytest.raises(EmptyPDFError) as exc_info:
        asyncio.run(
            service.save_validated_pdf(
                _make_upload(zero_page_bytes, filename="zero_pages.pdf"),
                str(uuid.uuid4()),
            )
        )
    assert exc_info.value.status_code == 422
    assert list(Path(test_settings.UPLOAD_DIR).iterdir()) == []


def test_resolve_path_and_delete_file_traversal_and_idempotency(
    test_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """resolve_path blocks traversal; delete_file is idempotent and retries WinError locks."""
    service = StorageService(test_settings)
    upload_dir = Path(test_settings.UPLOAD_DIR).resolve()
    upload_dir.mkdir(parents=True, exist_ok=True)

    doc_id = str(uuid.uuid4())
    resolved_rel = service.resolve_path(f"{doc_id}.pdf")
    assert resolved_rel == upload_dir / f"{doc_id}.pdf"
    resolved_abs = service.resolve_path(str(resolved_rel))
    assert resolved_abs == resolved_rel

    for bad_path in [
        "",
        "   ",
        ".",
        "..",
        "../outside.pdf",
        "nested/../../outside.pdf",
        "/etc/passwd",
        "\\Windows\\System32\\win.ini",
        "C:\\outside\\doc.pdf",
        "C:drive_relative.pdf",
        "bad\x00.pdf",
    ]:
        with pytest.raises(StorageError):
            service.resolve_path(bad_path)
        with pytest.raises(StorageError):
            service.delete_file(bad_path)

    # Idempotent delete on missing file inside UPLOAD_DIR.
    service.delete_file(f"{doc_id}.pdf")

    # Save, delete, and re-delete idempotently.
    saved = asyncio.run(
        service.save_validated_pdf(_make_upload(_make_pdf_bytes(["Delete test"])), doc_id)
    )
    assert Path(saved.storage_path).exists()
    service.delete_file(saved.storage_path)
    assert not Path(saved.storage_path).exists()
    service.delete_file(saved.storage_path)

    # Simulate transient Windows PermissionError on unlink followed by success.
    target = upload_dir / f"{uuid.uuid4()}.pdf"
    target.write_bytes(b"temp")
    original_unlink = Path.unlink
    attempts = {"count": 0}

    def flaky_unlink(self: Path, missing_ok: bool = False) -> None:
        attempts["count"] += 1
        if attempts["count"] <= 2:
            raise PermissionError("Simulated WinError 32 file lock")
        original_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", flaky_unlink)
    monkeypatch.setattr("app.services.storage.time.sleep", lambda _secs: None)

    service.delete_file(str(target))
    assert attempts["count"] == 3
    assert not target.exists()
