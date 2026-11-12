# Security Policy

## Scope & Current Release Posture

DocSense (`0.1.0`) is currently a **Milestone 1 local/prototype PDF ingestion platform** (Next.js frontend + FastAPI backend + SQLite + embedded ChromaDB + `sentence-transformers/all-MiniLM-L6-v2`).

**Important security notice:** DocSense `0.1.0` has **not** undergone a third-party penetration test or security certification, and it **does not yet include built-in authentication, authorization, rate limiting, or OS-level PDF parser sandboxing**. Until those controls are implemented, **do not expose DocSense directly to untrusted networks or the public internet** without placing it behind a trusted authenticating reverse proxy, firewall, and TLS termination layer.

---

## Supported Versions

| Version / Branch | Status | Security Fixes |
| --- | --- | --- |
| `master` (`0.1.0` Milestone 1) | Active development | Best-effort via PRs and tracked remediation issues |
| Older commits / worktrees | Unsupported | Upgrade to latest `master` |

---

## Reporting a Vulnerability

Please **do not** open a public GitHub issue for suspected security vulnerabilities that could put users or deployments at risk.

1. Report privately via GitHub Security Advisories:
   <https://github.com/arungurajapu/DOCSENSE/security/advisories/new>
2. Include as much detail as possible:
   - Affected file paths, endpoints, or dependencies (e.g., `backend/app/services/storage.py`, `POST /api/v1/documents`)
   - Exact reproduction steps or proof-of-concept payload (sanitize sensitive data)
   - Expected vs. actual behavior and potential impact
3. For already-known hardening gaps tracked by the project, see [`docs/security/licensing-and-security-audit.md`](docs/security/licensing-and-security-audit.md) and the open repository issues.

We aim to acknowledge reports promptly, validate reproducibility, prioritize remediation in `master`, and disclose fixes once a patch is merged.

---

## Security Controls Implemented in Milestone 1

The following defensive controls have been verified in the current `master` codebase:

- **Upload streaming size enforcement:** [`StorageService.save_validated_pdf`](backend/app/services/storage.py) streams uploads in `64 KiB` (`STREAM_CHUNK_SIZE`) chunks up to `MAX_UPLOAD_SIZE_MB` (default `25` MiB) before raising `FileTooLargeError` (`413`) and cleaning up temporary files.
- **PDF header & structural validation:** Rejects `0`-byte uploads (`400`), checks `%PDF-` signature within the first `1024` bytes (`PDF_HEADER_SCAN_BYTES`, `415`), rejects corrupt/unreadable PDFs via PyMuPDF (`415`), rejects encrypted/password-protected and `0`-page PDFs synchronously at upload (`422`), and fails zero-extractable-text PDFs (e.g., scanned image-only PDFs with `>= 1` page) asynchronously during background `PDFExtractor.extract` (`failed` status).
- **Path-traversal protection:** Document IDs are validated as UUIDs at the FastAPI boundary (`UUID`) and via regex (`_SAFE_DOCUMENT_ID_RE`), filenames are stripped of POSIX/Windows directory prefixes, drive-letter prefixes (`_WINDOWS_DRIVE_PREFIX_RE`), and ASCII control characters via `_sanitize_filename`, and `StorageService.resolve_path` enforces containment strictly inside `UPLOAD_DIR` (rejecting null bytes and escape paths).
- **Parameterized database queries:** [`DocumentRepository`](backend/app/repositories/document_repo.py) uses SQLAlchemy Core/ORM parameterized statements (no raw interpolated SQL strings) with SQLite `PRAGMA foreign_keys=ON` and WAL mode.
- **Per-document concurrency & cancellation safety:** [`IngestionService`](backend/app/services/ingestion.py) guards document state transitions with per-document `asyncio.Lock` instances and serializes ChromaDB I/O with `chroma_io_lock`, preventing orphan vectors/files on delete or failure.
- **Safe client rendering:** Frontend components render document filenames and error messages using standard React text nodes (no `dangerouslySetInnerHTML` or `innerHTML` sinks) and URL-encode document IDs with `encodeURIComponent`.
- **Exception sanitization on HTTP 500:** Unhandled FastAPI exceptions return a generic `INTERNAL_ERROR` JSON payload (`"An unexpected internal error occurred."`) rather than full Python tracebacks.
- **Secret & runtime artifact exclusion:** [`.gitignore`](.gitignore) excludes `.env`, `.env.*` (except examples), `data/`, `*.db*`, and `*.sqlite*`; git history contains only `.env.example` and `frontend/.env.example`.

---

## Known Limitations & Safe Deployment Requirements

Deployments running Milestone 1 (`0.1.0`) must observe these constraints:

1. **Bind locally (`127.0.0.1`) or front with an authenticating TLS reverse proxy:** No authentication, session management, or RBAC exists on any `/api/v1/*` route.
2. **Single-process execution only:** Always start uvicorn with `--workers 1`. Multi-worker uvicorn breaks in-process `asyncio.Lock` coordination, SQLite WAL single-writer expectations, and embedded ChromaDB thread safety.
3. **Restrict `CORS_ORIGINS`:** Keep `CORS_ORIGINS` limited to trusted origins (`http://localhost:3000`, `http://127.0.0.1:3000`). Because `CORSMiddleware` enables `allow_credentials=True` and wildcard methods/headers, never configure wildcard or untrusted origins.
4. **Do not ingest untrusted malicious PDFs without sandboxing:** PyMuPDF (`pymupdf`) parses PDFs in native C/C++ in-process, and DocSense does not scan uploads for malware or embedded active content.
5. **Degraded startup error handling:** If `EMBEDDING_MODEL` cannot load at startup, the backend starts in `degraded` mode (`/api/v1/health` reports `embedding_model_loaded: false`) and ingestion fails gracefully per document; monitor `/api/v1/health` before assuming ingestion capacity.
6. **Persistent `error_message` diagnostic detail:** Background ingestion failures currently record exception class names and exception strings in `Document.error_message` (visible via `GET /api/v1/documents`). A tracked remediation issue addresses redacting internal exception details from API-visible failure messages.
7. **HTTP security headers:** Next.js (`frontend/next.config.ts`) does not yet configure CSP, `X-Frame-Options`, `X-Content-Type-Options`, or `Referrer-Policy`. Configure these at the reverse proxy or in Next.js before external hosting.

---

## Security Audits & Threat Model

- **STRIDE Threat Model:** [`docs/security/threat-model.md`](docs/security/threat-model.md)
- **Dependency Vulnerability, Licensing & Code Security Audit:** [`docs/security/licensing-and-security-audit.md`](docs/security/licensing-and-security-audit.md)
- **Third-Party Software & AI Model Notices:** [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)

