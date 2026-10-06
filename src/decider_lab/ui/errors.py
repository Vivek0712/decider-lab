"""The error envelope of API.md section 2.2, and the handlers that produce it.

Raise `ApiError(status, code, message, hint=..., detail=...)` from any route; never return an
error dict by hand. Unexpected exceptions become `500 internal` with a generic message (the
traceback goes to the server log, redacted).
"""

from __future__ import annotations

import logging
import traceback
import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("decider_lab.ui")


class ApiError(Exception):
    """An API error with a stable `code` (API.md section 12)."""

    def __init__(self, status: int, code: str, message: str, *, hint: str | None = None,
                 detail: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.hint = hint
        self.detail = detail
        self.headers = headers


def request_id(request: Request) -> str:
    rid = getattr(request.state, "request_id", None)
    if not rid:
        rid = uuid.uuid4().hex
        request.state.request_id = rid
    return rid


def error_body(code: str, message: str, rid: str, *, hint: str | None = None,
               detail: dict[str, Any] | None = None) -> dict[str, Any]:
    err: dict[str, Any] = {"code": code, "message": message, "request_id": rid}
    if hint:
        err["hint"] = hint
    if detail is not None:
        err["detail"] = detail
    return {"error": err}


def error_response(request: Request, status: int, code: str, message: str, *, hint: str | None = None,
                   detail: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> JSONResponse:
    rid = request_id(request)
    resp = JSONResponse(error_body(code, message, rid, hint=hint, detail=detail), status_code=status, headers=headers)
    resp.headers["X-Request-Id"] = rid
    return resp


_STATUS_CODES = {400: "bad_request", 401: "unauthorized", 403: "forbidden_origin", 404: "not_found",
                 405: "method_not_allowed", 413: "too_large", 415: "unsupported_media", 421: "misdirected",
                 422: "bad_request", 428: "precondition_required"}


def install(app: FastAPI, redact: Any = None) -> None:
    """Register the handlers on `app`. `redact(str) -> str` cleans tracebacks before logging."""

    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError) -> JSONResponse:
        return error_response(request, exc.status, exc.code, exc.message, hint=exc.hint, detail=exc.detail,
                              headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        fields = []
        for e in exc.errors():
            loc = [str(x) for x in e.get("loc", ()) if x not in ("body", "query", "path", "header")]
            fields.append({"field": ".".join(loc) or "body", "message": str(e.get("msg", "invalid"))})
        return error_response(request, 422, "bad_request", "The request is not valid.", detail={"fields": fields})

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _STATUS_CODES.get(exc.status_code, "internal" if exc.status_code >= 500 else "bad_request")
        message = {404: "Not found.", 405: "This method is not allowed here."}.get(
            exc.status_code, str(exc.detail) if isinstance(exc.detail, str) else "Request failed.")
        detail = {"what": "route"} if exc.status_code == 404 else None
        return error_response(request, exc.status_code, code, message, detail=detail)

    @app.exception_handler(Exception)
    async def _internal(request: Request, exc: Exception) -> JSONResponse:
        text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        log.error("request %s failed:\n%s", request_id(request), redact(text) if redact else text)
        return error_response(request, 500, "internal", "Something went wrong in the Studio server.",
                              hint="The server log has the details.")
