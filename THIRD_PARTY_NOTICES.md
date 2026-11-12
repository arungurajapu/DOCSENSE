# Third-Party Software & Model Notices

This file documents the third-party software packages and pretrained machine learning models referenced or locked by DocSense (`0.1.0`), generated from repository lockfiles (`backend/requirements.lock`, `frontend/package-lock.json`) and model metadata on **2026-11-12**.

> **First-Party Licensing Notice (Option B):** A root `LICENSE` file is intentionally omitted from the repository while **Issue #18** (`pymupdf` GNU AGPL-3.0 remediation) remains open. First-party DocSense source code is intended for release under the **MIT License** once PDF extraction licensing compatibility is resolved. See [`docs/security/licensing-and-security-audit.md`](docs/security/licensing-and-security-audit.md).

---

## 1. Pretrained AI Model & Tokenizer Notice

| Component | Identifier | Source | License | Usage in DocSense |
| --- | --- | --- | --- | --- |
| Embedding Model & Tokenizer | `sentence-transformers/all-MiniLM-L6-v2` (base model: `nreimers/MiniLM-L6-H384-uncased`) | HuggingFace Hub | `Apache-2.0` | Token-window chunking (`TokenChunker`) and 384-dim CPU vector embeddings (`EmbeddingService`) |

Model license obligations under Apache-2.0 apply when redistributing cached or bundled weights (retain upstream notices and Apache-2.0 license text).

---

## 2. Backend Python Dependencies (`backend/requirements.lock`)

Audited `115` pinned packages installed in an isolated Python 3.12 virtual environment using `pip-licenses --from=mixed --with-system --format=json` (filtered strictly to packages listed in `backend/requirements.lock`).

### 2.1 Copyleft, Weak-Copyleft & Unknown-Metadata Packages Calling for Attention

| Package | Version | Relationship | Reported License Metadata | Compliance Note |
| --- | --- | --- | --- | --- |
| `pymupdf` | `2.1` | Direct | `Dual Licensed - GNU AFFERO GPL 3.0 or Artifex Commercial License` | **Strong network copyleft (AGPL-3.0)** without a commercial license. Tracked in Issue #18. |
| `pymupdf4llm` | `2.1` | Transitive (`pymupdf` family) | `UNKNOWN` (PyPI metadata `null`) | Sibling Artifex package in the PyMuPDF ecosystem; treat under AGPL-3.0/commercial notice alongside `pymupdf` until replaced (Issue #18). |
| `pymupdf_layout` | `2.1` | Transitive (`pymupdf` family) | `UNKNOWN` (PyPI metadata `null`) | Sibling Artifex package in the PyMuPDF ecosystem; treat under AGPL-3.0/commercial notice alongside `pymupdf` until replaced (Issue #18). |
| `certifi` | `2026.10.23` | Transitive | `Mozilla Public License 2.0 (MPL 2.0)` | File-level weak copyleft; compatible with MIT application code if unmodified MPL source remains available. |
| `orjson` | `3.13.0` | Transitive | `MPL-2.0 AND (Apache-2.0 OR MIT)` | Includes MPL-2.0 component alongside Apache-2.0/MIT dual-licensed code. |
| `tqdm` | `4.70.1` | Transitive | `MPL-2.0 AND MIT` | Contains MPL-2.0 + MIT licensed files; retain notices upon redistribution. |

### 2.2 Complete Backend Locked Package Inventory (`115` Packages)

| Package | Version | Relationship | Reported License Metadata |
| --- | --- | --- | --- |
| `aiohappyeyeballs` | `2.7.1` | Transitive | Python Software Foundation License |
| `aiohttp` | `3.14.5` | Transitive | Apache-2.0 AND MIT |
| `aiohttp-retry` | `2.9.1` | Transitive | MIT License |
| `aiosignal` | `1.4.0` | Transitive | Apache Software License |
| `annotated-doc` | `0.0.6` | Transitive | MIT |
| `annotated-types` | `0.8.0` | Transitive | MIT |
| `anyio` | `4.16.0` | Transitive | MIT |
| `attrs` | `26.1.0` | Transitive | MIT |
| `bcrypt` | `5.0.0` | Transitive | Apache Software License |
| `build` | `1.6.1` | Transitive | MIT |
| `certifi` | `2026.10.23` | Transitive | Mozilla Public License 2.0 (MPL 2.0) |
| `charset-normalizer` | `3.5.2` | Transitive | MIT |
| `chromadb` | `1.5.9` | Direct | Apache Software License |
| `click` | `8.5.1` | Transitive | BSD-3-Clause |
| `cloudpickle` | `3.1.2` | Transitive | BSD License |
| `colorama` | `0.4.6` | Transitive | BSD License |
| `durationpy` | `0.11` | Transitive | MIT |
| `fastapi` | `0.162.4` | Direct | MIT |
| `filelock` | `4.2.10` | Transitive | MIT |
| `flatbuffers` | `25.12.19` | Transitive | Apache Software License |
| `frozenlist` | `1.8.0` | Transitive | Apache-2.0 |
| `fsspec` | `2026.9.0` | Transitive | BSD-3-Clause |
| `googleapis-common-protos` | `1.76.0` | Transitive | Apache-2.0 |
| `grpcio` | `1.85.1` | Transitive | Apache-2.0 |
| `h11` | `0.16.0` | Transitive | MIT License |
| `hf-xet` | `1.8.0` | Transitive | Apache-2.0 |
| `httpcore` | `1.0.9` | Transitive | BSD-3-Clause |
| `httpcore2` | `2.16.0` | Transitive | BSD-3-Clause |
| `httptools` | `0.9.0` | Transitive | MIT |
| `httpx` | `0.28.1` | Direct | BSD License |
| `httpx2` | `2.16.0` | Transitive | BSD-3-Clause |
| `huggingface_hub` | `2.4.0` | Transitive | Apache Software License |
| `idna` | `3.20` | Transitive | BSD-3-Clause |
| `importlib_resources` | `7.1.0` | Transitive | Apache-2.0 |
| `iniconfig` | `2.3.1` | Transitive | MIT |
| `Jinja2` | `3.1.6` | Transitive | BSD License |
| `joblib` | `1.6.0` | Transitive | BSD-3-Clause |
| `jsonschema` | `4.26.0` | Transitive | MIT |
| `jsonschema-specifications` | `2025.9.1` | Transitive | MIT |
| `kubernetes` | `37.0.2` | Transitive | Apache-2.0 |
| `markdown-it-py` | `4.3.0` | Transitive | MIT License |
| `MarkupSafe` | `3.0.4` | Transitive | BSD-3-Clause |
| `mdurl` | `0.1.2` | Transitive | MIT License |
| `mmh3` | `5.3.1` | Transitive | MIT License |
| `mpmath` | `1.3.0` | Transitive | BSD License |
| `multidict` | `7.2.1` | Transitive | Apache-2.0 |
| `narwhals` | `2.28.0` | Transitive | MIT |
| `networkx` | `3.7` | Transitive | BSD-3-Clause |
| `numpy` | `2.5.4` | Transitive | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 |
| `oauthlib` | `4.0.1` | Transitive | BSD-3-Clause |
| `onnxruntime` | `1.31.1` | Transitive | MIT License |
| `opentelemetry-api` | `1.45.2` | Transitive | Apache-2.0 |
| `opentelemetry-exporter-otlp-common` | `0.66b2` | Transitive | Apache-2.0 |
| `opentelemetry-exporter-otlp-proto-common` | `1.45.2` | Transitive | Apache-2.0 |
| `opentelemetry-exporter-otlp-proto-grpc` | `1.45.2` | Transitive | Apache-2.0 |
| `opentelemetry-proto` | `1.45.2` | Transitive | Apache-2.0 |
| `opentelemetry-sdk` | `1.45.2` | Transitive | Apache-2.0 |
| `opentelemetry-semantic-conventions` | `0.66b2` | Transitive | Apache-2.0 |
| `orjson` | `3.13.0` | Transitive | MPL-2.0 AND (Apache-2.0 OR MIT) |
| `overrides` | `7.7.0` | Transitive | Apache License, Version 2.0 |
| `packaging` | `26.3` | Transitive | Apache-2.0 OR BSD-2-Clause |
| `pluggy` | `1.7.0` | Transitive | MIT License |
| `propcache` | `0.5.4` | Transitive | Apache-2.0 |
| `protobuf` | `7.36.2` | Transitive | 3-Clause BSD License |
| `psutil` | `7.2.2` | Transitive | BSD-3-Clause |
| `pybase64` | `1.5.2` | Transitive | BSD-2-Clause |
| `pydantic` | `2.14.3` | Direct | MIT |
| `pydantic-settings` | `2.16.2` | Direct | MIT |
| `pydantic_core` | `2.50.4` | Transitive | MIT |
| `Pygments` | `2.21.0` | Transitive | BSD-2-Clause |
| `pymupdf` | `2.1` | Direct | Dual Licensed - GNU AFFERO GPL 3.0 or Artifex Commercial License |
| `pymupdf4llm` | `2.1` | Transitive | UNKNOWN |
| `pymupdf_layout` | `2.1` | Transitive | UNKNOWN |
| `PyPika` | `0.51.1` | Transitive | Apache Software License |
| `pyproject_hooks` | `1.3.3` | Transitive | MIT |
| `pytest` | `9.2.0` | Direct | MIT |
| `python-dateutil` | `4.0.0` | Transitive | BSD-3-Clause |
| `python-dotenv` | `1.2.4` | Transitive | BSD-3-Clause |
| `python-multipart` | `0.0.33` | Direct | Apache-2.0 |
| `PyYAML` | `6.0.3` | Transitive | MIT License |
| `referencing` | `0.37.0` | Transitive | MIT |
| `regex` | `2026.11.9` | Transitive | Apache-2.0 AND CNRI-Python |
| `requests` | `2.34.2` | Transitive | Apache Software License |
| `requests-oauthlib` | `2.0.0` | Transitive | BSD License |
| `rich` | `15.0.0` | Transitive | MIT License |
| `rpds-py` | `2026.9.1` | Transitive | MIT |
| `ruff` | `0.17.4` | Direct | MIT |
| `safetensors` | `0.8.0` | Transitive | Apache Software License |
| `scikit-learn` | `1.9.1` | Transitive | BSD-3-Clause |
| `scipy` | `1.18.1` | Transitive | BSD License |
| `sentence-transformers` | `6.2.0` | Direct | Apache-2.0 |
| `setuptools` | `84.2.1` | Transitive | MIT |
| `shellingham` | `1.5.4` | Transitive | ISC License (ISCL) |
| `SQLAlchemy` | `2.1.8` | Direct | MIT |
| `starlette` | `1.10.0` | Transitive | BSD-3-Clause |
| `sympy` | `1.14.0` | Transitive | BSD License |
| `tabulate` | `0.10.0` | Transitive | MIT |
| `tenacity` | `9.2.1` | Transitive | Apache-2.0 |
| `threadpoolctl` | `3.8.0` | Transitive | BSD-3-Clause |
| `tokenizers` | `0.23.3` | Transitive | Apache Software License |
| `torch` | `2.15.0+cpu` | Direct | Apache-2.0 AND Apache-2.0 WITH LLVM-exception AND BSD-2-Clause AND BSD-3-Clause AND BSL-1.0 AND ISC AND MIT AND Zlib |
| `tqdm` | `4.70.1` | Transitive | MPL-2.0 AND MIT |
| `transformers` | `5.22.2` | Direct | Apache 2.0 License |
| `truststore` | `0.10.5` | Transitive | MIT |
| `typer` | `0.29.1` | Transitive | MIT |
| `typing-inspection` | `0.4.5` | Transitive | MIT |
| `typing_extensions` | `4.16.0` | Transitive | PSF-2.0 |
| `tzdata` | `2026.6` | Transitive | Apache-2.0 |
| `urllib3` | `2.8.0` | Transitive | MIT |
| `uvicorn` | `0.54.0` | Direct | BSD-3-Clause |
| `watchfiles` | `1.3.0` | Transitive | MIT License |
| `websocket-client` | `1.9.2` | Transitive | Apache-2.0 |
| `websockets` | `17.4` | Transitive | BSD-3-Clause |
| `wheel` | `0.48.0` | Transitive | MIT |
| `yarl` | `1.25.1` | Transitive | Apache-2.0 |

---

## 3. Frontend JavaScript / Node Dependencies (`frontend/package-lock.json`)

Extracted from all `581` package entries in `frontend/package-lock.json` (`0` entries missing license metadata).

### 3.1 Direct Runtime & Development Dependencies

| Package | Locked Version | Scope | License |
| --- | --- | --- | --- |
| `next` | `16.4.6` | Runtime | `MIT` |
| `react` | `19.3.1` | Runtime | `MIT` |
| `react-dom` | `19.3.1` | Runtime | `MIT` |
| `@playwright/test` | `1.65.0` | Dev/E2E | `Apache-2.0` |
| `@tailwindcss/postcss` | `4.3.5` | Dev/Build | `MIT` |
| `@testing-library/dom` | `10.4.2` | Dev/Test | `MIT` |
| `@testing-library/jest-dom` | `6.9.1` | Dev/Test | `MIT` |
| `@testing-library/react` | `16.3.3` | Dev/Test | `MIT` |
| `@testing-library/user-event` | `14.6.9` | Dev/Test | `MIT` |
| `@types/node` | `20.19.43` | Dev/Types | `MIT` |
| `@types/react` | `19.3.0` | Dev/Types | `MIT` |
| `@types/react-dom` | `19.3.0` | Dev/Types | `MIT` |
| `@vitejs/plugin-react` | `6.3.0` | Dev/Test | `MIT` |
| `eslint` | `9.39.5` | Dev/Lint | `MIT` |
| `eslint-config-next` | `16.4.6` | Dev/Lint | `MIT` |
| `jsdom` | `28.1.0` | Dev/Test | `MIT` |
| `openapi-typescript` | `7.10.1` | Dev/Contract | `MIT` |
| `tailwindcss` | `4.3.5` | Dev/Build | `MIT` |
| `typescript` | `5.9.3` | Dev/Build | `Apache-2.0` |
| `vitest` | `4.1.12` | Dev/Test | `MIT` |

### 3.2 Transitive Frontend License Distribution (`581` Total Lockfile Entries)

| SPDX / License Identifier | Package Count | Notes |
| --- | --- | --- |
| `MIT` | `468` | Permissive |
| `Apache-2.0` | `41` | Permissive |
| `ISC` | `21` | Permissive |
| `MPL-2.0` | `13` | Weak file-level copyleft (e.g., CSS/tooling transitive deps) |
| `LGPL-3.0-or-later` | `10` | `@img/sharp-libvips-*` optional native binary packages pulled via `next` (`1.3.4`) |
| `BSD-2-Clause` | `9` | Permissive |
| `BSD-3-Clause` | `3` | Permissive |
| `BlueOak-1.0.0` | `3` | Permissive |
| `Apache-2.0 AND LGPL-3.0-or-later` | `3` | `@img/sharp-win32-*` optional native binary packages (`0.35.5`) |
| `Apache-2.0 AND LGPL-3.0-or-later AND MIT` | `1` | `@img/sharp-wasm32` (`0.35.5`) |
| `0BSD` | `2` | Permissive |
| `CC0-1.0` | `2` | Public domain equivalent |
| `MIT-0` | `2` | `@csstools/color-helpers`, `@csstools/css-syntax-patches-for-csstree` (`dev: true`) |
| `(MIT OR CC0-1.0)` | `1` | Permissive dual license |
| `CC-BY-4.0` | `1` | Attribution notice |
| `Python-2.0` | `1` | Permissive (PSF-2.0 transitive build tooling) |

**Optional `sharp` / `libvips` (`LGPL-3.0-or-later`) Note:** `next` declares `sharp` optionally for runtime image optimization (`@img/sharp-libvips-*` `1.3.4`, `@img/sharp-win32-*` `0.35.5`, `@img/sharp-wasm32` `0.35.5`). Under LGPL-3.0-or-later, using these unmodified shared/dynamically loaded components does not relicense DocSense's application source, though redistributing container images or binary bundles that include `@img/sharp-libvips-*` requires preserving LGPL-3.0 notices and relinking/replacement compliance.

### 3.3 Dev / QA Browser Binary Notice

Running frontend E2E tests (`npx playwright install chromium`) downloads a Playwright-managed **Chromium** headless browser binary (`BSD-3-Clause` Chromium components plus upstream third-party media/codec notices). This browser binary is used strictly during local/CI QA testing (`frontend/tests/e2e/`) and is not part of the DocSense application runtime.

