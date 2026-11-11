"""Deterministic programmatic PyMuPDF fixture builders for E2E testing."""

from __future__ import annotations

import pymupdf


def make_text_pdf_bytes(
    page_texts: list[str] | None = None,
    *,
    blank_pages: int = 0,
) -> bytes:
    """Create a valid PDF with optional text pages followed by drawn blank pages."""
    with pymupdf.open() as doc:
        for text in page_texts or []:
            page = doc.new_page()
            if text:
                rc = page.insert_textbox(pymupdf.Rect(40, 40, 555, 800), text, fontsize=10)
                if rc < 0:
                    raise ValueError("Fixture page text overflowed PDF page rect.")
        for _ in range(blank_pages):
            page = doc.new_page()
            page.draw_rect(
                pymupdf.Rect(50, 50, 150, 150),
                color=(0, 0, 0),
                fill=(0.5, 0.5, 0.5),
            )
        return doc.tobytes()


def make_multipage_crosspage_pdf_bytes() -> bytes:
    """Create a 3-page PDF with a drawn blank middle page and >220 tokens across pages 1 and 3.

    Chunking with CHUNK_SIZE_TOKENS=200 and CHUNK_OVERLAP_TOKENS=40 yields multiple chunks,
    including at least one chunk whose character span crosses from page 1 to page 3
    (with page 2 blank/skipped by text extraction, giving page_number=1, page_end=3,
    and page_numbers_csv="1,3").
    """
    page_one_words = [f"alpha beta gamma delta pageoneword{i % 10}" for i in range(30)]
    page_three_words = [f"omega vector index token pagethreeword{i % 10}" for i in range(30)]
    page_one_text = " ".join(page_one_words)
    page_three_text = " ".join(page_three_words)

    with pymupdf.open() as doc:
        p1 = doc.new_page()
        assert p1.insert_textbox(pymupdf.Rect(40, 40, 555, 800), page_one_text, fontsize=10) >= 0

        p2 = doc.new_page()
        p2.draw_rect(
            pymupdf.Rect(60, 60, 220, 220),
            color=(0.1, 0.1, 0.1),
            fill=(0.85, 0.85, 0.85),
        )

        p3 = doc.new_page()
        assert (
            p3.insert_textbox(pymupdf.Rect(40, 40, 555, 800), page_three_text, fontsize=10) >= 0
        )

        return doc.tobytes()


def make_scanned_pdf_bytes(page_count: int = 2) -> bytes:
    """Create a valid PDF with drawn graphics only and zero extractable text."""
    return make_text_pdf_bytes(page_texts=None, blank_pages=page_count)


def make_encrypted_pdf_bytes(
    *,
    encryption: int = pymupdf.PDF_ENCRYPT_AES_256,
    user_pw: str = "user-secret",
    owner_pw: str = "owner-secret",
) -> bytes:
    """Create a password-encrypted PDF payload via PyMuPDF."""
    with pymupdf.open() as doc:
        page = doc.new_page()
        page.insert_text((72, 72), "Encrypted DocSense E2E fixture content.")
        return doc.tobytes(
            encryption=encryption,
            owner_pw=owner_pw,
            user_pw=user_pw,
        )


def make_zero_page_pdf_bytes() -> bytes:
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


def make_corrupt_pdf_bytes() -> bytes:
    """Create bytes with a valid %PDF- header followed by invalid/corrupt PDF body."""
    return b"%PDF-1.4\n1 0 obj\n<< /Broken /CorruptStream\n%%corrupt-eof"


def make_spoofed_pdf_bytes() -> bytes:
    """Create plain text masquerading as a .pdf file without a %PDF- header."""
    return b"This is plain text pretending to be a PDF document.\n"


def make_sized_valid_pdf_bytes(target_bytes: int, text: str = "Sized DocSense PDF") -> bytes:
    """Generate a valid PyMuPDF-readable PDF of exact byte size ``target_bytes``."""
    base = make_text_pdf_bytes([text])
    if target_bytes <= len(base):
        raise ValueError(
            f"target_bytes ({target_bytes}) must exceed base PDF size ({len(base)})."
        )
    padding_len = target_bytes - len(base) - 2
    if padding_len < 0:
        raise ValueError("target_bytes too close to base PDF size for comment padding.")
    return base + b"\n%" + (b"0" * padding_len)
