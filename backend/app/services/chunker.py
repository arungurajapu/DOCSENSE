"""Model-aligned overlapping token chunker with cross-page provenance."""

from collections.abc import Sequence
from typing import Any

from transformers import AutoTokenizer

from app.core.config import Settings, get_settings
from app.services.interfaces import ExtractedPage, TextChunk

CONTENT_PREVIEW_MAX_CHARS = 200
PAGE_SEPARATOR = "\n\n"


def _load_tokenizer(model_name: str) -> Any:
    """Load the HuggingFace tokenizer with offline local-cache fallback."""
    try:
        return AutoTokenizer.from_pretrained(model_name)
    except Exception:
        return AutoTokenizer.from_pretrained(model_name, local_files_only=True)


def _build_content_preview(text: str) -> str:
    """Create a normalized, non-empty snippet preview for chunk provenance."""
    normalized = " ".join(text.split())
    if not normalized:
        normalized = text[:CONTENT_PREVIEW_MAX_CHARS]
    if len(normalized) <= CONTENT_PREVIEW_MAX_CHARS:
        return normalized
    return f"{normalized[: CONTENT_PREVIEW_MAX_CHARS - 3].rstrip()}..."


def _resolve_page_provenance(
    page_spans: Sequence[tuple[int, int, int]],
    char_start: int,
    char_end: int,
) -> tuple[int, int, str]:
    """Compute first page, last page, and CSV page provenance for [char_start, char_end)."""
    overlapped = list(
        dict.fromkeys(
            page_num
            for p_start, p_end, page_num in page_spans
            if p_start < char_end and char_start < p_end
        )
    )
    if not overlapped:
        # Fallback to nearest page if a span ever sits solely on separator boundaries.
        nearest_page = min(
            page_spans,
            key=lambda span: min(abs(span[0] - char_start), abs(span[1] - char_start)),
        )[2]
        overlapped = [nearest_page]

    return overlapped[0], overlapped[-1], ",".join(str(p) for p in overlapped)


class TokenChunker:
    """WordPiece token-window chunker aligned with the configured embedding model."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        tokenizer: Any | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._tokenizer = (
            tokenizer
            if tokenizer is not None
            else _load_tokenizer(self._settings.EMBEDDING_MODEL)
        )

    def _encoded_length_with_special_tokens(self, text: str) -> int:
        return len(self._tokenizer.encode(text, add_special_tokens=True))

    def _content_token_count(self, text: str) -> int:
        return len(self._tokenizer.encode(text, add_special_tokens=False))

    def _fit_token_window_end(
        self,
        combined_text: str,
        token_spans: Sequence[tuple[int, int]],
        start_idx: int,
        end_idx: int,
        max_seq_len: int,
    ) -> int | None:
        """Binary search largest end index in [start_idx + 1, end_idx) fitting max_seq_len."""
        char_start = token_spans[start_idx][0]
        low = start_idx + 1
        high = end_idx - 1
        best: int | None = None

        while low <= high:
            mid = (low + high) // 2
            candidate = combined_text[char_start : token_spans[mid - 1][1]]
            if self._encoded_length_with_special_tokens(candidate) <= max_seq_len:
                best = mid
                low = mid + 1
            else:
                high = mid - 1

        return best

    def _fit_char_slice_end(
        self,
        combined_text: str,
        char_start: int,
        char_limit: int,
        max_seq_len: int,
    ) -> int:
        """Binary search largest character slice end in (char_start, char_limit] fitting budget."""
        low = char_start + 1
        high = char_limit
        best = char_start + 1

        while low <= high:
            mid = (low + high) // 2
            candidate = combined_text[char_start:mid]
            if self._encoded_length_with_special_tokens(candidate) <= max_seq_len:
                best = mid
                low = mid + 1
            else:
                high = mid - 1

        return best

    def _make_chunk(
        self,
        chunk_index: int,
        chunk_text: str,
        char_start: int,
        char_end: int,
        page_spans: Sequence[tuple[int, int, int]],
    ) -> TextChunk:
        page_number, page_end, page_numbers_csv = _resolve_page_provenance(
            page_spans=page_spans,
            char_start=char_start,
            char_end=char_end,
        )
        return TextChunk(
            chunk_index=chunk_index,
            text=chunk_text,
            page_number=page_number,
            page_end=page_end,
            page_numbers_csv=page_numbers_csv,
            token_count=self._content_token_count(chunk_text),
            char_start=char_start,
            char_end=char_end,
            content_preview=_build_content_preview(chunk_text),
        )

    def chunk(self, pages: Sequence[ExtractedPage], document_id: str) -> list[TextChunk]:
        """Split extracted pages into overlapping model-bounded chunks with page provenance."""
        del document_id  # Reserved for caller correlation/logging; provenance uses page spans.

        parts: list[str] = []
        page_spans: list[tuple[int, int, int]] = []
        cursor = 0

        for page in pages:
            if not page.text or not page.text.strip():
                continue
            if parts:
                cursor += len(PAGE_SEPARATOR)
            start = cursor
            end = start + len(page.text)
            parts.append(page.text)
            page_spans.append((start, end, page.page_number))
            cursor = end

        if not parts:
            return []

        combined_text = PAGE_SEPARATOR.join(parts)
        encoded = self._tokenizer(
            combined_text,
            return_offsets_mapping=True,
            truncation=False,
        )
        raw_offsets = encoded["offset_mapping"]
        token_spans: list[tuple[int, int]] = [
            (int(span_start), int(span_end))
            for span_start, span_end in raw_offsets
            if int(span_end) > int(span_start)
        ]

        if not token_spans:
            return []

        chunk_size = self._settings.CHUNK_SIZE_TOKENS
        overlap = self._settings.CHUNK_OVERLAP_TOKENS
        stride = chunk_size - overlap
        max_seq_len = self._settings.MODEL_MAX_SEQ_LENGTH

        chunks: list[TextChunk] = []
        n_tokens = len(token_spans)
        start_idx = 0

        while start_idx < n_tokens:
            end_idx = min(start_idx + chunk_size, n_tokens)
            char_start = token_spans[start_idx][0]
            char_end = token_spans[end_idx - 1][1]
            chunk_text = combined_text[char_start:char_end]

            if self._encoded_length_with_special_tokens(chunk_text) <= max_seq_len:
                if chunk_text.strip():
                    chunks.append(
                        self._make_chunk(
                            chunk_index=len(chunks),
                            chunk_text=chunk_text,
                            char_start=char_start,
                            char_end=char_end,
                            page_spans=page_spans,
                        )
                    )
                if end_idx >= n_tokens:
                    break
                start_idx += stride
                continue

            effective_end = self._fit_token_window_end(
                combined_text=combined_text,
                token_spans=token_spans,
                start_idx=start_idx,
                end_idx=end_idx,
                max_seq_len=max_seq_len,
            )
            if effective_end is not None:
                char_end = token_spans[effective_end - 1][1]
                chunk_text = combined_text[char_start:char_end]
                if chunk_text.strip():
                    chunks.append(
                        self._make_chunk(
                            chunk_index=len(chunks),
                            chunk_text=chunk_text,
                            char_start=char_start,
                            char_end=char_end,
                            page_spans=page_spans,
                        )
                    )
                if effective_end >= n_tokens:
                    break
                start_idx = max(start_idx + 1, effective_end - overlap)
                continue

            # Even a single token span exceeded max_seq_len upon re-tokenization; split by chars.
            span_start, span_end = token_spans[start_idx]
            char_cursor = span_start
            while char_cursor < span_end:
                sub_end = self._fit_char_slice_end(
                    combined_text=combined_text,
                    char_start=char_cursor,
                    char_limit=span_end,
                    max_seq_len=max_seq_len,
                )
                sub_text = combined_text[char_cursor:sub_end]
                if sub_text.strip():
                    chunks.append(
                        self._make_chunk(
                            chunk_index=len(chunks),
                            chunk_text=sub_text,
                            char_start=char_cursor,
                            char_end=sub_end,
                            page_spans=page_spans,
                        )
                    )
                char_cursor = sub_end

            start_idx += 1

        return chunks
