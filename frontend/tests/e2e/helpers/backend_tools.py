"""CLI helper for generating PDF fixtures, seeding recovery data, and verifying stores."""

from __future__ import annotations

import hashlib
import json
import sys
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import chromadb  # noqa: E402
from sqlalchemy import select  # noqa: E402
from tests.fixtures import (  # noqa: E402
    make_corrupt_pdf_bytes,
    make_encrypted_pdf_bytes,
    make_multipage_crosspage_pdf_bytes,
    make_scanned_pdf_bytes,
    make_sized_valid_pdf_bytes,
    make_spoofed_pdf_bytes,
    make_text_pdf_bytes,
    make_zero_page_pdf_bytes,
)

from app.core.config import Settings  # noqa: E402
from app.db.session import create_db_engine, create_session_factory, init_db  # noqa: E402
from app.models.document import Document, DocumentChunk  # noqa: E402
from app.repositories.document_repo import DocumentRepository  # noqa: E402
from app.repositories.vector_store import ChromaVectorStore  # noqa: E402
from app.services.interfaces import TextChunk  # noqa: E402


def cmd_generate_fixtures(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    files = {
        "multipage.pdf": make_multipage_crosspage_pdf_bytes(),
        "simple.pdf": make_text_pdf_bytes(["Simple drag and drop PDF fixture content."]),
        "scanned.pdf": make_scanned_pdf_bytes(2),
        "encrypted.pdf": make_encrypted_pdf_bytes(),
        "zeropage.pdf": make_zero_page_pdf_bytes(),
        "corrupt.pdf": make_corrupt_pdf_bytes(),
        "spoofed.pdf": make_spoofed_pdf_bytes(),
        "oversize_1_1mb.pdf": make_sized_valid_pdf_bytes(
            int(1.1 * 1024 * 1024),
            "Valid 1.1 MB PDF for server 413 cutoff check.",
        ),
        "empty_zero.pdf": b"",
        ".pdf": b"%PDF-1.4\n",
        "notes.txt": b"Plain text file should be blocked client-side.",
    }
    manifest = {}
    for name, data in files.items():
        target = out_dir / name
        target.write_bytes(data)
        manifest[name] = str(target)
    print(json.dumps(manifest))


def cmd_seed_recovery(work_dir: Path) -> None:
    db_path = work_dir / "db" / "docsense.db"
    chroma_dir = work_dir / "chroma"
    upload_dir = work_dir / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)

    pending_id = str(uuid.uuid4())
    processing_id = str(uuid.uuid4())
    deleting_id = str(uuid.uuid4())
    ready_id = str(uuid.uuid4())

    pending_bytes = make_text_pdf_bytes(["Pending document recovered cleanly on startup."])
    processing_bytes = make_text_pdf_bytes(
        ["Interrupted processing document ready to reprocess from UI."]
    )
    deleting_bytes = make_text_pdf_bytes(["Interrupted deleting document to purge on startup."])
    ready_bytes = make_text_pdf_bytes(["Existing ready document persisted prior to restart."])

    pending_pdf = upload_dir / f"{pending_id}.pdf"
    processing_pdf = upload_dir / f"{processing_id}.pdf"
    deleting_pdf = upload_dir / f"{deleting_id}.pdf"
    ready_pdf = upload_dir / f"{ready_id}.pdf"

    pending_pdf.write_bytes(pending_bytes)
    processing_pdf.write_bytes(processing_bytes)
    deleting_pdf.write_bytes(deleting_bytes)
    ready_pdf.write_bytes(ready_bytes)

    settings = Settings(
        _env_file=None,
        DATABASE_URL=f"sqlite:///{db_path.as_posix()}",
        CHROMA_PERSIST_DIR=str(chroma_dir),
        UPLOAD_DIR=str(upload_dir),
    )
    engine = create_db_engine(settings.DATABASE_URL)
    store = ChromaVectorStore(settings=settings)
    try:
        init_db(engine)
        session_factory = create_session_factory(engine)
        repo = DocumentRepository(session_factory)

        repo.create_document(
            document_id=ready_id,
            filename="seeded-ready.pdf",
            content_type="application/pdf",
            file_size_bytes=len(ready_bytes),
            sha256_hash=hashlib.sha256(ready_bytes).hexdigest(),
            storage_path=str(ready_pdf),
            page_count=1,
        )
        assert repo.claim_for_processing(ready_id) is True
        ready_chunk = TextChunk(
            chunk_index=0,
            text="Existing ready document persisted prior to restart.",
            page_number=1,
            page_end=1,
            page_numbers_csv="1",
            token_count=8,
            char_start=0,
            char_end=52,
            content_preview="Existing ready document persisted prior to restart.",
        )
        unit_vec = [1.0] + [0.0] * 383
        store.upsert_chunks(ready_id, "seeded-ready.pdf", [ready_chunk], [unit_vec])
        assert repo.complete_ingestion(ready_id, 1, [ready_chunk]) is not None

        repo.create_document(
            document_id=pending_id,
            filename="seeded-pending.pdf",
            content_type="application/pdf",
            file_size_bytes=len(pending_bytes),
            sha256_hash=hashlib.sha256(pending_bytes).hexdigest(),
            storage_path=str(pending_pdf),
            page_count=1,
        )

        repo.create_document(
            document_id=processing_id,
            filename="seeded-processing.pdf",
            content_type="application/pdf",
            file_size_bytes=len(processing_bytes),
            sha256_hash=hashlib.sha256(processing_bytes).hexdigest(),
            storage_path=str(processing_pdf),
            page_count=1,
        )
        assert repo.claim_for_processing(processing_id) is True

        repo.create_document(
            document_id=deleting_id,
            filename="seeded-deleting.pdf",
            content_type="application/pdf",
            file_size_bytes=len(deleting_bytes),
            sha256_hash=hashlib.sha256(deleting_bytes).hexdigest(),
            storage_path=str(deleting_pdf),
            page_count=1,
        )
        repo.mark_deleting(deleting_id)

        partial_chunk = TextChunk(
            chunk_index=0,
            text="Partial interrupted chunk",
            page_number=1,
            page_end=1,
            page_numbers_csv="1",
            token_count=3,
            char_start=0,
            char_end=25,
            content_preview="Partial interrupted chunk",
        )
        with session_factory() as session:
            session.add_all(
                [
                    DocumentChunk(
                        document_id=processing_id,
                        chunk_index=0,
                        page_number=1,
                        page_end=1,
                        page_numbers_csv="1",
                        token_count=3,
                        char_start=0,
                        char_end=25,
                        content_preview="Partial interrupted chunk",
                        chroma_id=f"{processing_id}:0",
                    ),
                    DocumentChunk(
                        document_id=deleting_id,
                        chunk_index=0,
                        page_number=1,
                        page_end=1,
                        page_numbers_csv="1",
                        token_count=3,
                        char_start=0,
                        char_end=25,
                        content_preview="Deleting chunk",
                        chroma_id=f"{deleting_id}:0",
                    ),
                ]
            )
            session.commit()

        store.upsert_chunks(processing_id, "seeded-processing.pdf", [partial_chunk], [unit_vec])
        store.upsert_chunks(deleting_id, "seeded-deleting.pdf", [partial_chunk], [unit_vec])
    finally:
        store.close()
        engine.dispose()

    print(
        json.dumps(
            {
                "ready_id": ready_id,
                "pending_id": pending_id,
                "processing_id": processing_id,
                "deleting_id": deleting_id,
            }
        )
    )


def cmd_verify_deleted(work_dir: Path, doc_id: str) -> None:
    db_path = work_dir / "db" / "docsense.db"
    chroma_dir = work_dir / "chroma"
    upload_dir = work_dir / "uploads"

    pdf_exists = (upload_dir / f"{doc_id}.pdf").exists()
    db_doc_exists = False
    db_chunk_count = 0
    chroma_vector_count = 0

    engine = create_db_engine(f"sqlite:///{db_path.as_posix()}")
    try:
        with create_session_factory(engine)() as session:
            db_doc_exists = session.get(Document, doc_id) is not None
            db_chunk_count = len(
                session.execute(
                    select(DocumentChunk).where(DocumentChunk.document_id == doc_id)
                )
                .scalars()
                .all()
            )
    finally:
        engine.dispose()

    chroma = chromadb.PersistentClient(path=str(chroma_dir))
    try:
        collection = chroma.get_collection("docsense_chunks")
        res = collection.get(where={"document_id": doc_id}, include=[])
        chroma_vector_count = len(res["ids"])
    finally:
        ChromaVectorStore._release_client_handles(chroma)

    print(
        json.dumps(
            {
                "pdf_exists": pdf_exists,
                "db_doc_exists": db_doc_exists,
                "db_chunk_count": db_chunk_count,
                "chroma_vector_count": chroma_vector_count,
            }
        )
    )


def main() -> None:
    action = sys.argv[1]
    if action == "generate-fixtures":
        cmd_generate_fixtures(Path(sys.argv[2]))
    elif action == "seed-recovery":
        cmd_seed_recovery(Path(sys.argv[2]))
    elif action == "verify-deleted":
        cmd_verify_deleted(Path(sys.argv[2]), sys.argv[3])
    else:
        raise SystemExit(f"Unknown action: {action}")


if __name__ == "__main__":
    main()
