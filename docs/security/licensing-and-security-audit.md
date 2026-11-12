# DocSense Security, Dependency & Licensing Audit (TASK-7)

**Audit Date:** 2026-11-12  
**Audited Baseline:** `master` commit `a9c23d7f28763a4e1e40b3903fda78316dbcb02f` (`DocSense 0.1.0`, Milestone 1)  
**Scope:** Historical PR & ownership verification (#8–#17), repository secret hygiene, read-only codebase security audit, Python (`backend/requirements.lock`) & npm (`frontend/package-lock.json`) vulnerability scans, third-party & pretrained model licensing inventory, STRIDE threat model, MIT compatibility assessment, and prioritized remediation issues.

> **Disclaimer:** This audit reflects automated tool output and manual code review of Milestone 1 (`0.1.0`) as of `a9c23d7`. It does **not** constitute formal legal advice, an external penetration test, or a security certification.

---

## 1. Executive Summary

| Audit Pillar | Outcome | Key Finding |
| --- | --- | --- |
| **Historical PR & Secret Hygiene** | **PASS** | PRs #8–#17 match assigned role ownership; git history contains only `.env.example` & `frontend/.env.example` (zero `.env`, `.db`, `.sqlite`, upload, or Chroma files). |
| **Python Vulnerability Scan (`pip-audit`)** | **FINDINGS** | `112` locked deps evaluated (`+3` installer/build tools = `115` locked): `110` reported `0` advisories, `1` (`chromadb==1.5.9`) had `5` advisory entries (`4` distinct CVEs, `fix_versions: []`), and `1` (`torch==2.15.0+cpu` from the CPU PyTorch index) was **skipped** by `pip-audit` (`not found on PyPI`). Low Milestone 1 exposure for ChromaDB in embedded mode; tracked in **Issue #28** (`Ready`). |
| **Frontend Vulnerability Scan (`npm audit`)** | **FINDINGS** | `8` **moderate** advisories (`0` critical, `0` high, `0` low), all chaining through dev-only `eslint-config-next` -> `typescript-eslint` -> `minimatch` (`GHSA-g3hv-75wg-m249`). Non-runtime dev exposure; tracked in **Issue #22** (`Ready`). |
| **Code Security & STRIDE Audit** | **PASS WITH HARDENING GAPS** | Core Milestone 1 input validation, path containment, parameterized SQL, concurrency locking, HTTP 500 masking, and React text escaping are solid; missing auth, bounded ingestion concurrency/page limits, Next.js security headers, and `error_message` redaction are tracked in **Issues #19–#21** (`Ready`) and **#23–#27** (`Backlog`). |
| **Third-Party & MIT Compatibility** | **BLOCKER (Option B)** | Backend includes `pymupdf==2.1` (**GNU AGPL-3.0** / commercial dual license) plus sibling packages `pymupdf4llm==2.1` & `pymupdf_layout==2.1`. Root MIT `LICENSE` is intentionally deferred until **Issue #18** resolves PyMuPDF replacement or commercial licensing. |

---

## 2. Historical PR (#8–#17) & Repository Hygiene Verification

### 2.1 Merged Pull Request Audit

| PR | Issue | Branch | Merge Commit | Merged (UTC) | Scope & Ownership |
| --- | --- | --- | --- | --- | --- |
| **#8** | [#1](https://github.com/arungurajapu/DOCSENSE/issues/1) | `agent/architect/issue-1` | `c918db0` | 2026-11-11T11:31:37Z | TASK-1: Architecture foundation, Py3.12 lock, SQLite models, OpenAPI contract (`26` files) |
| **#9** | [#2](https://github.com/arungurajapu/DOCSENSE/issues/2) | `agent/backend/issue-2` | `daa1c73` | 2026-11-11T13:19:21Z | TASK-2: `StorageService`, `PDFExtractor`, `TokenChunker` & unit tests (`6` files) |
| **#10** | [#3](https://github.com/arungurajapu/DOCSENSE/issues/3) | `agent/backend/issue-3` | `fb9e15f` | 2026-11-11T13:19:36Z | TASK-3: `EmbeddingService`, `ChromaVectorStore` & unit tests (`5` files) |
| **#11** | [#4](https://github.com/arungurajapu/DOCSENSE/issues/4) | `agent/frontend/issue-4` | `012d054` | 2026-11-11T13:38:51Z | TASK-4: Next.js + TypeScript UI components, API client & Vitest suite (`30` files) |
| **#12** | [#5](https://github.com/arungurajapu/DOCSENSE/issues/5) | `agent/backend/issue-5` | `4c90fef` | 2026-11-11T14:40:21Z | TASK-5: `IngestionService`, `DocumentRepository`, FastAPI routers & integration tests (`15` files) |
| **#15** | [#13](https://github.com/arungurajapu/DOCSENSE/issues/13) | `agent/backend/issue-13` | `8957c14` | 2026-11-11T16:33:26Z | Bugfix: Degraded backend startup when `EMBEDDING_MODEL` tokenizer fails (`4` files) |
| **#16** | [#6](https://github.com/arungurajapu/DOCSENSE/issues/6) | `agent/qa/issue-6` | `b78a53f` | 2026-11-11T17:16:08Z | TASK-6: Playwright + backend E2E QA suite & ESLint output ignores (`20` files; zero prod code edits) |
| **#17** | [#14](https://github.com/arungurajapu/DOCSENSE/issues/14) | `agent/frontend/issue-14` | `a9c23d7` | 2026-11-12T07:03:24Z | Bugfix: Eliminate duplicate accessible `Choose PDF file` button role (`4` files) |

### 2.2 Git History Secret & Runtime Data Scan

Ran `git log --all --name-only` filtering for `.env`, `.db`, `.sqlite`, `data/`, `uploads/`, and `chroma/` artifacts:
- **Tracked matches:** `.env.example` and `frontend/.env.example` only.
- **Unwanted secrets/databases/uploads in git history:** `0`.
- [`.gitignore`](../../.gitignore) excludes `.env`, `.env.*` (with `!.env.example`), `data/`, `*.db*`, and `*.sqlite*`.

---

## 3. Dependency Vulnerability Audit

### 3.1 Python Backend (`pip-audit -r backend/requirements.lock --format json`)

Executed in a clean temporary Python 3.12 virtual environment (`pip-audit 2.10.1`). Of `115` locked packages (`packaging`, `setuptools`, and `wheel` are installer/build tooling excluded from dependency auditing), `112` entries were evaluated by `pip-audit`:
- **`110` dependencies** returned `vulns: []` (zero known advisories).
- **`1` dependency (`torch==2.15.0+cpu`)** was **skipped** (`"Dependency not found on PyPI and could not be audited: torch (2.15.0+cpu)"`) because the `+cpu` build originates from `https://download.pytorch.org/whl/cpu` rather than PyPI, so PyTorch vulnerabilities are not automatically covered by PyPI-only `pip-audit` lookups.
- **`1` dependency (`chromadb==1.5.9`)** accounted for all **5 advisory entries (4 unique CVEs)**, all with `fix_versions: []`:

| Advisory IDs | Package & Version | Summary | Milestone 1 Exposure | Remediation |
| --- | --- | --- | --- | --- |
| `PYSEC-2026-311` (x2) / `CVE-2026-45829` / `GHSA-f4j7-r4q5-qw2c` | `chromadb==1.5.9` | Pre-auth code injection in Chroma `/api/v2/.../collections` via malicious model repo + `trust_remote_code=true` | **Low (not directly reachable):** DocSense uses embedded `PersistentClient` only and does not expose Chroma's HTTP `/api/v2` server or user-controlled `trust_remote_code`. | **Issue #28** (`Ready`): Monitor & upgrade `chromadb` once patched release publishes |
| `PYSEC-2026-3814` / `CVE-2026-45833` / `GHSA-36p7-vc44-83pf` | `chromadb==1.5.9` | Authenticated code injection in `/api/v2/.../collections/{collection_id}` (`UPDATE_COLLECTION` + `trust_remote_code=true`) | **Low (not directly reachable)** in embedded single-client mode | **Issue #28** (`Ready`) |
| `PYSEC-2026-3815` / `CVE-2026-45831` / `GHSA-xph7-9rjv-w5fr` | `chromadb==1.5.9` | `SimpleRBACAuthorizationProvider` cross-tenant permission bypass | **Low (not directly reachable):** RBAC server provider unused | **Issue #28** (`Ready`) |
| `PYSEC-2026-3813` / `CVE-2026-45830` / `GHSA-2wm9-hf6c-p5cr` | `chromadb==1.5.9` | Missing tenant authorization check on collection read/write/update/delete | **Low (not directly reachable):** Embedded single-tenant store | **Issue #28** (`Ready`) |

### 3.2 Frontend JavaScript (`npm audit --package-lock-only --json`)

Executed against `frontend/package-lock.json` (`581` lockfile entries):
- **Summary:** `0` critical, `0` high, **`8` moderate**, `0` low (`total: 8`).
- **Root Advisory:** `1244623` / `GHSA-g3hv-75wg-m249` — *minimatch has potential denial of service via globparts amplification* (`moderate`, `minimatch` `7.0.0 - 11.0.0`).
- **Dependency Chain:** Dev-only `eslint-config-next` (`16.4.6`) -> `typescript-eslint` -> `@typescript-eslint/eslint-plugin`, `@typescript-eslint/parser`, `@typescript-eslint/type-utils`, `@typescript-eslint/typescript-estree`, `@typescript-eslint/utils` -> `minimatch`.
- **Exposure & Remediation:** Does not ship in `next build` client runtime assets or execute on untrusted HTTP input. Because `npm audit`'s automated recommendation downgrades `eslint-config-next` to major `15.5.29`, controlled remediation is tracked in **Issue #22** (`Ready`).

---

## 4. Code Security Audit & Threat Model Summary

Detailed trust-boundary diagrams and full STRIDE tables live in [`docs/security/threat-model.md`](threat-model.md). Key verified code observations:

### Verified Controls
- **File Upload & Path Containment (`backend/app/services/storage.py`):** Streams chunks (`64 KiB`) enforcing `MAX_UPLOAD_SIZE_MB` (`25` MiB default -> HTTP `413`), rejects `0`-byte files (`400`), checks `%PDF-` in `PDF_HEADER_SCAN_BYTES` (`1024`, `415`), rejects corrupt PDFs (`415`) and encrypted/0-page PDFs (`422`), sanitizes filenames, validates UUIDs, checks `Path.is_relative_to(UPLOAD_DIR)`, and atomically promotes via `os.replace`.
- **SQL & Race Safety (`backend/app/repositories/document_repo.py`, `ingestion.py`, `vector_store.py`):** Parameterized SQLAlchemy statements (zero string-interpolated SQL), `PRAGMA foreign_keys=ON`, WAL mode, per-document `asyncio.Lock`, `chroma_io_lock`, compensating vector deletes, and `await file.close()`.
- **HTTP 500 Sanitization (`backend/app/api/errors.py`):** Unhandled FastAPI exceptions return generic `{"error": {"code": "INTERNAL_ERROR", "message": "An unexpected internal error occurred."}}`.
- **Frontend Rendering (`frontend/src/`):** Zero matches for `dangerouslySetInnerHTML`, `innerHTML`, `eval(`, or `Function(`; React escapes filenames and error strings; `api.ts` applies `encodeURIComponent(documentId)`.

### Confirmed Hardening Gaps
1. **Unbounded ingestion concurrency & missing page/time limits (`backend/app/services/ingestion.py`, `extractor.py`):** Multiple uploads create unbounded background `asyncio.Task` runners without an `asyncio.Semaphore`, `MAX_PDF_PAGES`, or parse timeout (**Issue #19**).
2. **Error detail disclosure (`ingestion.py:402-407`, `extractor.py`, `storage.py`):** Generic `Exception` catch blocks store `INTERNAL_ERROR: Unexpected ingestion failure ({type(exc).__name__}): {detail}` into `Document.error_message` (surfaced via `GET /api/v1/documents`), and `ExtractionError`/`StorageError` include filenames/exception text (**Issue #20**).
3. **Missing browser security headers (`frontend/next.config.ts`):** Only `reactStrictMode: true` is set; no CSP, `X-Frame-Options`, `X-Content-Type-Options`, or `Referrer-Policy` (**Issue #21**).
4. **Milestone 2+ network/multi-user controls:** No authentication/RBAC/audit logs (**Issue #23**), no HTTP rate limiting or storage quota (**Issue #24**), in-process native PyMuPDF execution without OS sandbox (**Issue #25**), unpinned HuggingFace revision + wildcard CORS methods/headers with `allow_credentials=True` (**Issue #26**), and `--workers 1` single-process lock/SQLite boundary (**Issue #27**).

---

## 5. Third-Party & AI Model Licensing Inventory + MIT Compatibility Assessment

Full package tables are published in [`THIRD_PARTY_NOTICES.md`](../../THIRD_PARTY_NOTICES.md).

### 5.1 Distribution Summary

- **Pretrained AI Model:** `sentence-transformers/all-MiniLM-L6-v2` -> `Apache-2.0` (compatible with MIT distribution; retain Apache-2.0 notice if redistributing weights).
- **Backend (`115` locked packages):**
  - `109` packages under standard permissive licenses (`MIT`, `Apache-2.0`, `BSD-2-Clause`, `BSD-3-Clause`, `ISC`, `PSF-2.0`, or multi-permissive combinations such as `torch`'s `Apache-2.0 AND BSD-* AND BSL-1.0 AND ISC AND MIT AND Zlib`).
  - `3` packages with weak file-level copyleft (`certifi`: `MPL-2.0`; `orjson`: `MPL-2.0 AND (Apache-2.0 OR MIT)`; `tqdm`: `MPL-2.0 AND MIT`) — compatible with an MIT-licensed Python application provided unmodified MPL-2.0 source availability & notices are respected.
  - **`1` direct copyleft blocker (`pymupdf==2.1`):** Dual-licensed under **GNU Affero GPL v3.0 (AGPL-3.0)** or a **Commercial Artifex Software License**.
  - **`2` sibling packages with `UNKNOWN` PyPI metadata (`pymupdf4llm==2.1`, `pymupdf_layout==2.1`):** Part of Artifex's PyMuPDF ecosystem; treated under the same AGPL-3.0 / commercial license boundary as `pymupdf`.
- **Frontend (`581` npm lockfile entries):**
  - `554` permissive packages (`MIT`, `Apache-2.0`, `ISC`, `BSD-*`, `0BSD`, `BlueOak-1.0.0`, `MIT-0`, `CC0-1.0`, `CC-BY-4.0`, `Python-2.0`).
  - `13` transitive tooling/dev packages under `MPL-2.0`.
  - `14` optional `sharp` native image packages (`@img/sharp-libvips-*` under `LGPL-3.0-or-later`; `@img/sharp-win32-*` under `Apache-2.0 AND LGPL-3.0-or-later`; `@img/sharp-wasm32` under `Apache-2.0 AND LGPL-3.0-or-later AND MIT`) pulled optionally by `next`. Under LGPL-3.0-or-later, dynamic/modular use does not relicense DocSense application code, though container/binary redistribution must preserve LGPL-3.0 notices and library replacement rights.
- **Dev QA Browser (`Playwright Chromium`):** Installed only for headless E2E testing (`npx playwright install chromium`), never bundled into runtime.

### 5.2 MIT Compatibility Assessment & Option B Decision

1. **First-party code intent:** DocSense's first-party Python (`backend/app/**`), TypeScript (`frontend/src/**`), and schema (`contracts/**`) source files contain no copyleft headers and are structured to permit **MIT** licensing—including isolating PDF parsing cleanly behind `PDFExtractor` and `StorageService` (see [`docs/architecture/licensing-and-ingestion.md`](../architecture/licensing-and-ingestion.md)).
2. **Why `pymupdf` blocks an unqualified root MIT `LICENSE` today:**
   - Under **AGPL-3.0 Section 13 (Remote Network Interaction)**, if a modified/combined work based on AGPL-3.0 software interacts with users over a computer network, the entire corresponding source of the combined work must be offered to network users under AGPL-3.0.
   - Because DocSense directly imports `pymupdf` inside its FastAPI backend (`backend/app/services/extractor.py`, `backend/app/services/storage.py`) and serves a network API consumed by the Next.js UI, redistributing or hosting the full application without an Artifex commercial license triggers AGPL-3.0 terms for the combined program—which conflicts with a simplistic repository-wide "MIT only" license claim.
   - **Technical side-benefit of replacement:** PyMuPDF executes native MuPDF C/C++ parsing inside the FastAPI worker process; replacing it behind `PDFExtractor` and `StorageService` (or isolating parsing into a sandbox) both clears AGPL-3.0 obligations and reduces native memory-safety exposure (**Issues #18 & #25**).
3. **Approved Resolution (Option B):**
   - Root `LICENSE` is **withheld** in PR for Issue #7.
   - Users & portfolio reviewers are informed explicitly via `README.md`, `THIRD_PARTY_NOTICES.md`, and this audit.
   - Once **Issue #18** replaces `pymupdf`/`pymupdf4llm`/`pymupdf_layout` with a permissive PDF library (or documents an Artifex commercial license), add the root MIT `LICENSE` and update `THIRD_PARTY_NOTICES.md`.

---

## 6. Tracked Remediation Issues (GitHub Issues & Project #2)

All confirmed findings are filed without modifying application code in TASK-7:

### Ready (Immediate Pre-Release Hardening & Licensing)

| Issue | Category | Priority | Agent Role | Summary |
| --- | --- | --- | --- | --- |
| [#18](https://github.com/arungurajapu/DOCSENSE/issues/18) | Licensing | High | Architect | Replace PyMuPDF (`pymupdf`, `pymupdf4llm`, `pymupdf_layout` AGPL-3.0) or obtain commercial license before adding root MIT `LICENSE` |
| [#19](https://github.com/arungurajapu/DOCSENSE/issues/19) | Security | High | Backend | Bound concurrent background ingestions (`asyncio.Semaphore`) and enforce PDF page-count / extraction timeout limits |
| [#20](https://github.com/arungurajapu/DOCSENSE/issues/20) | Security | Medium | Backend | Redact internal exception class names and paths from API-visible `Document.error_message` and error responses |
| [#21](https://github.com/arungurajapu/DOCSENSE/issues/21) | Security | Medium | Frontend | Configure HTTP security headers (CSP, `X-Frame-Options`, `X-Content-Type-Options`, `Referrer-Policy`) in `frontend/next.config.ts` |
| [#28](https://github.com/arungurajapu/DOCSENSE/issues/28) | Security | Medium | Backend | Remediate/upgrade `chromadb==1.5.9` once patched upstream for `CVE-2026-45829`, `CVE-2026-45830`, `CVE-2026-45831`, `CVE-2026-45833` |
| [#22](https://github.com/arungurajapu/DOCSENSE/issues/22) | Security | Low | Frontend | Remediate moderate dev-only `minimatch` ReDoS (`GHSA-g3hv-75wg-m249`) via `eslint-config-next` / `@typescript-eslint` |

### Backlog (Milestone 2+ Hardening & Scalability)

| Issue | Category | Priority | Agent Role | Summary |
| --- | --- | --- | --- | --- |
| [#23](https://github.com/arungurajapu/DOCSENSE/issues/23) | Security | High | Backend | Add authentication, authorization / document ownership isolation, and audit logging |
| [#24](https://github.com/arungurajapu/DOCSENSE/issues/24) | Security | Medium | Backend | Add HTTP rate limiting and upload storage disk quota enforcement |
| [#25](https://github.com/arungurajapu/DOCSENSE/issues/25) | Security | Medium | Backend | Sandbox native PDF parsing in an isolated subprocess and evaluate malware/active-content scanning |
| [#26](https://github.com/arungurajapu/DOCSENSE/issues/26) | Security | Low | Backend | Pin HuggingFace `EMBEDDING_MODEL` commit revision/checksums and restrict CORS methods/headers |
| [#27](https://github.com/arungurajapu/DOCSENSE/issues/27) | Architecture | Medium | Architect | Replace single-process `--workers 1` in-memory locks/SQLite/embedded Chroma for multi-worker scaling |
