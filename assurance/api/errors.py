"""RFC 9457 problem details for every error response."""
from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger(__name__)
PROBLEM = "application/problem+json"


def problem(status: int, title: str, detail=None, **extra) -> JSONResponse:
    body = {"type": "about:blank", "title": title, "status": status}
    if detail is not None:
        body["detail"] = detail
    body.update(extra)
    return JSONResponse(body, status_code=status, media_type=PROBLEM)


def install(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def http_exc(request: Request, exc: StarletteHTTPException):
        resp = problem(exc.status_code, _title(exc.status_code), exc.detail)
        if exc.headers:
            resp.headers.update(exc.headers)
        return resp

    @app.exception_handler(RequestValidationError)
    async def validation_exc(request: Request, exc: RequestValidationError):
        errors = [{"loc": list(e.get("loc", [])), "msg": e.get("msg"), "type": e.get("type")} for e in exc.errors()]
        return problem(422, "Validation failed", "The request is not valid", errors=errors)

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):  # pragma: no cover
        log.exception("unhandled error on %s %s", request.method, request.url.path)
        return problem(500, "Internal Server Error", "An unexpected error occurred")


def _title(status: int) -> str:
    from http import HTTPStatus

    try:
        return HTTPStatus(status).phrase
    except ValueError:
        return "Error"
