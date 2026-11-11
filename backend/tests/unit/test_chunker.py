"""Unit tests for model-aligned overlapping token chunking and cross-page provenance."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
from transformers import AutoTokenizer

import app.services.chunker as chunker_module
from app.core.config import Settings
from app.services.chunker import TokenChunker
from app.services.interfaces import ExtractedPage
from app.services.interfaces import TokenChunker as TokenChunkerProtocol


def _build_token_exact_text(tokenizer: Any, num_tokens: int) -> str:
    """Build text that tokenizes to an exact count of distinct single-token words."""
    # Common WordPiece tokens like numbered lowercase words vary, so verify single-token words.
    vocab_words = ["alpha", "beta", "gamma", "delta", "omega", "vector", "index", "token"]
    words = [vocab_words[i % len(vocab_words)] for i in range(num_tokens)]
    text = " ".join(words)
    assert len(tokenizer.encode(text, add_special_tokens=False)) == num_tokens
    return text


def test_token_chunker_implements_protocol_without_tiktoken(test_settings: Settings) -> None:
    """TokenChunker satisfies TokenChunker protocol and does not depend on tiktoken."""
    chunker = TokenChunker(test_settings)
    assert isinstance(chunker, TokenChunkerProtocol)
    assert "tiktoken" not in sys.modules
    assert "pymupdf" not in chunker_module.__dict__


def test_token_chunker_local_cache_fallback(
    test_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AutoTokenizer loading falls back to local_files_only=True when online load fails."""
    real_from_pretrained = AutoTokenizer.from_pretrained
    calls: list[dict[str, Any]] = []

    def fake_from_pretrained(model_name: str, **kwargs: Any) -> Any:
        calls.append({"model_name": model_name, **kwargs})
        if not kwargs.get("local_files_only"):
            raise OSError("Simulated offline environment")
        return real_from_pretrained(model_name, **kwargs)

    monkeypatch.setattr(chunker_module.AutoTokenizer, "from_pretrained", fake_from_pretrained)

    chunker = TokenChunker(test_settings)
    assert len(calls) == 2
    assert calls[0] == {"model_name": test_settings.EMBEDDING_MODEL}
    assert calls[1] == {"model_name": test_settings.EMBEDDING_MODEL, "local_files_only": True}

    chunks = chunker.chunk([ExtractedPage(page_number=1, text="offline fallback check")], "doc-1")
    assert len(chunks) == 1


@pytest.mark.parametrize(
    ("num_tokens", "expected_counts"),
    [
        (1, [1]),
        (200, [200]),
        (201, [200, 41]),
        (360, [200, 200]),
        (361, [200, 200, 41]),
    ],
)
def test_token_chunker_exact_200_40_overlap_boundaries(
    test_settings: Settings,
    num_tokens: int,
    expected_counts: list[int],
) -> None:
    """Verify exact window counts and 40-token overlap on 1, 200, 201, 360, and 361 tokens."""
    tokenizer = AutoTokenizer.from_pretrained(test_settings.EMBEDDING_MODEL)
    text = _build_token_exact_text(tokenizer, num_tokens)
    chunker = TokenChunker(test_settings, tokenizer=tokenizer)

    chunks = chunker.chunk([ExtractedPage(page_number=1, text=text)], document_id="doc-bounds")

    assert [chunk.chunk_index for chunk in chunks] == list(range(len(expected_counts)))
    assert [chunk.token_count for chunk in chunks] == expected_counts

    for chunk in chunks:
        assert chunk.text == text[chunk.char_start : chunk.char_end]
        assert chunk.content_preview
        assert (
            len(tokenizer.encode(chunk.text, add_special_tokens=True))
            <= test_settings.MODEL_MAX_SEQ_LENGTH
        )

    # Verify exact 40-token overlap between consecutive chunks.
    for prev_chunk, next_chunk in zip(chunks, chunks[1:], strict=False):
        prev_ids = tokenizer.encode(prev_chunk.text, add_special_tokens=False)
        next_ids = tokenizer.encode(next_chunk.text, add_special_tokens=False)
        assert prev_ids[-test_settings.CHUNK_OVERLAP_TOKENS :] == (
            next_ids[: test_settings.CHUNK_OVERLAP_TOKENS]
        )


def test_token_chunker_enforces_256_special_token_budget_on_adversarial_inputs(
    tmp_path: Path,
) -> None:
    """Adversarial unbroken inputs and boundary 254-token configs never exceed 256 total tokens."""
    cfg = Settings(
        _env_file=None,
        DATABASE_URL=f"sqlite:///{(tmp_path / 'db.db').as_posix()}",
        CHROMA_PERSIST_DIR=str(tmp_path / "chroma"),
        UPLOAD_DIR=str(tmp_path / "uploads"),
        CHUNK_SIZE_TOKENS=254,
        CHUNK_OVERLAP_TOKENS=40,
        MODEL_MAX_SEQ_LENGTH=256,
    )
    tokenizer = AutoTokenizer.from_pretrained(cfg.EMBEDDING_MODEL)
    chunker = TokenChunker(cfg, tokenizer=tokenizer)

    adversarial_inputs = [
        "a" * 5000,
        "unbelievable" * 120,
        "-".join(["subwordization"] * 300),
        "x/" * 400 + " " + "y" * 1500,
    ]

    for idx, text in enumerate(adversarial_inputs):
        chunks = chunker.chunk([ExtractedPage(page_number=1, text=text)], document_id=f"adv-{idx}")
        assert chunks
        for chunk in chunks:
            encoded_with_special = tokenizer.encode(chunk.text, add_special_tokens=True)
            assert len(encoded_with_special) <= cfg.MODEL_MAX_SEQ_LENGTH
            assert chunk.token_count > 0
            assert chunk.content_preview


def test_token_chunker_clamps_and_splits_when_retokenization_expands(
    tmp_path: Path,
) -> None:
    """Verify binary-search token clamping and character-split fallback if re-tokenization grows."""
    cfg = Settings(
        _env_file=None,
        DATABASE_URL=f"sqlite:///{(tmp_path / 'db.db').as_posix()}",
        CHROMA_PERSIST_DIR=str(tmp_path / "chroma"),
        UPLOAD_DIR=str(tmp_path / "uploads"),
        CHUNK_SIZE_TOKENS=6,
        CHUNK_OVERLAP_TOKENS=2,
        MODEL_MAX_SEQ_LENGTH=10,
    )

    class ExpandingTokenizer:
        """Fake tokenizer whose isolated slices expand more tokens than full-text tokenization."""

        def __call__(
            self,
            text: str,
            *,
            return_offsets_mapping: bool = False,
            truncation: bool = False,
        ) -> dict[str, Any]:
            del truncation
            # Full-text returns 10-char spans per token; one huge span tests char-split fallback.
            spans = [(0, 0), (0, 20), (20, 24), (24, 28), (28, 32), (32, 36), (36, 40), (0, 0)]
            if return_offsets_mapping:
                return {"offset_mapping": spans}
            return {"input_ids": list(range(len(spans)))}

        def encode(self, text: str, *, add_special_tokens: bool = True) -> list[int]:
            # 1 token per 2 chars on re-tokenization, so the 20-char span produces 10 + 2 > 10.
            n_tokens = max(1, (len(text) + 1) // 2)
            total = n_tokens + (2 if add_special_tokens else 0)
            return list(range(total))

    fake_tok = ExpandingTokenizer()
    chunker = TokenChunker(cfg, tokenizer=fake_tok)
    text = "abcdefghijklmnopqrstuvwxyz0123456789ABCD"
    chunks = chunker.chunk([ExtractedPage(page_number=1, text=text)], document_id="fake-expand")

    assert len(chunks) >= 2
    for chunk in chunks:
        assert len(fake_tok.encode(chunk.text, add_special_tokens=True)) <= cfg.MODEL_MAX_SEQ_LENGTH
        assert chunk.content_preview

    class WindowClampingTokenizer:
        """Tokenizer whose single spans fit, but 6-token slices re-tokenize above max_seq=10."""

        def __call__(
            self,
            text: str,
            *,
            return_offsets_mapping: bool = False,
            truncation: bool = False,
        ) -> dict[str, Any]:
            del truncation
            spans = [(i * 4, (i + 1) * 4) for i in range(8)]
            if return_offsets_mapping:
                return {"offset_mapping": spans}
            return {"input_ids": list(range(len(spans)))}

        def encode(self, text: str, *, add_special_tokens: bool = True) -> list[int]:
            # 1 token per 2 chars: 4-token (16 char) window -> 10 <= 10; 6-token -> 14 > 10.
            n_tokens = max(1, len(text) // 2)
            total = n_tokens + (2 if add_special_tokens else 0)
            return list(range(total))

    clamp_tok = WindowClampingTokenizer()
    clamp_chunker = TokenChunker(cfg, tokenizer=clamp_tok)
    clamp_chunks = clamp_chunker.chunk(
        [ExtractedPage(page_number=1, text="A" * 32)],
        document_id="fake-clamp",
    )
    assert len(clamp_chunks) >= 2
    for chunk in clamp_chunks:
        total_len = len(clamp_tok.encode(chunk.text, add_special_tokens=True))
        assert total_len <= cfg.MODEL_MAX_SEQ_LENGTH
        assert chunk.content_preview


def test_token_chunker_cross_page_provenance_and_skipped_blank_pages(
    test_settings: Settings,
) -> None:
    """Cross-page chunks record exact page_number, page_end, csv, and skip blank pages."""
    tokenizer = AutoTokenizer.from_pretrained(test_settings.EMBEDDING_MODEL)
    page1_text = _build_token_exact_text(tokenizer, 120)
    page3_text = _build_token_exact_text(tokenizer, 120)

    pages = [
        ExtractedPage(page_number=1, text=page1_text),
        ExtractedPage(page_number=2, text="   \n  "),
        ExtractedPage(page_number=3, text=page3_text),
    ]
    combined = f"{page1_text}\n\n{page3_text}"

    chunker = TokenChunker(test_settings, tokenizer=tokenizer)
    chunks = chunker.chunk(pages, document_id="cross-page")

    assert len(chunks) == 2  # 240 tokens -> [0:200] (pages 1,3), [160:240] (page 3)

    assert chunks[0].page_number == 1
    assert chunks[0].page_end == 3
    assert chunks[0].page_numbers_csv == "1,3"
    assert chunks[0].text == combined[chunks[0].char_start : chunks[0].char_end]

    assert chunks[1].page_number == 3
    assert chunks[1].page_end == 3
    assert chunks[1].page_numbers_csv == "3"
    assert chunks[1].text == combined[chunks[1].char_start : chunks[1].char_end]


def test_token_chunker_preserves_untruncated_page_numbers_csv_over_255_chars(
    test_settings: Settings,
) -> None:
    """Chunks spanning >100 short pages preserve full untruncated >255-char page_numbers_csv."""
    pages = [ExtractedPage(page_number=page_num, text="word") for page_num in range(1, 131)]
    chunker = TokenChunker(test_settings)

    chunks = chunker.chunk(pages, document_id="many-pages")

    assert len(chunks) == 1
    chunk = chunks[0]
    expected_csv = ",".join(str(p) for p in range(1, 131))
    assert len(expected_csv) > 255
    assert chunk.page_number == 1
    assert chunk.page_end == 130
    assert chunk.page_numbers_csv == expected_csv
    assert chunk.token_count == 130


def test_token_chunker_empty_or_whitespace_pages_returns_empty(test_settings: Settings) -> None:
    """Empty or whitespace-only page inputs produce an empty chunk list."""
    chunker = TokenChunker(test_settings)
    assert chunker.chunk([], "empty-doc") == []
    assert chunker.chunk([ExtractedPage(page_number=1, text="  \n\t ")], "ws-doc") == []
