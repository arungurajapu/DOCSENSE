# DocSense Threat Model (STRIDE)

**Baseline Audited:** DocSense `0.1.0` Milestone 1 (`master`)  
**Scope:** End-to-end PDF upload, validation, text extraction, tokenization/chunking, embedding generation, ChromaDB vector indexing, SQLite metadata persistence, and Next.js Web UI.  
**Status:** Documented architecture and Milestone 1 risk baseline (non-exhaustive; covers implemented surface only).

---

## 1. System Architecture & Trust Boundaries

DocSense Milestone 1 runs as a two-tier local/prototype application with a single-process FastAPI backend and a Next.js client dashboard:

```text
+-------------------------------------------------------------------------+
| Trust Boundary 1: Untrusted User Browser / Network Client               |
|  - Selects / drops PDF files (client-side validation can be bypassed)   |
|  - Calls HTTP/JSON & multipart APIs on NEXT_PUBLIC_API_BASE_URL         |
+------------------------------------+------------------------------------+
                                     | HTTP (CORS: CORS_ORIGINS)
                                     v
+-------------------------------------------------------------------------+
| Trust Boundary 2: FastAPI API Process (Single-Worker uvicorn --workers 1)|
|  - /api/v1/health, /api/v1/documents, /documents/{id}, /reprocess       |
|  - No AuthN / AuthZ / Rate Limiting in Milestone 1                      |
|  - StorageService: streams <= MAX_UPLOAD_SIZE_MB, %PDF- check, UUID path |
|  - Background asyncio.Task pipeline (IngestionService):                 |
|      1. PDFExtractor (PyMuPDF native C/C++ in-process via to_thread)    |
|      2. TokenChunker (HuggingFace AutoTokenizer)                        |
|      3. EmbeddingService (SentenceTransformer CPU PyTorch + HuggingFace)|
|      4. ChromaVectorStore (chroma_io_lock) + DocumentRepository (SQLite)|
+------------------------------------+------------------------------------+
                                     |
         +---------------------------+---------------------------+
         v                                                       v
+-----------------------------------+   +---------------------------------+
| Trust Boundary 3: Local Filesystem|   | Trust Boundary 4: External / Hub|
|  - UPLOAD_DIR (raw PDFs)          |   |  - HuggingFace Hub download     |
|  - DATABASE_URL (SQLite WAL files)|   |    (when EMBEDDING_MODEL is not |
|  - CHROMA_PERSIST_DIR (vectors)   |   |    pre-cached locally)          |
+-----------------------------------+   +---------------------------------+
```

### Primary Assets to Protect

1. **Uploaded PDF contents & extracted text chunks** (stored on disk in `UPLOAD_DIR` and indexed in ChromaDB / SQLite `content_preview`).
2. **Database & Vector Index consistency** (SQLite `documents` / `document_chunks` tables matching ChromaDB embeddings).
3. **Host filesystem & process stability** (preventing path escape, native crash/OOM in PyMuPDF or PyTorch, disk exhaustion).
4. **Runtime configuration & environment variables** (`.env`, local model cache, filesystem paths).
5. **Browser session integrity** (preventing XSS via malicious filenames or server error messages).

---

## 2. Trust Boundary Analysis

| Boundary | Transition | Current Controls | Remaining Exposure |
| --- | --- | --- | --- |
| **TB-1: Browser -> FastAPI API** | Untrusted HTTP requests, multipart uploads, UUID path params, query pagination (`limit <= 100`, `offset >= 0`) | Pydantic/FastAPI schema validation, `UUID` path typing, `MAX_UPLOAD_SIZE_MB` streaming cap, `%PDF-` header check, `CORS_ORIGINS` allowlist | No authentication/authorization; no rate limiting; no concurrent upload cap; `allow_credentials=True` with wildcard methods/headers |
| **TB-2: Managed Python -> Native C/C++ Parsers & ML** | Python web process -> PyMuPDF (`pymupdf` wrapping the native MuPDF C/C++ library), PyTorch/Transformers/Tokenizers | PyMuPDF run off event loop via `asyncio.to_thread`; corrupted/encrypted/0-page errors caught cleanly | No OS/process sandbox around native PDF parser; maliciously crafted PDFs triggering C-level memory issues crash the single uvicorn worker |
| **TB-3: API Process -> Local Persistence** | Python app -> `UPLOAD_DIR`, SQLite (`data/docsense.db`), `CHROMA_PERSIST_DIR` | UUID filenames (`{document_id}.pdf`), `StorageService.resolve_path` containment check, atomic temp-to-final `os.replace`, WAL mode, `chroma_io_lock`, SQLAlchemy parameterization | No filesystem quota / total upload count limit; no encryption at rest; single-process assumption (`--workers 1`) |
| **TB-4: Backend -> HuggingFace Hub** | Model/tokenizer load on cold start (`AutoTokenizer.from_pretrained`, `SentenceTransformer`) | `local_files_only=True` fallback when offline; clean degraded startup (PR #15 / Issue #13) | First uncached run fetches artifacts over HTTPS from external HuggingFace endpoints without pinned commit revision/hash unless offline cache or local path is configured |

---

## 3. STRIDE Threat Analysis

### S — Spoofing

| ID | Threat | Affected Component | Current Mitigation | Severity (Local / Internet-Exposed) | Recommended Action |
| --- | --- | --- | --- | --- | --- |
| **S-1** | Unauthenticated callers impersonating legitimate users or accessing API endpoints directly | All `/api/v1/*` routes (`backend/app/api/v1/`) | None in Milestone 1 (local-only architecture) | Low / **Critical** | Implement AuthN/AuthZ (API key, JWT/OIDC, or reverse-proxy auth) before multi-user or network deployment |
| **S-2** | Content-type / extension spoofing (uploading non-PDF executable/HTML/script renamed `.pdf`) | `POST /api/v1/documents` (`StorageService.save_validated_pdf`) | Checks `%PDF-` header in first `1024` bytes (`UnsupportedMediaTypeError` 415) AND validates structure via PyMuPDF (`pymupdf.open`), forcing stored extension `.pdf` and `application/pdf` content type; never serves raw PDFs inline via GET | **Low** (mitigated) | Retain server-side magic-byte + parser validation; if raw file download is added later, enforce `Content-Disposition: attachment` and `X-Content-Type-Options: nosniff` |

### T — Tampering

| ID | Threat | Affected Component | Current Mitigation | Severity (Local / Internet-Exposed) | Recommended Action |
| --- | --- | --- | --- | --- | --- |
| **T-1** | Path traversal or arbitrary file overwrite/deletion via crafted upload filenames or `document_id` | `StorageService` (`storage.py`), `documents.py`, `vector_store.py` | FastAPI enforces `UUID` on path params; `_sanitize_filename` strips POSIX/Windows directory components; `_validate_document_id` regex check; `StorageService.resolve_path` verifies `is_relative_to(UPLOAD_DIR)` and rejects `\x00` | **Low** (mitigated) | Keep defense-in-depth checks and existing unit tests (`test_storage.py`) |
| **T-2** | SQL injection via query parameters or metadata fields | `DocumentRepository` (`document_repo.py`) | All queries use SQLAlchemy ORM / Core expressions with bound parameters (`select`, `update`, `delete`, `insert`) | **Low** (mitigated) | Enforce parameterized queries across future search/QA endpoints |
| **T-3** | State corruption / orphan vectors from concurrent delete vs. ingestion vs. reprocess | `IngestionService` (`ingestion.py`), `ChromaVectorStore` | Per-document `asyncio.Lock`, `cancel_event`, `extraction_done`, compensating vector delete, conditional SQL updates (`status == processing`) | **Low** (mitigated for `--workers 1`) | Add startup warning/guard if multiple workers detected; move locking to DB/queue before multi-worker scaling |
| **T-4** | Supply-chain or remote model weight tampering when fetching `EMBEDDING_MODEL` from HuggingFace Hub | `TokenChunker` (`chunker.py`), `EmbeddingService` (`embeddings.py`) | Supports local model directory path and `local_files_only=True` fallback | Medium | Pin an immutable HuggingFace commit revision / checksum or bundle pre-verified weights for production deployments |

### R — Repudiation

| ID | Threat | Affected Component | Current Mitigation | Severity (Local / Internet-Exposed) | Recommended Action |
| --- | --- | --- | --- | --- | --- |
| **R-1** | No audit trail of who uploaded, reprocessed, or deleted documents | `documents.py`, `ingestion.py` | Standard Python `logging` events with `document_id` and timestamps on DB rows (`created_at`, `updated_at`) | Low / Medium | When AuthN is added, record actor identity and security-relevant audit logs (upload, delete, reprocess, auth failures) |

### I — Information Disclosure

| ID | Threat | Affected Component | Current Mitigation | Severity (Local / Internet-Exposed) | Recommended Action |
| --- | --- | --- | --- | --- | --- |
| **I-1** | Internal exception details / file paths leaked in `Document.error_message` | `IngestionService._run_ingestion` (`ingestion.py:402-407`), `PDFExtractor.extract` (`extractor.py`), `StorageService` | HTTP 500 handler in `errors.py` returns generic `"An unexpected internal error occurred."` | **Medium** | Redact raw exception class names, trace-like exception strings, and host filenames/paths before persisting `error_message` or returning `DocSenseError` messages to API clients |
| **I-2** | Unauthenticated read access to all uploaded document metadata (`filename`, `sha256_hash`, `page_count`, `error_message`) | `GET /api/v1/documents`, `GET /api/v1/documents/{id}` | None in Milestone 1 | Low / **High** | Enforce per-user authentication and tenant ownership filtering before shared deployment |
| **I-3** | Cross-site UI embedding / missing browser security headers | `frontend/next.config.ts` | Only `reactStrictMode: true` configured | **Medium** | Add CSP, `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy` headers in Next.js or reverse proxy |
| **I-4** | Accidental cross-origin data exposure via misconfigured `CORS_ORIGINS` | `backend/app/main.py:154-160` | Defaults strictly to `http://localhost:3000` and `http://127.0.0.1:3000` | Low / High if misconfigured | Never configure `CORS_ORIGINS=["*"]` with `allow_credentials=True`; tighten `allow_methods` and `allow_headers` |

### D — Denial of Service

| ID | Threat | Affected Component | Current Mitigation | Severity (Local / Internet-Exposed) | Recommended Action |
| --- | --- | --- | --- | --- | --- |
| **D-1** | Unbounded concurrent uploads & background CPU/memory ingestion tasks (PyMuPDF + PyTorch) exhausting RAM/CPU/disk | `POST /api/v1/documents` -> `IngestionService.schedule_ingestion` | Per-file `MAX_UPLOAD_SIZE_MB` streaming cap (`25 MiB` default) | **High** (even locally under batch abuse) | Add an `asyncio.Semaphore` / bounded worker queue (`MAX_CONCURRENT_INGESTIONS`), HTTP rate limiting, and total storage quota monitoring |
| **D-2** | Malformed or decompression-bomb PDF (excessive pages, huge text streams, adversarial image/font tables) crashing PyMuPDF or timing out tokenization | `PDFExtractor.extract`, `TokenChunker.chunk` | Offloaded to worker thread; empty/corrupt/encrypted handled via `ExtractionError` | **Medium–High** | Enforce page-count ceiling (`MAX_PDF_PAGES`), per-document extraction timeout, and process isolation/sandboxing for native PDF parsing |
| **D-3** | Unbounded HTTP pagination abuse or orphaned DB/disk growth | `GET /api/v1/documents` (`limit` capped `1..100`) | `limit <= 100` enforced by FastAPI Query validation | Low | Keep `le=100` bound; add cleanup/retention policies if hosted |
| **D-4** | ReDoS in frontend lint/tooling dependency (`minimatch` GHSA-g3hv-75wg-m249) | Frontend dev toolchain (`eslint-config-next` -> `typescript-eslint` -> `minimatch`) | Dev-only dependency (not executed in runtime browser bundle or API server) | **Low** (dev/CI impact only) | Upgrade/override `minimatch` or update `eslint-config-next` once fixed upstream without breaking Next 16 |

### E — Elevation of Privilege

| ID | Threat | Affected Component | Current Mitigation | Severity (Local / Internet-Exposed) | Recommended Action |
| --- | --- | --- | --- | --- | --- |
| **E-1** | Missing role separation — any network-reachable client can upload, trigger expensive reprocessing, and permanently delete any document | `POST /documents`, `POST /reprocess`, `DELETE /documents/{id}` | None in Milestone 1 (single-user trust model) | Low / **Critical** | Introduce authentication + RBAC / document ownership checks prior to multi-user exposure |
| **E-2** | Malicious PDF exploit targeting native MuPDF C/C++ memory corruption inside backend Python process | `pymupdf` (`extractor.py`, `storage.py`) | Strict `%PDF-` + single-file streaming limit | Medium / High | Isolate PDF parsing into an unprivileged subprocess/sandbox or replace PyMuPDF with a memory-safe/pure-Python or containerized parser (aligns with AGPL-3.0 remediation) |
| **E-3** | Cross-Site Scripting (XSS) via malicious PDF filenames or error payloads | `DocumentList.tsx`, `DeleteConfirmDialog.tsx`, `UploadPanel.tsx` | React automatic HTML escaping; zero `dangerouslySetInnerHTML` / `innerHTML` usage | **Low** (mitigated) | Enforce CSP (`script-src 'self'`) as defense-in-depth |

---

## 4. Prioritized Threat Remediation Summary

Immediate pre-release hardening and future Milestone 2 backlog items derived from this threat model are tracked as GitHub Issues and summarized in [`docs/security/licensing-and-security-audit.md`](licensing-and-security-audit.md).

