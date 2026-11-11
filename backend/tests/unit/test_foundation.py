"""Foundation architecture, SQLite pragma/WAL/cascade, OpenAPI, and Windows compatibility tests."""

from __future__ import annotations

import gc
import importlib.metadata as importlib_metadata
import json
import os
import shutil
import sys
import uuid
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pydantic
import pymupdf
import pytest
import sqlalchemy
import torch
from fastapi import UploadFile
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from app.core.config import BACKEND_DIR, Settings
from app.core.exceptions import (
    BadRequestError,
    DocSenseError,
    DocumentConflictError,
    DocumentNotFoundError,
    EmptyPDFError,
    EncryptedPDFError,
    FileTooLargeError,
    ServiceUnavailableError,
    UnsupportedMediaTypeError,
)
from app.db.session import create_db_engine, create_session_factory, init_db
from app.models.document import Document, DocumentChunk, DocumentStatus
from app.schemas import (
    DocumentListResponse,
    DocumentResponse,
    ErrorDetail,
    ErrorResponse,
    HealthResponse,
)
from app.services.interfaces import (
    EmbeddingService,
    ExtractedPage,
    PDFExtractor,
    SavedFileResult,
    StorageService,
    TextChunk,
    TokenChunker,
    VectorStore,
)
from scripts.export_openapi import DEFAULT_OUTPUT_PATH, export_openapi

# ---------------------------------------------------------------------------
# 1. SQLite Connection Pragma, WAL Initialization, UTC, and ON DELETE CASCADE
# ---------------------------------------------------------------------------


def test_sqlite_multi_connection_pragmas_and_no_wal_in_connect_listener(tmp_path: Path) -> None:
    """Verify >=2 distinct SQLite connections set foreign_keys=1 & busy_timeout=5000 only."""
    db_path = tmp_path / "pragmas_multi.db"
    engine = create_db_engine(f"sqlite:///{db_path.as_posix()}")
    try:
        # Hold two distinct connections open concurrently BEFORE calling init_db().
        with engine.connect() as conn1, engine.connect() as conn2:
            raw_conn1 = conn1.connection.dbapi_connection
            raw_conn2 = conn2.connection.dbapi_connection
            assert raw_conn1 is not raw_conn2, "Expected two distinct DBAPI SQLite connections"

            for idx, conn in enumerate((conn1, conn2), start=1):
                fk = conn.execute(text("PRAGMA foreign_keys;")).scalar_one()
                busy = conn.execute(text("PRAGMA busy_timeout;")).scalar_one()
                journal = str(conn.execute(text("PRAGMA journal_mode;")).scalar_one()).lower()

                assert fk == 1, f"Connection {idx}: expected foreign_keys=1, got {fk}"
                assert busy == 5000, f"Connection {idx}: expected busy_timeout=5000, got {busy}"
                # The connect listener must NOT enable WAL mode automatically.
                assert journal != "wal", (
                    f"Connection {idx}: connect listener unexpectedly set journal_mode={journal}"
                )
    finally:
        engine.dispose()


def test_init_db_sets_wal_once_on_file_sqlite_and_skips_memory(tmp_path: Path) -> None:
    """Verify init_db sets WAL on file-backed SQLite and >=2 new connections preserve pragmas."""
    db_path = tmp_path / "nested" / "wal_init.db"
    file_engine = create_db_engine(f"sqlite:///{db_path.as_posix()}")
    try:
        mode = init_db(file_engine)
        assert mode == "wal"

        table_names = set(inspect(file_engine).get_table_names())
        assert {"documents", "document_chunks"}.issubset(table_names)

        # Open >=2 distinct connections AFTER init_db() and verify WAL + per-connection pragmas.
        with file_engine.connect() as conn1, file_engine.connect() as conn2:
            assert conn1.connection.dbapi_connection is not conn2.connection.dbapi_connection
            for idx, conn in enumerate((conn1, conn2), start=1):
                journal_mode = str(conn.execute(text("PRAGMA journal_mode;")).scalar_one()).lower()
                fk = conn.execute(text("PRAGMA foreign_keys;")).scalar_one()
                busy = conn.execute(text("PRAGMA busy_timeout;")).scalar_one()
                assert journal_mode == "wal", f"Connection {idx}: expected wal, got {journal_mode}"
                assert fk == 1, f"Connection {idx}: expected foreign_keys=1, got {fk}"
                assert busy == 5000, f"Connection {idx}: expected busy_timeout=5000, got {busy}"
    finally:
        file_engine.dispose()

    memory_engine = create_db_engine("sqlite:///:memory:")
    try:
        mem_mode = init_db(memory_engine)
        assert mem_mode is None
        mem_tables = set(inspect(memory_engine).get_table_names())
        assert {"documents", "document_chunks"}.issubset(mem_tables)
    finally:
        memory_engine.dispose()


def test_utc_datetime_persistence_and_pydantic_iso8601_serialization(tmp_path: Path) -> None:
    """Verify UTCDateTime persists/restores UTC across SQLite sessions and serializes ISO UTC."""
    db_path = tmp_path / "utc_roundtrip.db"
    engine = create_db_engine(f"sqlite:///{db_path.as_posix()}")
    init_db(engine)

    ist_tz = timezone(timedelta(hours=5, minutes=30))
    created_ist = datetime(2026, 11, 11, 16, 0, 0, tzinfo=ist_tz)
    expected_utc = datetime(2026, 11, 11, 10, 30, 0, tzinfo=UTC)
    doc_id = str(uuid.uuid4())
    session_factory = create_session_factory(engine)

    try:
        session = session_factory()
        try:
            session.add(
                Document(
                    id=doc_id,
                    filename="utc_test.pdf",
                    content_type="application/pdf",
                    file_size_bytes=512,
                    sha256_hash="f" * 64,
                    storage_path=f"data/uploads/{doc_id}.pdf",
                    status=DocumentStatus.PENDING.value,
                    created_at=created_ist,
                    updated_at=created_ist,
                )
            )
            session.commit()
        finally:
            session.close()

        # Reload in a brand-new SQLAlchemy session to verify SQLite read-path UTC restoration.
        with session_factory() as reload_session:
            reloaded = reload_session.get(Document, doc_id)
            assert reloaded is not None
            assert reloaded.created_at.tzinfo is UTC
            assert reloaded.updated_at.tzinfo is UTC
            assert reloaded.created_at == expected_utc
            assert reloaded.updated_at == expected_utc

            doc_schema = DocumentResponse.model_validate(reloaded)
            assert doc_schema.id == uuid.UUID(doc_id)
            assert doc_schema.created_at.tzinfo is UTC
            assert doc_schema.updated_at.tzinfo is UTC

            serialized = json.loads(doc_schema.model_dump_json())
            assert serialized["created_at"].endswith(("Z", "+00:00"))
            assert serialized["updated_at"].endswith(("Z", "+00:00"))
            assert datetime.fromisoformat(serialized["created_at"]) == expected_utc

        # Verify HealthResponse also normalizes naive and non-UTC datetimes to explicit UTC.
        naive_dt = datetime(2026, 11, 11, 10, 30, 0)
        health = HealthResponse(
            status="ok",
            version="0.1.0",
            database="ok",
            vector_store="ok",
            embedding_model_loaded=True,
            timestamp=naive_dt,
        )
        assert health.timestamp.tzinfo is UTC
        health_json = json.loads(health.model_dump_json())
        assert health_json["timestamp"].endswith(("Z", "+00:00"))
    finally:
        engine.dispose()


def test_raw_sql_delete_document_cascades_to_chunks(tmp_path: Path) -> None:
    """Verify raw SQL DELETE FROM documents cascades and page_numbers_csv stores >255 chars."""
    db_path = tmp_path / "cascade.db"
    engine = create_db_engine(f"sqlite:///{db_path.as_posix()}")
    init_db(engine)

    doc_id = str(uuid.uuid4())
    long_pages_csv = ",".join(str(p) for p in range(1, 120))  # > 255 chars
    assert len(long_pages_csv) > 255

    session_factory = create_session_factory(engine)
    try:
        with session_factory() as session:
            doc = Document(
                id=doc_id,
                filename="sample.pdf",
                content_type="application/pdf",
                file_size_bytes=1024,
                sha256_hash="a" * 64,
                storage_path=f"data/uploads/{doc_id}.pdf",
                status=DocumentStatus.READY.value,
                page_count=120,
                chunk_count=2,
            )
            session.add(doc)
            session.flush()

            session.add_all(
                [
                    DocumentChunk(
                        document_id=doc_id,
                        chunk_index=0,
                        page_number=1,
                        page_end=1,
                        page_numbers_csv="1",
                        token_count=120,
                        char_start=0,
                        char_end=450,
                        content_preview="First chunk preview",
                        chroma_id=f"{doc_id}:0",
                    ),
                    DocumentChunk(
                        document_id=doc_id,
                        chunk_index=1,
                        page_number=1,
                        page_end=119,
                        page_numbers_csv=long_pages_csv,
                        token_count=140,
                        char_start=350,
                        char_end=900,
                        content_preview="Second cross-page chunk preview",
                        chroma_id=f"{doc_id}:1",
                    ),
                ]
            )
            session.commit()

        # Execute raw SQL DELETE directly on documents (bypassing ORM cascade).
        with engine.begin() as conn:
            before_count = conn.execute(
                text("SELECT COUNT(*) FROM document_chunks WHERE document_id = :id"),
                {"id": doc_id},
            ).scalar_one()
            assert before_count == 2

            deleted = conn.execute(
                text("DELETE FROM documents WHERE id = :id"),
                {"id": doc_id},
            )
            assert deleted.rowcount == 1

            after_count = conn.execute(
                text("SELECT COUNT(*) FROM document_chunks WHERE document_id = :id"),
                {"id": doc_id},
            ).scalar_one()
            assert after_count == 0
    finally:
        engine.dispose()


def test_document_status_check_constraint_and_chroma_id_uniqueness(
    sqlite_file_engine: Any,
) -> None:
    """Verify DB constraints reject invalid document status and duplicate chroma_id."""
    session_factory = create_session_factory(sqlite_file_engine)
    with session_factory() as session:
        bad_doc = Document(
            filename="bad.pdf",
            content_type="application/pdf",
            file_size_bytes=10,
            sha256_hash="b" * 64,
            storage_path="data/uploads/bad.pdf",
            status="invalid_status",
        )
        session.add(bad_doc)
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

    doc_id = str(uuid.uuid4())
    with session_factory() as session:
        doc = Document(
            id=doc_id,
            filename="dup.pdf",
            content_type="application/pdf",
            file_size_bytes=512,
            sha256_hash="c" * 64,
            storage_path=f"data/uploads/{doc_id}.pdf",
            status=DocumentStatus.PENDING.value,
        )
        session.add(doc)
        session.flush()
        session.add(
            DocumentChunk(
                document_id=doc_id,
                chunk_index=0,
                page_number=1,
                page_end=1,
                page_numbers_csv="1",
                token_count=10,
                char_start=0,
                char_end=20,
                content_preview="dup",
                chroma_id="shared-chroma-id",
            )
        )
        session.commit()

        session.add(
            DocumentChunk(
                document_id=doc_id,
                chunk_index=1,
                page_number=1,
                page_end=1,
                page_numbers_csv="1",
                token_count=10,
                char_start=20,
                char_end=40,
                content_preview="dup2",
                chroma_id="shared-chroma-id",
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()


# ---------------------------------------------------------------------------
# 2. Config Bounds, CWD Independence, Exceptions, Schemas, and Protocols
# ---------------------------------------------------------------------------


def test_config_defaults_and_token_budget_validation() -> None:
    """Verify default settings and strict overlap/special-token bounds."""
    cfg = Settings(_env_file=None)
    assert cfg.EMBEDDING_MODEL == "sentence-transformers/all-MiniLM-L6-v2"
    assert cfg.CHUNK_SIZE_TOKENS == 200
    assert cfg.CHUNK_OVERLAP_TOKENS == 40
    assert cfg.MODEL_MAX_SEQ_LENGTH == 256
    assert cfg.MAX_UPLOAD_SIZE_MB == 25
    assert cfg.max_upload_size_bytes == 25 * 1024 * 1024

    # Exact boundary (254 + 2 == 256) succeeds.
    boundary_cfg = Settings(
        _env_file=None,
        CHUNK_SIZE_TOKENS=254,
        CHUNK_OVERLAP_TOKENS=40,
        MODEL_MAX_SEQ_LENGTH=256,
    )
    assert boundary_cfg.CHUNK_SIZE_TOKENS == 254

    # Overlap >= chunk size fails.
    with pytest.raises(PydanticValidationError):
        Settings(_env_file=None, CHUNK_SIZE_TOKENS=200, CHUNK_OVERLAP_TOKENS=200)

    # Negative overlap fails.
    with pytest.raises(PydanticValidationError):
        Settings(_env_file=None, CHUNK_SIZE_TOKENS=200, CHUNK_OVERLAP_TOKENS=-1)

    # Chunk size + 2 special tokens > model max_seq_length fails (255 + 2 = 257 > 256).
    with pytest.raises(PydanticValidationError):
        Settings(
            _env_file=None,
            CHUNK_SIZE_TOKENS=255,
            CHUNK_OVERLAP_TOKENS=40,
            MODEL_MAX_SEQ_LENGTH=256,
        )

    # Comma-separated CORS origins parse cleanly.
    cors_cfg = Settings(
        _env_file=None,
        CORS_ORIGINS="http://localhost:3000, https://app.example.com",
    )
    assert cors_cfg.CORS_ORIGINS == ["http://localhost:3000", "https://app.example.com"]


def test_config_cwd_independent_paths_and_env_isolation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    test_settings: Settings,
) -> None:
    """Verify relative data paths resolve to BACKEND_DIR regardless of CWD and isolate .env."""
    outside_dir = tmp_path / "outside_cwd"
    outside_dir.mkdir()
    (outside_dir / ".env").write_text(
        "MAX_UPLOAD_SIZE_MB=99\nUPLOAD_DIR=./custom_uploads\n",
        encoding="utf-8",
    )

    monkeypatch.chdir(outside_dir)

    # Default Settings with _env_file=None still resolves relative paths to BACKEND_DIR.
    cfg = Settings(_env_file=None)
    assert cfg.UPLOAD_DIR == str((BACKEND_DIR / "data" / "uploads").resolve())
    assert cfg.CHROMA_PERSIST_DIR == str((BACKEND_DIR / "data" / "chroma").resolve())
    expected_db_url = f"sqlite:///{(BACKEND_DIR / 'data' / 'docsense.db').resolve().as_posix()}"
    assert cfg.DATABASE_URL == expected_db_url
    assert Settings(_env_file=None, DATABASE_URL="sqlite:///:memory:").DATABASE_URL == (
        "sqlite:///:memory:"
    )

    # Fixture test_settings must remain isolated from any external .env file.
    assert test_settings.MAX_UPLOAD_SIZE_MB == 25
    assert test_settings.UPLOAD_DIR == str(tmp_path / "uploads")

    # Explicit .env loading resolves relative overrides against BACKEND_DIR too.
    loaded_cfg = Settings(_env_file=outside_dir / ".env")
    assert loaded_cfg.MAX_UPLOAD_SIZE_MB == 99
    assert loaded_cfg.UPLOAD_DIR == str((BACKEND_DIR / "custom_uploads").resolve())


def test_exceptions_and_pydantic_v2_schemas() -> None:
    """Verify domain exception payloads, required DocumentResponse keys, and UUID coercion."""
    cases: list[tuple[DocSenseError, int, str]] = [
        (BadRequestError("Empty filename"), 400, "BAD_REQUEST"),
        (DocumentNotFoundError("Missing doc"), 404, "DOCUMENT_NOT_FOUND"),
        (DocumentConflictError("Cannot reprocess ready document"), 409, "DOCUMENT_CONFLICT"),
        (FileTooLargeError("Too large"), 413, "FILE_TOO_LARGE"),
        (UnsupportedMediaTypeError("Not a PDF"), 415, "UNSUPPORTED_MEDIA_TYPE"),
        (EncryptedPDFError("Encrypted"), 422, "ENCRYPTED_PDF_NOT_SUPPORTED"),
        (EmptyPDFError("No text"), 422, "EMPTY_PDF_NOT_SUPPORTED"),
        (ServiceUnavailableError("DB down"), 503, "SERVICE_UNAVAILABLE"),
    ]
    for exc, expected_status, expected_code in cases:
        assert exc.status_code == expected_status
        assert exc.code == expected_code
        validated = ErrorResponse.model_validate(exc.to_error_payload())
        assert validated.error == ErrorDetail(code=expected_code, message=exc.message)

    now = datetime.now(UTC)
    doc_uuid = uuid.uuid4()
    orm_doc = Document(
        id=str(doc_uuid),
        filename="report.pdf",
        content_type="application/pdf",
        file_size_bytes=2048,
        sha256_hash="d" * 64,
        storage_path="data/uploads/report.pdf",
        status=DocumentStatus.READY.value,
        error_message=None,
        page_count=3,
        chunk_count=5,
        created_at=now,
        updated_at=now,
    )
    doc_schema = DocumentResponse.model_validate(orm_doc)
    assert doc_schema.id == doc_uuid
    assert doc_schema.status == DocumentStatus.READY
    assert doc_schema.chunk_count == 5

    # error_message, page_count, and chunk_count must be required keys in DocumentResponse.
    with pytest.raises(PydanticValidationError):
        DocumentResponse.model_validate(
            {
                "id": str(doc_uuid),
                "filename": "report.pdf",
                "content_type": "application/pdf",
                "file_size_bytes": 2048,
                "sha256_hash": "d" * 64,
                "status": "pending",
                "created_at": now,
                "updated_at": now,
            }
        )

    doc_list = DocumentListResponse(items=[doc_schema], total=1, limit=20, offset=0)
    assert len(doc_list.items) == 1


def test_runtime_checkable_service_protocols() -> None:
    """Verify service DTOs and runtime-checkable protocols work as contracts."""

    class FakeStorage:
        async def save_validated_pdf(
            self,
            upload_file: UploadFile,
            document_id: str,
        ) -> SavedFileResult:
            return SavedFileResult(
                filename="sample.pdf",
                storage_path=f"data/uploads/{document_id}.pdf",
                sha256_hash="e" * 64,
                file_size_bytes=10,
                content_type="application/pdf",
                page_count=1,
            )

        def delete_file(self, storage_path: str) -> None:
            return None

        def resolve_path(self, storage_path: str) -> Path:
            return Path(storage_path)

    class FakeExtractor:
        def extract(self, file_path: str | Path) -> list[ExtractedPage]:
            return [ExtractedPage(page_number=1, text="hello")]

    class FakeChunker:
        def chunk(self, pages: list[ExtractedPage], document_id: str) -> list[TextChunk]:
            return [
                TextChunk(
                    chunk_index=0,
                    text="hello",
                    page_number=1,
                    page_end=1,
                    page_numbers_csv="1",
                    token_count=1,
                    char_start=0,
                    char_end=5,
                    content_preview="hello",
                )
            ]

    class FakeEmbeddings:
        def embed_texts(self, texts: list[str]) -> list[list[float]]:
            return [[0.0] * 384 for _ in texts]

        async def embed_texts_async(self, texts: list[str]) -> list[list[float]]:
            return self.embed_texts(texts)

        def is_loaded(self) -> bool:
            return True

    class FakeVectorStore:
        def __init__(self) -> None:
            self.closed = False

        def upsert_chunks(
            self,
            document_id: str,
            filename: str,
            chunks: list[TextChunk],
            embeddings: list[list[float]],
        ) -> None:
            return None

        def delete_document(self, document_id: str) -> None:
            return None

        def count_by_document(self, document_id: str) -> int:
            return 0

        def health_check(self) -> bool:
            return not self.closed

        def close(self) -> None:
            self.closed = True

    vector_store = FakeVectorStore()
    assert isinstance(FakeStorage(), StorageService)
    assert isinstance(FakeExtractor(), PDFExtractor)
    assert isinstance(FakeChunker(), TokenChunker)
    assert isinstance(FakeEmbeddings(), EmbeddingService)
    assert isinstance(vector_store, VectorStore)
    vector_store.close()
    assert vector_store.closed is True


# ---------------------------------------------------------------------------
# 3. OpenAPI Contract Export & Parity Validation
# ---------------------------------------------------------------------------


def test_openapi_contract_export_and_committed_sync(tmp_path: Path) -> None:
    """Verify export_openapi generates valid contract matching contracts/openapi.json."""
    exported_path = tmp_path / "openapi.json"
    schema = export_openapi(exported_path)

    assert exported_path.exists()
    assert DEFAULT_OUTPUT_PATH.exists(), (
        "Committed contracts/openapi.json is missing; run python scripts/export_openapi.py"
    )
    committed_schema = json.loads(DEFAULT_OUTPUT_PATH.read_text(encoding="utf-8"))
    assert schema == committed_schema

    component_schemas = schema["components"]["schemas"]
    assert "HTTPValidationError" not in component_schemas
    assert "ValidationError" not in component_schemas
    expected_schemas = {
        "ErrorResponse",
        "ErrorDetail",
        "HealthResponse",
        "DocumentResponse",
        "DocumentListResponse",
        "DocumentStatus",
    }
    assert expected_schemas.issubset(component_schemas.keys())

    # DocumentResponse must require all non-optional response keys and use format: uuid for id.
    doc_schema = component_schemas["DocumentResponse"]
    assert doc_schema["properties"]["id"]["format"] == "uuid"
    assert set(doc_schema["required"]) == {
        "id",
        "filename",
        "content_type",
        "file_size_bytes",
        "sha256_hash",
        "status",
        "error_message",
        "page_count",
        "chunk_count",
        "created_at",
        "updated_at",
    }

    expected_endpoints: dict[tuple[str, str], tuple[str, set[str]]] = {
        ("/api/v1/health", "get"): ("200", {"503"}),
        ("/api/v1/documents", "post"): ("202", {"400", "413", "415", "422"}),
        ("/api/v1/documents", "get"): ("200", {"422"}),
        ("/api/v1/documents/{document_id}", "get"): ("200", {"404", "422"}),
        ("/api/v1/documents/{document_id}/reprocess", "post"): ("202", {"404", "409", "422"}),
        ("/api/v1/documents/{document_id}", "delete"): ("204", {"404", "409", "422"}),
    }

    paths = schema["paths"]
    assert set(paths.keys()) == {path for path, _ in expected_endpoints}

    for (path, method), (ok_code, error_codes) in expected_endpoints.items():
        operation = paths[path][method]
        if "{document_id}" in path:
            path_params = [
                param
                for param in operation.get("parameters", [])
                if param.get("in") == "path" and param.get("name") == "document_id"
            ]
            assert len(path_params) == 1
            assert path_params[0]["schema"]["type"] == "string"
            assert path_params[0]["schema"]["format"] == "uuid"

        responses = operation["responses"]
        assert ok_code in responses
        for err_code in error_codes:
            assert err_code in responses
            err_schema = responses[err_code]["content"]["application/json"]["schema"]
            assert err_schema == {"$ref": "#/components/schemas/ErrorResponse"}


# ---------------------------------------------------------------------------
# 4. Windows Python 3.12 + 6-Package Compatibility Gate Verification
# ---------------------------------------------------------------------------


def test_gate_python_and_core_versions() -> None:
    """Gate 1 & Gate 3: Confirm Python 3.12.x (>3.12.0), Pydantic 2.x, SQLAlchemy 2.x."""
    assert sys.version_info[:2] == (3, 12)
    assert sys.version_info > (3, 12, 0)
    assert pydantic.__version__.startswith("2.")
    assert sqlalchemy.__version__.startswith("2.")


def test_gate_pymupdf_extract_close_and_immediate_delete(tmp_path: Path) -> None:
    """Gate 4: PyMuPDF opens via context manager, reads text, closes, and removes cleanly."""
    pdf_path = tmp_path / "windows_lock_check.pdf"
    sample_text = "DocSense Windows PyMuPDF handle release verification"

    with pymupdf.open() as new_doc:
        page = new_doc.new_page()
        page.insert_text((72, 72), sample_text)
        new_doc.save(str(pdf_path))

    assert pdf_path.exists()

    with pymupdf.open(str(pdf_path)) as read_doc:
        assert len(read_doc) == 1
        extracted = read_doc[0].get_text()
        assert sample_text in extracted

    # Must succeed immediately on Windows NTFS without WinError 32.
    os.remove(pdf_path)
    assert not pdf_path.exists()


def test_gate_torch_tokenizer_and_sentence_transformer() -> None:
    """Gate 5: CPU-only PyTorch, special-token budget (200/254<=256), and (384,) embeddings."""
    from sentence_transformers import SentenceTransformer
    from transformers import AutoTokenizer

    # Assert CPU-only PyTorch and confirm zero nvidia-* CUDA packages are installed.
    assert not torch.cuda.is_available()
    assert "+cpu" in torch.__version__
    installed_names = {
        dist.metadata["Name"].lower()
        for dist in importlib_metadata.distributions()
        if dist.metadata.get("Name")
    }
    nvidia_pkgs = sorted(name for name in installed_names if name.startswith("nvidia-"))
    assert not nvidia_pkgs, f"Unexpected CUDA/NVIDIA packages installed: {nvidia_pkgs}"

    tensor = torch.tensor([[1.0, 2.0], [3.0, 4.0]], dtype=torch.float32)
    product = tensor @ tensor.T
    assert product.shape == (2, 2)
    # [[1, 2], [3, 4]] @ [[1, 3], [2, 4]] == [[5, 11], [11, 25]], sum == 52.0
    assert float(product.sum().item()) == pytest.approx(52.0)

    model_name = "sentence-transformers/all-MiniLM-L6-v2"
    tokenizer = AutoTokenizer.from_pretrained(model_name)
    assert tokenizer.num_special_tokens_to_add(pair=False) == 2

    encoded = tokenizer(
        "DocSense verifies token budgeting.",
        return_offsets_mapping=True,
        truncation=False,
    )
    assert len(encoded["input_ids"]) >= 4
    assert len(encoded["offset_mapping"]) == len(encoded["input_ids"])

    model = SentenceTransformer(model_name, device="cpu")
    assert model.max_seq_length == 256

    assert len(tokenizer.encode("word", add_special_tokens=False)) == 1
    ids_200 = tokenizer.encode(" ".join(["word"] * 200), add_special_tokens=True)
    ids_254 = tokenizer.encode(" ".join(["word"] * 254), add_special_tokens=True)
    assert len(ids_200) == 202 <= model.max_seq_length
    assert len(ids_254) == 256 <= model.max_seq_length

    single_vec = model.encode("DocSense embedding compatibility test", normalize_embeddings=True)
    assert single_vec.shape == (384,)

    batch_vecs = model.encode(
        ["First chunk text", "Second chunk text"],
        normalize_embeddings=True,
    )
    assert batch_vecs.shape == (2, 384)


def test_gate_chromadb_persistent_client_ntfs_lifecycle(tmp_path: Path) -> None:
    """Gate 6: ChromaDB PersistentClient creates cosine collection, queries, deletes, cleans."""
    import chromadb

    chroma_dir = tmp_path / "chroma_ntfs_gate"
    client = chromadb.PersistentClient(path=str(chroma_dir))
    collection = client.get_or_create_collection(
        name="docsense_gate_test",
        metadata={"hnsw:space": "cosine"},
    )

    doc1_vec = [1.0] + [0.0] * 383
    doc2_vec = [0.0, 1.0] + [0.0] * 382

    collection.upsert(
        ids=["doc-1:0", "doc-2:0"],
        embeddings=[doc1_vec, doc2_vec],
        documents=["Alpha PDF content", "Beta PDF content"],
        metadatas=[
            {
                "document_id": "doc-1",
                "filename": "alpha.pdf",
                "page_number": 1,
                "page_end": 1,
                "page_numbers_csv": "1",
                "chunk_index": 0,
                "token_count": 3,
            },
            {
                "document_id": "doc-2",
                "filename": "beta.pdf",
                "page_number": 1,
                "page_end": 2,
                "page_numbers_csv": "1,2",
                "chunk_index": 0,
                "token_count": 3,
            },
        ],
    )

    assert collection.count() == 2

    query_res = collection.query(query_embeddings=[doc1_vec], n_results=1)
    assert query_res["ids"] == [["doc-1:0"]]
    assert query_res["metadatas"][0][0]["document_id"] == "doc-1"

    collection.delete(where={"document_id": "doc-1"})
    assert collection.count() == 1

    remaining = collection.get(where={"document_id": "doc-2"})
    assert remaining["ids"] == ["doc-2:0"]

    client.delete_collection("docsense_gate_test")
    assert all(col.name != "docsense_gate_test" for col in client.list_collections())

    # Release client/system cache before removing temp directory on Windows.
    try:
        client._system.stop()  # noqa: SLF001
    except AttributeError:
        pass
    if hasattr(chromadb.api.client, "SharedSystemClient"):
        chromadb.api.client.SharedSystemClient.clear_system_cache()
    del collection
    del client
    gc.collect()
    shutil.rmtree(chroma_dir, ignore_errors=False)
    assert not chroma_dir.exists()
