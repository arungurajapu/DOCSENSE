# DocSense

**DocSense** (`0.1.0`, Milestone 1) is an end-to-end document ingestion and indexing platform built with **Next.js (TypeScript)**, **FastAPI (Python 3.12)**, **SQLite (SQLAlchemy 2.x + WAL)**, **ChromaDB**, and **`sentence-transformers/all-MiniLM-L6-v2`**.

Users can upload PDF documents through a responsive, keyboard-accessible dashboard, track asynchronous lifecycle transitions (`pending -> processing -> ready | failed | deleting`), inspect validation/extraction diagnostics, reprocess failed documents, and delete documents cleanly across filesystem storage, SQLite metadata, and vector embeddings.

> **Important Security & Licensing Notices before Hosting:**
> 1. **Milestone 1 Deployment Boundary:** DocSense `0.1.0` has **no built-in authentication, rate limiting, or OS PDF-parser sandbox** and requires single-worker execution (`uvicorn app.main:app --workers 1`). Run locally or behind an authenticating TLS reverse proxy. See [`SECURITY.md`](SECURITY.md).
> 2. **Root `LICENSE` Deferred (Option B):** First-party source code is intended for **MIT** release, but the backend currently uses **`pymupdf`** (**GNU AGPL-3.0** / Artifex Commercial License). Root `LICENSE` is intentionally omitted until **Issue #18** replaces PyMuPDF or secures a commercial license. See [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) and [`docs/security/licensing-and-security-audit.md`](docs/security/licensing-and-security-audit.md).

---

## Architecture Overview

```text
+-----------------------------------+       HTTP / REST (/api/v1)      +----------------------------------------+
| Next.js Frontend (Port 3000)      | <------------------------------> | FastAPI Backend (uvicorn --workers 1)  |
| - UploadPanel (drag/drop + picker)|                                  | - Streaming PDF validation (<=25 MiB)  |
| - DocumentList (filters + polling)|                                  | - IngestionService (per-doc lock +     |
| - HealthBadge (ok / degraded)     |                                  |   safe cancellation & startup recovery)|
| - DeleteConfirmDialog             |                                  | - PDFExtractor (PyMuPDF)               |
+-----------------------------------+                                  | - TokenChunker (all-MiniLM-L6-v2)      |
                                                                       | - EmbeddingService (CPU PyTorch)       |
                                                                       +----------+------------------+----------+
                                                                                  |                  |
                                                                                  v                  v
                                                                       +------------------+  +------------------+
                                                                       | SQLite (WAL)     |  | Embedded ChromaDB|
                                                                       | + Local PDF Disk |  | + chroma_io_lock |
                                                                       +------------------+  +------------------+
```

### Key Implemented Features

- **Streaming Upload & Validation:** Enforces size caps (`MAX_UPLOAD_SIZE_MB`, default `25` MiB -> `413`), rejects `0`-byte files (`400`), checks `%PDF-` magic headers (`415`), rejects corrupt PDF structure (`415`) and encrypted/0-page PDFs (`422`), computes SHA-256 hashes, sanitizes filenames, and stores files atomically inside `UPLOAD_DIR`.
- **Model-Aligned Chunking & Vector Indexing:** Extracts per-page text, splits tokens aligned to `EMBEDDING_MODEL` (`CHUNK_SIZE_TOKENS=200`, `CHUNK_OVERLAP_TOKENS=40`, `MODEL_MAX_SEQ_LENGTH=256`), generates L2-normalized 384-dim embeddings on CPU, and commits ChromaDB vectors (`"{document_id}:{chunk_index}"`) + SQLite chunk provenance atomically.
- **Race-Safe Cancellation & Windows Cleanup:** Per-document `asyncio.Lock` + `threading.Event` cancellation flags + Chroma mutex + Windows retry file deletion (`_unlink_with_retry`) prevent orphan state during concurrent upload/delete/reprocess workflows.
- **Startup Reconciliation & Degraded Mode:** On boot, incomplete `deleting` rows are purged, `processing` rows are cleanly marked `failed` with retry guidance, `pending` rows are rescheduled, and if `EMBEDDING_MODEL` cannot initialize (e.g., offline without cache), the API stays up returning HTTP `200` with `status: "degraded"` and `embedding_model_loaded: false`.
- **Frozen OpenAPI 3.1 Contract Parity:** Shared schema contract in [`contracts/openapi.json`](contracts/openapi.json) stays synchronized with FastAPI schemas and generated TypeScript types (`frontend/src/types/openapi.generated.ts`).

---

## Tech Stack

- **Frontend:** Next.js `16.4.6`, React `19.3.1`, TypeScript `5.9.3`, Tailwind CSS v4, Vitest + Testing Library, Playwright (Chromium)
- **Backend:** FastAPI `0.162.4`, Uvicorn `0.54.0`, SQLAlchemy `2.1.8`, Pydantic v2 (`2.14.3`), `pydantic-settings`, Python `3.12`
- **NLP / Embeddings / Storage:** `sentence-transformers` (`all-MiniLM-L6-v2`), `transformers`, CPU `torch`, `chromadb` (`PersistentClient`), `pymupdf` (scheduled for replacement under Issue #18)

---

## Repository Structure

```text
DocSense/
├── backend/
│   ├── app/
│   │   ├── api/            # FastAPI routers (/api/v1/health, /api/v1/documents), deps, unified errors
│   │   ├── core/           # pydantic-settings config & domain exceptions
│   │   ├── db/             # SQLAlchemy engine, WAL pragmas, UTCDateTime type
│   │   ├── models/         # Document & DocumentChunk ORM models
│   │   ├── repositories/   # DocumentRepository (SQLite) & ChromaVectorStore
│   │   └── services/       # StorageService, PDFExtractor, TokenChunker, EmbeddingService, IngestionService
│   ├── scripts/            # OpenAPI exporter (export_openapi.py)
│   ├── tests/              # Unit, integration (race/recovery/parity), and backend E2E tests
│   ├── pyproject.toml      # Project metadata & Ruff/pytest configuration
│   └── requirements.lock   # Pinned Python 3.12 lockfile (CPU PyTorch)
├── frontend/
│   ├── src/
│   │   ├── app/            # Next.js App Router pages & styles
│   │   ├── components/     # DocumentsDashboard, StatusBadge, UploadPanel, DocumentList, HealthBadge, DeleteConfirmDialog
│   │   ├── lib/            # Fetch API client, formatting, validation, runtime config
│   │   └── types/          # OpenAPI generated & UI types
│   ├── scripts/            # OpenAPI TypeScript contract checker/generator (sync-contracts.mjs)
│   └── tests/e2e/          # Playwright headless Chromium end-to-end tests
├── contracts/
│   └── openapi.json        # Frozen OpenAPI 3.1 contract
├── docs/
│   ├── architecture/       # Licensing, Windows Py3.12 setup & concurrency architecture
│   └── security/           # STRIDE threat model & TASK-7 security/licensing audit
├── SECURITY.md             # Vulnerability reporting & Milestone 1 deployment boundaries
├── THIRD_PARTY_NOTICES.md  # Complete Python, npm, and AI model license notices
└── .env.example            # Root environment configuration template
```

---

## Local Setup & Running (Windows / Python 3.12)

### Prerequisites

- **Python 3.12** (`>=3.12,<3.13`; required by `backend/pyproject.toml`)
- **Node.js 20+** and **npm**
- Internet access on first run to cache `sentence-transformers/all-MiniLM-L6-v2` (or an existing local HuggingFace cache / directory path configured via `EMBEDDING_MODEL`)

### 1. Environment Configuration

Copy the environment templates as needed (never commit `.env` or `.env.local` files):

```powershell
Copy-Item .env.example .env
Copy-Item frontend\.env.example frontend\.env.local
```

Key defaults in [`.env.example`](.env.example) & [`frontend/.env.example`](frontend/.env.example):
- `MAX_UPLOAD_SIZE_MB=25`, `NEXT_PUBLIC_MAX_UPLOAD_SIZE_MB=25`
- `CORS_ORIGINS=["http://localhost:3000","http://127.0.0.1:3000"]`
- `EMBEDDING_MODEL=sentence-transformers/all-MiniLM-L6-v2`
- `NEXT_PUBLIC_API_BASE_URL=http://localhost:8000`

### 2. Backend Setup & Run (`--workers 1` Required)

```powershell
cd backend
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.lock
pip install --no-deps -e .
pip check
uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

> **Concurrency constraint:** Never start Milestone 1 with `--workers > 1` or `--reload` in production. In-process `asyncio.Lock` synchronization, SQLite WAL single-writer locking, and embedded `ChromaVectorStore` require a single backend worker process.

### 3. Frontend Setup & Run

```powershell
cd frontend
npm ci
npm run dev
```

Open <http://localhost:3000> in your browser. Interactive FastAPI OpenAPI docs are available locally at <http://localhost:8000/docs>.

---

## Verification & Test Commands

### Backend

```powershell
cd backend
.\.venv\Scripts\python -m pytest tests/unit tests/integration -q
.\.venv\Scripts\python -m pytest tests/e2e -q
.\.venv\Scripts\python -m ruff check app tests scripts
```

### Frontend & Contract Parity

```powershell
cd frontend
npm run check:contracts
npm run lint
npm run typecheck
npm test
npm run build

# One-time Chromium setup for Playwright E2E tests:
npx playwright install chromium
npm run test:e2e
```

---

## API Contract Summary (`/api/v1`)

Full OpenAPI 3.1 schema: [`contracts/openapi.json`](contracts/openapi.json). All 4xx/5xx responses conform to `ErrorResponse { error: { code, message } }`.

| Method | Path | Purpose | Status Codes |
| --- | --- | --- | --- |
| `GET` | `/api/v1/health` | Report service status (`ok` / `degraded`), SQLite & Chroma health, and `embedding_model_loaded` | `200`, `503` |
| `POST` | `/api/v1/documents` | Stream-validate and store PDF upload (`multipart/form-data`), enqueue async ingestion | `202`, `400`, `413`, `415`, `422` |
| `GET` | `/api/v1/documents` | Paginated document list (`limit` `1..100`, `offset >= 0`, optional `status` filter) | `200`, `422` |
| `GET` | `/api/v1/documents/{document_id}` | Fetch single document metadata & lifecycle status | `200`, `404`, `422` |
| `POST` | `/api/v1/documents/{document_id}/reprocess` | Reset a `failed` document to `pending` and reschedule ingestion | `202`, `404`, `409`, `422` |
| `DELETE` | `/api/v1/documents/{document_id}` | Safely cancel active ingestion and delete Chroma vectors, PDF file, and SQLite rows | `204`, `404`, `409`, `422` |

---

## Security, Threat Model & Licensing Status

- **Security Policy & Deployment Checklist:** [`SECURITY.md`](SECURITY.md)
- **STRIDE Threat Model:** [`docs/security/threat-model.md`](docs/security/threat-model.md)
- **Security, Dependency & Licensing Audit (TASK-7):** [`docs/security/licensing-and-security-audit.md`](docs/security/licensing-and-security-audit.md)
- **Third-Party Software & AI Model Notices:** [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)
- **Architecture & Windows Compatibility Rationale:** [`docs/architecture/licensing-and-ingestion.md`](docs/architecture/licensing-and-ingestion.md)

No root `LICENSE` file is committed yet while **Issue #18** (`pymupdf` GNU AGPL-3.0 remediation) remains open; see [`docs/security/licensing-and-security-audit.md`](docs/security/licensing-and-security-audit.md) for the complete MIT compatibility assessment and prioritized remediation roadmap (**Issues #18–#28**).
