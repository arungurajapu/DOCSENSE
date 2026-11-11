"""PyMuPDF-isolated PDF text extractor."""

import gc
import traceback
from pathlib import Path

import pymupdf

from app.core.exceptions import EmptyPDFError, EncryptedPDFError, ExtractionError
from app.services.interfaces import ExtractedPage


def _release_exception_handles(exc: BaseException) -> None:
    """Clear traceback frames on failed PyMuPDF open calls so C-level file handles close."""
    if exc.__traceback__ is not None:
        traceback.clear_frames(exc.__traceback__)
    if exc.__cause__ is not None and exc.__cause__.__traceback__ is not None:
        traceback.clear_frames(exc.__cause__.__traceback__)
    gc.collect()


class PDFExtractor:
    """Contract implementation isolating PyMuPDF page text extraction."""

    def extract(self, file_path: str | Path) -> list[ExtractedPage]:
        """Extract ordered 1-based non-empty text pages from a local PDF file."""
        path = Path(file_path)
        if not path.is_file():
            raise ExtractionError(f"PDF file not found: {path}")

        pages: list[ExtractedPage] = []
        try:
            with pymupdf.open(str(path)) as doc:
                if doc.is_encrypted or doc.needs_pass:
                    raise EncryptedPDFError(
                        "Encrypted or password-protected PDF files are not supported."
                    )
                if len(doc) == 0:
                    raise EmptyPDFError("PDF document contains zero pages.")

                for page_number, page in enumerate(doc, start=1):
                    raw_text = page.get_text("text")
                    cleaned = (
                        raw_text.replace("\x00", "")
                        .replace("\r\n", "\n")
                        .replace("\r", "\n")
                        .strip()
                    )
                    if cleaned:
                        pages.append(ExtractedPage(page_number=page_number, text=cleaned))
        except (EncryptedPDFError, EmptyPDFError, ExtractionError):
            raise
        except Exception as exc:
            message = f"Failed to extract text from PDF '{path.name}': {exc}"
            _release_exception_handles(exc)
            del exc
            gc.collect()
            raise ExtractionError(message) from None

        if not pages:
            raise EmptyPDFError("PDF contains no extractable text.")

        return pages
