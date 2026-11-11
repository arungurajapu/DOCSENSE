"""Deterministic programmatic PDF fixture generators for E2E QA suites."""

from tests.fixtures.pdf_factory import (
    make_corrupt_pdf_bytes,
    make_encrypted_pdf_bytes,
    make_multipage_crosspage_pdf_bytes,
    make_scanned_pdf_bytes,
    make_sized_valid_pdf_bytes,
    make_spoofed_pdf_bytes,
    make_text_pdf_bytes,
    make_zero_page_pdf_bytes,
)

__all__ = [
    "make_corrupt_pdf_bytes",
    "make_encrypted_pdf_bytes",
    "make_multipage_crosspage_pdf_bytes",
    "make_scanned_pdf_bytes",
    "make_sized_valid_pdf_bytes",
    "make_spoofed_pdf_bytes",
    "make_text_pdf_bytes",
    "make_zero_page_pdf_bytes",
]
