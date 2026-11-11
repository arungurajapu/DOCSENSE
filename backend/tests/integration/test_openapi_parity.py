"""OpenAPI contract parity and import side-effect checks."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from tests.integration.conftest import make_isolated_settings

import app.main as app_main
from app.core.config import REPO_ROOT
from app.main import create_app

CONTRACT_PATH = REPO_ROOT / "contracts" / "openapi.json"


def _resolve_refs(
    node: Any,
    root: dict[str, Any],
    *,
    seen_refs: frozenset[str] = frozenset(),
) -> Any:
    """Recursively dereference local ``#/...`` JSON pointers for structural comparison."""
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
    """Normalize an OpenAPI operation with resolved refs, excluding non-contract prose."""
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
        content_schemas: dict[str, Any] = {}
        for media_type, media_obj in resp.get("content", {}).items():
            content_schemas[media_type] = media_obj.get("schema")
        normalized_responses[str(status_code)] = {
            "description": resp.get("description"),
            "content": content_schemas,
        }

    request_body: dict[str, Any] | None = None
    if "requestBody" in resolved:
        rb = resolved["requestBody"]
        rb_content = {
            media_type: media_obj.get("schema")
            for media_type, media_obj in rb.get("content", {}).items()
        }
        request_body = {
            "required": rb.get("required", False),
            "content": rb_content,
        }

    return {
        "operationId": resolved["operationId"],
        "summary": resolved.get("summary"),
        "tags": resolved.get("tags", []),
        "parameters": dict(sorted(params_by_key.items())),
        "requestBody": request_body,
        "responses": normalized_responses,
    }


def test_create_app_has_zero_import_or_construction_io_side_effects(tmp_path: Path) -> None:
    """Importing app.main and calling create_app() must not create DB/Chroma/upload files."""
    isolated = make_isolated_settings(tmp_path)
    app = create_app(isolated)

    assert app.title == "DocSense API"
    assert app_main.app is not None
    assert not (tmp_path / "db").exists()
    assert not (tmp_path / "chroma").exists()
    assert not (tmp_path / "uploads").exists()


def test_live_openapi_matches_frozen_contract(tmp_path: Path) -> None:
    """Verify full structural parity between live app.openapi() and contracts/openapi.json."""
    assert CONTRACT_PATH.exists(), f"Missing frozen OpenAPI contract at {CONTRACT_PATH}"
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    live_app = create_app(make_isolated_settings(tmp_path))
    live = live_app.openapi()

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

    assert set(live["paths"].keys()) == set(contract["paths"].keys())

    for path, contract_methods in contract["paths"].items():
        live_methods = live["paths"][path]
        assert set(live_methods.keys()) == set(contract_methods.keys())
        for method, contract_op in contract_methods.items():
            live_op = live_methods[method]
            assert _normalize_operation(live_op, live) == _normalize_operation(
                contract_op, contract
            )

            # Every 4xx/5xx response must reference #/components/schemas/ErrorResponse.
            for status_code, resp_def in live_op["responses"].items():
                if str(status_code).startswith(("4", "5")):
                    app_json = resp_def["content"]["application/json"]
                    assert app_json["schema"] == {"$ref": "#/components/schemas/ErrorResponse"}
