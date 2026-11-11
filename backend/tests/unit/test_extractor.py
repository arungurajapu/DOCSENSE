"""Unit tests for PyMuPDF-isolated PDF text extraction and handle release."""

from __future__ import annotations

import os
from pathlib import Path

import pymupdf
import pytest

from app.core.exceptions import EmptyPDFError, EncryptedPDFError, ExtractionError
from app.services.extractor import PDFExtractor
from app.services.interfaces import ExtractedPage
from app.services.interfaces import PDFExtractor as PDFExtractorProtocol


def _write_zero_page_pdf(path: Path) -> None:
    """Write a minimal PDF with /Count 0."""
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
    path.write_bytes(header + obj1 + obj2 + xref + trailer)


def test_pdf_extractor_implements_protocol() -> None:
    """PDFExtractor conforms to the runtime-checkable PDFExtractor protocol."""
    assert isinstance(PDFExtractor(), PDFExtractorProtocol)


def test_extract_multipage_preserves_1based_numbering_with_blank_intermediate_pages(
    tmp_path: Path,
) -> None:
    """Blank intermediate pages are omitted while original 1-based page numbers remain exact."""
    pdf_path = tmp_path / "multipage_blanks.pdf"
    with pymupdf.open() as doc:
        p1 = doc.new_page()
        p1.insert_text((72, 72), "Page one alpha")
        doc.new_page()  # Page 2: completely blank
        p3 = doc.new_page()
        p3.draw_rect(pymupdf.Rect(50, 50, 120, 120), color=(0, 0, 0))  # Page 3: graphics only
        p4 = doc.new_page()
        p4.insert_text((72, 72), "Page four omega")
        doc.save(str(pdf_path))

    pages = PDFExtractor().extract(pdf_path)

    assert pages == [
        ExtractedPage(page_number=1, text="Page one alpha"),
        ExtractedPage(page_number=4, text="Page four omega"),
    ]

    # Deterministic handle release allows immediate Windows os.remove.
    os.remove(pdf_path)
    assert not pdf_path.exists()


def test_extract_scanned_pdf_raises_empty_pdf_error_and_releases_handle(
    tmp_path: Path,
) -> None:
    """Scanned/image-only PDFs with zero extractable text raise EmptyPDFError and release lock."""
    pdf_path = tmp_path / "scanned.pdf"
    with pymupdf.open() as doc:
        page = doc.new_page()
        pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 16, 16), 0)
        pix.set_rect(pix.irect, (200, 200, 200))
        page.insert_image(pymupdf.Rect(50, 50, 200, 200), pixmap=pix)
        doc.save(str(pdf_path))

    with pytest.raises(EmptyPDFError):
        PDFExtractor().extract(pdf_path)

    os.remove(pdf_path)
    assert not pdf_path.exists()


def test_extract_zero_page_pdf_raises_empty_pdf_error_and_releases_handle(
    tmp_path: Path,
) -> None:
    """A 0-page PDF raises EmptyPDFError and releases the OS handle."""
    pdf_path = tmp_path / "zero_page.pdf"
    _write_zero_page_pdf(pdf_path)

    with pytest.raises(EmptyPDFError):
        PDFExtractor().extract(pdf_path)

    os.remove(pdf_path)
    assert not pdf_path.exists()


@pytest.mark.parametrize(
    "encryption",
    [pymupdf.PDF_ENCRYPT_AES_256, pymupdf.PDF_ENCRYPT_RC4_128],
)
def test_extract_encrypted_pdf_raises_encrypted_error_and_releases_handle(
    tmp_path: Path,
    encryption: int,
) -> None:
    """Encrypted PDFs raise EncryptedPDFError and allow immediate file deletion."""
    pdf_path = tmp_path / "encrypted.pdf"
    with pymupdf.open() as doc:
        page = doc.new_page()
        page.insert_text((72, 72), "Secret")
        doc.save(
            str(pdf_path),
            encryption=encryption,
            owner_pw="owner-pw",
            user_pw="user-pw",
        )

    with pytest.raises(EncryptedPDFError):
        PDFExtractor().extract(pdf_path)

    os.remove(pdf_path)
    assert not pdf_path.exists()


def test_extract_missing_or_corrupt_pdf_raises_extraction_error_and_releases_handle(
    tmp_path: Path,
) -> None:
    """Missing paths, directories, and corrupt PDFs raise ExtractionError cleanly."""
    extractor = PDFExtractor()

    with pytest.raises(ExtractionError):
        extractor.extract(tmp_path / "does_not_exist.pdf")

    with pytest.raises(ExtractionError):
        extractor.extract(tmp_path)

    corrupt_path = tmp_path / "corrupt.pdf"
    corrupt_path.write_bytes(b"%PDF-1.7\ncorrupted-binary-payload")

    try:
        extractor.extract(corrupt_path)
        pytest.fail("Expected ExtractionError for corrupt PDF")
    except ExtractionError:
        os.remove(corrupt_path)

    assert not corrupt_path.exists()
