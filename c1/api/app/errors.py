"""The one error envelope of the platform (Part I, Table 8).

Every refusal is raised as an ApiError and rendered as
{"error": {"code", "message", "details"}}. FastAPI's own validation errors
(HTTP 422 by default) are converted to VALIDATION / 400.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("scicp.errors")


class ApiError(Exception):
    status: int = 500
    code: str = "INTERNAL"

    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None,
                 status: Optional[int] = None, code: Optional[str] = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}
        if status is not None:
            self.status = status
        if code is not None:
            self.code = code

    def to_body(self) -> Dict[str, Any]:
        body: Dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details:
            body["details"] = self.details
        return {"error": body}


class Validation(ApiError):
    status, code = 400, "VALIDATION"


class Unauthorized(ApiError):
    status, code = 401, "UNAUTHORIZED"

    def __init__(self, message: str = "Invalid credentials", details=None):
        super().__init__(message, details)


class Forbidden(ApiError):
    status, code = 403, "FORBIDDEN"

    def __init__(self, message: str = "Not allowed for this role", details=None):
        super().__init__(message, details)


class NotFound(ApiError):
    status, code = 404, "NOT_FOUND"

    def __init__(self, message: str = "Not found", details=None):
        super().__init__(message, details)


class Conflict(ApiError):
    status, code = 409, "CONFLICT"


class Locked(ApiError):
    status, code = 423, "LOCKED"


class BusinessRule(ApiError):
    """422 BUSINESS_RULE; details.rule names the rule (BR-xx)."""
    status, code = 422, "BUSINESS_RULE"

    def __init__(self, rule: str, message: str, **details: Any):
        super().__init__(message, {"rule": rule, **{k: v for k, v in details.items() if v is not None}})


class RateLimit(ApiError):
    status, code = 429, "RATE_LIMIT"


class BadGateway(ApiError):
    status, code = 502, "BAD_GATEWAY"


_HTTP_CODES = {400: "VALIDATION", 401: "UNAUTHORIZED", 403: "FORBIDDEN", 404: "NOT_FOUND",
               405: "NOT_FOUND", 409: "CONFLICT", 422: "VALIDATION", 423: "LOCKED", 429: "RATE_LIMIT",
               502: "BAD_GATEWAY"}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError):
        return JSONResponse(status_code=exc.status, content=exc.to_body())

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError):
        errors = []
        for e in exc.errors():
            loc = [str(p) for p in e.get("loc", []) if p not in ("body", "query", "path")]
            errors.append({"field": ".".join(loc), "message": e.get("msg", "invalid")})
        return JSONResponse(status_code=400, content={"error": {
            "code": "VALIDATION", "message": "Request does not match the schema",
            "details": {"errors": errors}}})

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException):
        code = _HTTP_CODES.get(exc.status_code, "INTERNAL" if exc.status_code >= 500 else "VALIDATION")
        detail = exc.detail if isinstance(exc.detail, str) else "Request failed"
        return JSONResponse(status_code=exc.status_code, content={"error": {"code": code, "message": detail}},
                            headers=getattr(exc, "headers", None))

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception):
        log.exception("Unhandled error: %s", exc)
        return JSONResponse(status_code=500, content={"error": {"code": "INTERNAL", "message": "Internal error"}})
