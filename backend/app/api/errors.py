"""Unified FastAPI exception handlers returning ErrorResponse payloads."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.exceptions import DocSenseError

logger = logging.getLogger(__name__)

_HTTP_STATUS_CODES: dict[int, str] = {
    400: "BAD_REQUEST",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    413: "FILE_TOO_LARGE",
    415: "UNSUPPORTED_MEDIA_TYPE",
    422: "VALIDATION_ERROR",
    503: "SERVICE_UNAVAILABLE",
}


def _format_validation_message(exc: RequestValidationError) -> str:
    """Create a concise human-readable message from FastAPI validation errors."""
    parts: list[str] = []
    for err in exc.errors():
        raw_loc = err.get("loc", ())
        loc_str = ".".join(str(segment) for segment in raw_loc if segment != "body")
        msg = str(err.get("msg", "Invalid input"))
        parts.append(f"{loc_str}: {msg}" if loc_str else msg)
    return "; ".join(parts) if parts else "Request validation failed."


def _error_response(
    *,
    status_code: int,
    code: str,
    message: str,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
        headers=headers,
    )


async def _handle_docsense_error(_request: Request, exc: DocSenseError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content=exc.to_error_payload())


async def _handle_validation_error(
    _request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    return _error_response(
        status_code=422,
        code="VALIDATION_ERROR",
        message=_format_validation_message(exc),
    )


async def _handle_http_exception(
    _request: Request,
    exc: StarletteHTTPException,
) -> JSONResponse:
    detail: Any = exc.detail
    if isinstance(detail, dict) and "error" in detail:
        return JSONResponse(
            status_code=exc.status_code,
            content=detail,
            headers=dict(exc.headers) if exc.headers else None,
        )
    code = _HTTP_STATUS_CODES.get(exc.status_code, f"HTTP_{exc.status_code}")
    message = str(detail) if detail else "HTTP error."
    return _error_response(
        status_code=exc.status_code,
        code=code,
        message=message,
        headers=dict(exc.headers) if exc.headers else None,
    )


async def _handle_unhandled_exception(request: Request, _exc: Exception) -> JSONResponse:
    logger.exception(
        "Unhandled exception while processing %s %s",
        request.method,
        request.url.path,
    )
    return _error_response(
        status_code=500,
        code="INTERNAL_ERROR",
        message="An unexpected internal error occurred.",
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Register unified ErrorResponse handlers on the FastAPI application."""
    app.add_exception_handler(DocSenseError, _handle_docsense_error)  # type: ignore[arg-type]
    app.add_exception_handler(
        RequestValidationError,
        _handle_validation_error,  # type: ignore[arg-type]
    )
    app.add_exception_handler(
        StarletteHTTPException,
        _handle_http_exception,  # type: ignore[arg-type]
    )
    app.add_exception_handler(Exception, _handle_unhandled_exception)


__all__ = ["register_exception_handlers"]
