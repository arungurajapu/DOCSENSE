# DocSense Architecture: Licensing, Ingestion Provenance & Concurrency Boundaries

## 1. PyMuPDF (`pymupdf`) Licensing Considerations

DocSense Milestone 1 uses [`pymupdf`](https://pypi.org/project/PyMuPDF/) (MuPDF Python bindings by Artifex Software) for fast, accurate PDF header validation and page-level text extraction.

### License Terms
- **Open-Source License**: `pymupdf` / MuPDF is distributed under **GNU AGPL-3.0**.
- **Commercial License**: Artifex Software offers a paid commercial license for proprietary deployments that cannot comply with AGPL-3.0 copyleft terms.
- **AGPL-3.0 Network Trigger**: Under AGPL-3.0 Section 13, making a modified or combined work available to users over a network (e.g., commercial SaaS) generally obligates distributing the complete corresponding source code under AGPL-3.0 unless a commercial Artifex license is obtained.

### Architectural Isolation (`PDFExtractor` Protocol)
To keep licensing risk cleanly bounded and allow zero-churn library substitution prior to any proprietary/commercial distribution:
1. All `pymupdf` imports are isolated strictly behind the internal domain contracts in `backend/app/services/interfaces.py` (`PDFExtractor` and PDF-validation logic inside `StorageService`).
2. `PDFExtractor.extract(file_path)` returns pure Python `ExtractedPage(page_number, text)` data transfer objects—no MuPDF document, page, or fitz objects ever leak across module boundaries.
3. Downstream components (`TokenChunker`, `EmbeddingService`, `VectorStore`, `IngestionService`, SQLAlchemy models, FastAPI endpoints, and the Next.js frontend) have zero direct dependency on `pymupdf`.
4. Swapping to an Apache/BSD/MIT-licensed extractor (such as `pypdfium2`, `pypdf`, or `pdfplumber`) or acquiring an Artifex commercial license only affects the isolated extraction/validation adapter.

### Deterministic Windows File Handle Release
On Windows NTFS, open file handles prevent immediate deletion or overwrite (`PermissionError: [WinError 32]`). Every `pymupdf.open(...)` call MUST use a deterministic `with pymupdf.open(...) as doc:` context manager so the OS descriptor closes immediately on scope exit.

---

## 2. Model-Aligned Token Chunking & Cross-Page Provenance

### Tokenizer Alignment (`all-MiniLM-L6-v2`)
- **Configured Model**: `sentence-transformers/all-MiniLM-L6-v2` (`384`-dimensional embeddings, `MODEL_MAX_SEQ_LENGTH = 256`).
- **WordPiece Budget**: Unlike OpenAI `tiktoken`, `all-MiniLM-L6-v2` uses HuggingFace `AutoTokenizer` WordPiece tokenization and injects `[CLS]` and `[SEP]` (`2` special tokens) on encoding.
- **Config Guardrail**: `backend/app/core/config.py` enforces both:
  - `0 <= CHUNK_OVERLAP_TOKENS < CHUNK_SIZE_TOKENS` (`40 < 200`)
  - `CHUNK_SIZE_TOKENS + 2 <= MODEL_MAX_SEQ_LENGTH` (`200 + 2 <= 256`)
  This guarantees chunk token sequences remain safely inside the `256` token limit even after `[CLS]`/`[SEP]` insertion.

### Cross-Page Chunk Provenance
1. Ordered 1-based `ExtractedPage(page_number, text)` entries are concatenated with `\n\n`, recording each page's exact `[start, end)` character span in the combined document text.
2. `AutoTokenizer(..., return_offsets_mapping=True, truncation=False)` maps token slices (`CHUNK_SIZE_TOKENS = 200`, `CHUNK_OVERLAP_TOKENS = 40`) directly back to original character offsets `[char_start, char_end)`.
3. Any page whose character interval overlaps `[char_start, char_end)` contributes to provenance:
   - `page_number`: first overlapping 1-based page number.
   - `page_end`: last overlapping 1-based page number.
   - `page_numbers_csv`: comma-separated scalar page list (e.g., `"2,3"`).
4. **ChromaDB Metadata Scalar Constraint**: Embedded ChromaDB rejects list/array metadata values. Storing `page_number: int`, `page_end: int`, and `page_numbers_csv: str` preserves exact multi-page provenance in both SQLite (`document_chunks`) and ChromaDB (`docsense_chunks`).

---

## 3. Single-Process Concurrency & `--workers 1` Boundary

Milestone 1 persists metadata in local SQLite (`DATABASE_URL`) and vectors in embedded `chromadb.PersistentClient` (`CHROMA_PERSIST_DIR`) on local NTFS storage.

### SQLite Discipline (`backend/app/db/session.py`)
- Every SQLite connection applies **only** per-connection settings via `@event.listens_for(engine, "connect")`:
  - `PRAGMA foreign_keys=ON;` (enforces `ON DELETE CASCADE` from `documents` to `document_chunks`)
  - `PRAGMA busy_timeout=5000;` (mitigates short SQLite write contention)
- `PRAGMA journal_mode=WAL;` is executed **once** at startup inside `init_db(engine)` on file-backed SQLite databases (skipped for `:memory:`), avoiding lock/race overhead on every new connection.

### In-Process Synchronization Scope
- Embedded `chromadb.PersistentClient` is **not** multi-process safe on Windows NTFS.
- Ingestion/deletion coordination (`asyncio.Lock` per `document_id`, `threading.Event` cancellation signaling, and `threading.Lock` around ChromaDB I/O) is strictly **single-process**.
- **Deployment Requirement**: Uvicorn MUST run with `--workers 1` (`uvicorn app.main:app --workers 1`). Scaling horizontally across multiple worker processes or hosts requires migrating to a shared RDBMS (e.g., PostgreSQL), distributed task queue + locking, and client-server ChromaDB (or dedicated vector service).

---

## 4. Windows Python 3.12.10 Setup & Compatibility Gate

### Why Python 3.12 (`>=3.12,<3.13`)
DocSense pins `requires-python = ">=3.12,<3.13"` because key ML/vector dependencies (`torch`, `chromadb`, `pymupdf`, `tokenizers`/`onnxruntime`) provide prebuilt Windows x64 (`AMD64`) binary wheels for Python 3.12, whereas newer Python releases (e.g., 3.14) lack full wheel coverage and trigger source compilation errors on Windows.

### Local Verification Commands (Windows)
Run from `backend/`:

```powershell
# 1. Create virtual environment explicitly with Python 3.12.10
C:\Users\arun\AppData\Local\Programs\Python\Python312\python.exe -m venv .venv
.\.venv\Scripts\python.exe -V  # Expected: Python 3.12.10

# 2. Install backend + dev extras and check dependency integrity
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pip check

# 3. Export frozen OpenAPI contract
.\.venv\Scripts\python.exe scripts\export_openapi.py

# 4. Execute foundation & Windows 6-package compatibility test suite
.\.venv\Scripts\pytest.exe -v
```
