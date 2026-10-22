"""Common error format (API spec v0.3 §9): every failure returns
{"error": {"code", "message", "retryable", "details"}, "request_id"}."""

from enum import StrEnum
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class ErrorCode(StrEnum):
    BAD_REQUEST = "BAD_REQUEST"
    INVALID_INPUT = "INVALID_INPUT"
    IDEMPOTENCY_KEY_REUSED = "IDEMPOTENCY_KEY_REUSED"
    SESSION_UNAUTHORIZED = "SESSION_UNAUTHORIZED"
    SESSION_EXPIRED = "SESSION_EXPIRED"
    SESSION_FORBIDDEN = "SESSION_FORBIDDEN"
    NOT_FOUND = "NOT_FOUND"
    MAP_NOT_FOUND = "MAP_NOT_FOUND"
    POI_NOT_FOUND = "POI_NOT_FOUND"
    LOCALIZATION_NOT_FOUND = "LOCALIZATION_NOT_FOUND"
    ROUTE_NOT_FOUND = "ROUTE_NOT_FOUND"
    LOCALIZATION_EXPIRED = "LOCALIZATION_EXPIRED"
    MAP_VERSION_MISMATCH = "MAP_VERSION_MISMATCH"
    REVISION_CONFLICT = "REVISION_CONFLICT"
    INVALID_STATE = "INVALID_STATE"
    LOCATION_NOT_CONFIRMED = "LOCATION_NOT_CONFIRMED"
    IMAGE_TOO_LARGE = "IMAGE_TOO_LARGE"
    UNSUPPORTED_IMAGE = "UNSUPPORTED_IMAGE"
    AI_SERVICE_UNAVAILABLE = "AI_SERVICE_UNAVAILABLE"
    INTERNAL = "INTERNAL"


_STATUS: dict[ErrorCode, int] = {
    ErrorCode.BAD_REQUEST: 400,
    ErrorCode.INVALID_INPUT: 422,
    ErrorCode.IDEMPOTENCY_KEY_REUSED: 422,
    ErrorCode.SESSION_UNAUTHORIZED: 401,
    ErrorCode.SESSION_EXPIRED: 401,
    ErrorCode.SESSION_FORBIDDEN: 403,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.MAP_NOT_FOUND: 404,
    ErrorCode.POI_NOT_FOUND: 404,
    ErrorCode.LOCALIZATION_NOT_FOUND: 404,
    ErrorCode.ROUTE_NOT_FOUND: 422,
    ErrorCode.LOCALIZATION_EXPIRED: 410,
    ErrorCode.MAP_VERSION_MISMATCH: 409,
    ErrorCode.REVISION_CONFLICT: 409,
    ErrorCode.INVALID_STATE: 409,
    ErrorCode.LOCATION_NOT_CONFIRMED: 409,
    ErrorCode.IMAGE_TOO_LARGE: 413,
    ErrorCode.UNSUPPORTED_IMAGE: 415,
    ErrorCode.AI_SERVICE_UNAVAILABLE: 503,
    ErrorCode.INTERNAL: 500,
}

_RETRYABLE = {ErrorCode.AI_SERVICE_UNAVAILABLE, ErrorCode.REVISION_CONFLICT}


class ApiError(Exception):
    def __init__(self, code: ErrorCode, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}

    @property
    def status_code(self) -> int:
        return _STATUS[self.code]


def error_response(
    request: Request, code: ErrorCode, message: str, details: dict[str, Any] | None = None
) -> JSONResponse:
    body = {
        "error": {"code": code, "message": message, "retryable": code in _RETRYABLE, "details": details or {}},
        "request_id": getattr(request.state, "request_id", None),
    }
    return JSONResponse(body, status_code=_STATUS[code])


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError) -> JSONResponse:
        return error_response(request, exc.code, exc.message, exc.details)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Field-level problems (wrong enum, missing field) are "fix your input", not a protocol error.
        errors = [{"loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors()]
        return error_response(request, ErrorCode.INVALID_INPUT, "Request body is invalid.", {"errors": errors})

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = ErrorCode.NOT_FOUND if exc.status_code == 404 else ErrorCode.BAD_REQUEST
        return error_response(request, code, str(exc.detail))
