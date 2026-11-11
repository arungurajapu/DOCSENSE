"""Tri-layer OpenAPI contract parity E2E tests against live Uvicorn server."""

from __future__ import annotations

import copy
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import httpx
from tests.e2e.conftest import REPO_ROOT, run_live_backend

CONTRACT_PATH = REPO_ROOT / "contracts" / "openapi.json"
FRONTEND_DIR = REPO_ROOT / "frontend"
FRONTEND_GENERATED_TYPES = FRONTEND_DIR / "src" / "types" / "openapi.generated.ts"

EXPECTED_OPERATIONS = {
    ("/api/v1/health", "get"),
    ("/api/v1/documents", "post"),
    ("/api/v1/documents", "get"),
    ("/api/v1/documents/{document_id}", "get"),
    ("/api/v1/documents/{document_id}/reprocess", "post"),
    ("/api/v1/documents/{document_id}", "delete"),
}


def _resolve_refs(
    node: Any,
    root: dict[str, Any],
    *,
    seen_refs: frozenset[str] = frozenset(),
) -> Any:
    """Recursively dereference local ``#/...`` JSON pointers."""
    if isinstance(node, list):
        return [_resolve_refs(item, root, seen_refs=seen_refs) for item in node]
    if not isinstance(node, dict):
        return node

    ref = node.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/"):
        if ref in seen_refs:
            return {"$cycle_ref": ref}
        target: Any = root
        for part in ref[2:].split("/"):
            target = target[part]
        resolved = _resolve_refs(copy.deepcopy(target), root, seen_refs=seen_refs | {ref})
        siblings = {k: v for k, v in node.items() if k != "$ref"}
        if siblings and isinstance(resolved, dict):
            merged = dict(resolved)
            merged.update(_resolve_refs(siblings, root, seen_refs=seen_refs))
            return merged
        return resolved

    return {k: _resolve_refs(v, root, seen_refs=seen_refs) for k, v in node.items()}


def _normalize_operation(operation: dict[str, Any], root: dict[str, Any]) -> dict[str, Any]:
    """Normalize operation parameters, requestBody, and responses with $refs resolved."""
    resolved = _resolve_refs(operation, root)

    params_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for param in resolved.get("parameters", []):
        key = (param["in"], param["name"])
        params_by_key[key] = {
            "in": param["in"],
            "name": param["name"],
            "required": param.get("required", False),
            "description": param.get("description"),
            "schema": param["schema"],
        }

    normalized_responses: dict[str, Any] = {}
    for status_code, resp in resolved.get("responses", {}).items():
        content_schemas = {
            media_type: media_obj.get("schema")
            for media_type, media_obj in resp.get("content", {}).items()
        }
        normalized_responses[str(status_code)] = {
            "description": resp.get("description"),
            "content": content_schemas,
        }

    request_body: dict[str, Any] | None = None
    if "requestBody" in resolved:
        rb = resolved["requestBody"]
        request_body = {
            "required": rb.get("required", False),
            "content": {
                media_type: media_obj.get("schema")
                for media_type, media_obj in rb.get("content", {}).items()
            },
        }

    return {
        "operationId": resolved["operationId"],
        "summary": resolved.get("summary"),
        "tags": resolved.get("tags", []),
        "parameters": dict(sorted(params_by_key.items())),
        "requestBody": request_body,
        "responses": normalized_responses,
    }


def test_tri_layer_openapi_contract_parity(tmp_path: Path) -> None:
    """Verify live HTTP /openapi.json, contracts/openapi.json, and frontend types match."""
    assert CONTRACT_PATH.is_file(), f"Missing frozen OpenAPI contract: {CONTRACT_PATH}"
    assert FRONTEND_GENERATED_TYPES.is_file(), (
        f"Missing generated frontend contract types: {FRONTEND_GENERATED_TYPES}"
    )

    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))

    with run_live_backend(tmp_path) as server:
        with httpx.Client(base_url=server.base_url, timeout=5.0) as client:
            resp = client.get("/openapi.json")
            assert resp.status_code == 200
            live = resp.json()

    assert live["openapi"] == contract["openapi"]
    assert live["info"] == contract["info"]

    live_schemas = live["components"]["schemas"]
    contract_schemas = contract["components"]["schemas"]
    assert "HTTPValidationError" not in live_schemas
    assert "ValidationError" not in live_schemas
    assert set(live_schemas.keys()) == set(contract_schemas.keys())

    for schema_name in contract_schemas:
        assert _resolve_refs(live_schemas[schema_name], live) == _resolve_refs(
            contract_schemas[schema_name], contract
        )

    observed_operations: set[tuple[str, str]] = set()
    assert set(live["paths"].keys()) == set(contract["paths"].keys())

    for path, contract_methods in contract["paths"].items():
        live_methods = live["paths"][path]
        assert set(live_methods.keys()) == set(contract_methods.keys())
        for method, contract_op in contract_methods.items():
            observed_operations.add((path, method))
            live_op = live_methods[method]
            assert _normalize_operation(live_op, live) == _normalize_operation(
                contract_op, contract
            )
            for status_code, resp_def in live_op["responses"].items():
                if str(status_code).startswith(("4", "5")):
                    assert (
                        resp_def["content"]["application/json"]["schema"]
                        == {"$ref": "#/components/schemas/ErrorResponse"}
                    )

    assert observed_operations == EXPECTED_OPERATIONS

    # Layer 3: Verify frontend generated OpenAPI TypeScript contract is up-to-date.
    node_exe = shutil.which("node")
    assert node_exe is not None, "node executable not found on PATH"
    check_proc = subprocess.run(
        [node_exe, "scripts/sync-contracts.mjs", "--check"],
        cwd=str(FRONTEND_DIR),
        capture_output=True,
        text=True,
        check=False,
    )
    assert check_proc.returncode == 0, (
        "Frontend contract parity failed:\n"
        f"STDOUT:\n{check_proc.stdout}\nSTDERR:\n{check_proc.stderr}"
    )
