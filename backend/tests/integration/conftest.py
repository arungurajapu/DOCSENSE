"""Shared helpers and fixtures for DocSense backend integration tests."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import httpx
import pymupdf
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.exceptions import EmbeddingError


def make_text_pdf_bytes(
    page_texts: list[str] | None = None,
    *,
    blank_pages: int = 0,
) -> bytes:
    """Create a valid PDF payload with optional text pages and drawn blank pages."""
    with pymupdf.open() as doc:
        for text in page_texts or []:
            page = doc.new_page()
            if text:
                page.insert_textbox(pymupdf.Rect(50, 50, 540, 760), text)
        for _ in range(blank_pages):
            page = doc.new_page()
            page.draw_rect(
                pymupdf.Rect(50, 50, 150, 150),
                color=(0, 0, 0),
                fill=(0.5, 0.5, 0.5),
            )
        return doc.tobytes()


def make_scanned_pdf_bytes(page_count: int = 1) -> bytes:
    """Create a valid multi-page image/vector PDF containing zero extractable text."""
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
        page.insert_text((72, 72), "Confidential encrypted content")
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


def make_isolated_settings(tmp_path: Path, **overrides: Any) -> Settings:
    """Build isolated Settings storing DB, ChromaDB, and uploads inside ``tmp_path``."""
    db_file = tmp_path / "db" / "docsense.db"
    params: dict[str, Any] = {
        "_env_file": None,
        "DATABASE_URL": f"sqlite:///{db_file.as_posix()}",
        "CHROMA_PERSIST_DIR": str(tmp_path / "chroma"),
        "UPLOAD_DIR": str(tmp_path / "uploads"),
    }
    params.update(overrides)
    return Settings(**params)


class DeterministicEmbeddingService:
    """Fast deterministic 384-d embedding service for concurrency and recovery tests."""

    def __init__(
        self,
        *,
        loaded: bool = False,
        fail_load: bool = False,
        dim: int = 384,
    ) -> None:
        self._loaded = loaded
        self._fail_load = fail_load
        self._dim = dim

    def load(self) -> None:
        if self._fail_load:
            raise EmbeddingError("Simulated embedding model load failure.")
        self._loaded = True

    def is_loaded(self) -> bool:
        return self._loaded

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        if not self._loaded:
            self.load()
        vectors: list[list[float]] = []
        for idx, _text in enumerate(texts):
            vec = [0.0] * self._dim
            vec[idx % self._dim] = 1.0
            vectors.append(vec)
        return vectors

    async def embed_texts_async(self, texts: Sequence[str]) -> list[list[float]]:
        return await asyncio.to_thread(self.embed_texts, texts)


def poll_until_terminal(
    client: TestClient,
    document_id: str,
    *,
    timeout: float = 15.0,
    interval: float = 0.05,
) -> dict[str, Any]:
    """Synchronously poll GET /api/v1/documents/{id} until status is ready or failed."""
    deadline = time.monotonic() + timeout
    last_payload: dict[str, Any] = {}
    while time.monotonic() < deadline:
        resp = client.get(f"/api/v1/documents/{document_id}")
        assert resp.status_code == 200, f"Unexpected status {resp.status_code}: {resp.text}"
        last_payload = resp.json()
        if last_payload["status"] in {"ready", "failed"}:
            return last_payload
        time.sleep(interval)
    raise AssertionError(
        f"Timed out waiting for document {document_id} to finish; last={last_payload}"
    )


async def poll_until_terminal_async(
    client: httpx.AsyncClient,
    document_id: str,
    *,
    timeout: float = 15.0,
    interval: float = 0.02,
) -> dict[str, Any]:
    """Asynchronously poll GET /api/v1/documents/{id} until status is ready or failed."""
    deadline = time.monotonic() + timeout
    last_payload: dict[str, Any] = {}
    while time.monotonic() < deadline:
        resp = await client.get(f"/api/v1/documents/{document_id}")
        assert resp.status_code == 200, f"Unexpected status {resp.status_code}: {resp.text}"
        last_payload = resp.json()
        if last_payload["status"] in {"ready", "failed"}:
            return last_payload
        await asyncio.sleep(interval)
    raise AssertionError(
        f"Timed out waiting for document {document_id} to finish; last={last_payload}"
    )
