"""Uniform JSON error responses: ``{"error": {"code", "message", "details"}}``."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from ..application.errors import ConfigurationError, LifecycleError, MonitorError, NotFoundError, StorageError

logger = logging.getLogger(__name__)

_STATUS_BY_ERROR: dict[type[MonitorError], tuple[int, str]] = {
    NotFoundError: (404, "not_found"),
    LifecycleError: (409, "invalid_state_transition"),
    StorageError: (503, "storage_unavailable"),
    ConfigurationError: (500, "configuration_error"),
}


def error_body(code: str, message: str, details: Any = None) -> dict[str, Any]:
    body: dict[str, Any] = {"code": code, "message": message}
    if details is not None:
        body["details"] = details
    return {"error": body}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(MonitorError)
    async def _monitor_error(_: Request, exc: MonitorError) -> JSONResponse:
        status, code = next(
            (value for error_type, value in _STATUS_BY_ERROR.items() if isinstance(exc, error_type)),
            (500, "internal_error"),
        )
        if status >= 500:
            logger.error("%s: %s", code, exc)
        return JSONResponse(status_code=status, content=error_body(code, str(exc)))

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        details = [
            {"loc": list(error.get("loc", ())), "msg": error.get("msg", ""), "type": error.get("type", "")}
            for error in exc.errors()
        ]
        return JSONResponse(status_code=422, content=error_body("validation_error", "request validation failed", details))

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {401: "unauthorized", 403: "forbidden", 404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "http_error")
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(code, str(exc.detail)),
            headers=getattr(exc, "headers", None),
        )
