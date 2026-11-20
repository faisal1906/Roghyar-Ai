"""Privacy-safe HTTP errors, correlation, and opt-in request bounds."""

import json
import logging
import math
from enum import StrEnum
from uuid import UUID, uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict
from starlette.datastructures import MutableHeaders
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse

logger = logging.getLogger("roghyar.http")


class ErrorCode(StrEnum):
    VALIDATION_ERROR = "VALIDATION_ERROR"
    UNAUTHENTICATED = "UNAUTHENTICATED"
    FORBIDDEN = "FORBIDDEN"
    NOT_FOUND = "NOT_FOUND"
    METHOD_NOT_ALLOWED = "METHOD_NOT_ALLOWED"
    CONFLICT = "CONFLICT"
    PAYLOAD_TOO_LARGE = "PAYLOAD_TOO_LARGE"
    UNSUPPORTED_MEDIA_TYPE = "UNSUPPORTED_MEDIA_TYPE"
    RATE_LIMITED = "RATE_LIMITED"
    DEPENDENCY_UNAVAILABLE = "DEPENDENCY_UNAVAILABLE"
    CLINICAL_CONTENT_UNAVAILABLE = "CLINICAL_CONTENT_UNAVAILABLE"
    INTERNAL_ERROR = "INTERNAL_ERROR"


ERRORS = {
    ErrorCode.VALIDATION_ERROR: (422, "The request is invalid."),
    ErrorCode.UNAUTHENTICATED: (401, "Authentication is required."),
    ErrorCode.FORBIDDEN: (403, "Access is denied."),
    ErrorCode.NOT_FOUND: (404, "The requested resource was not found."),
    ErrorCode.METHOD_NOT_ALLOWED: (405, "The request method is not supported."),
    ErrorCode.CONFLICT: (409, "The request conflicts with the current state."),
    ErrorCode.PAYLOAD_TOO_LARGE: (413, "The request body is too large."),
    ErrorCode.UNSUPPORTED_MEDIA_TYPE: (415, "The request content type is not supported."),
    ErrorCode.RATE_LIMITED: (429, "Too many requests. Try again later."),
    ErrorCode.DEPENDENCY_UNAVAILABLE: (503, "The service is temporarily unavailable."),
    ErrorCode.CLINICAL_CONTENT_UNAVAILABLE: (503, "Reviewed clinical content is unavailable."),
    ErrorCode.INTERNAL_ERROR: (500, "An unexpected server error occurred."),
}
STATUS_CODES = {status: code for code, (status, _) in ERRORS.items()
                if code != ErrorCode.CLINICAL_CONTENT_UNAVAILABLE}
STATUS_CODES[400] = ErrorCode.VALIDATION_ERROR


class ErrorBody(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    code: ErrorCode
    message: str
    request_id: UUID
    details: None = None


class ErrorEnvelope(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    error: ErrorBody


class APIError(Exception):
    def __init__(self, code: ErrorCode, *, retry_after: int | None = None):
        super().__init__(code.value)
        self.code = code
        self.retry_after = retry_after


def error_response(request_id: UUID, code: ErrorCode, *, retry_after: int | None = None,
                   allow: str | None = None, status_override: int | None = None) -> JSONResponse:
    status, message = ERRORS[code]
    headers = {"Cache-Control": "no-store"}
    if code == ErrorCode.UNAUTHENTICATED:
        headers["WWW-Authenticate"] = "Bearer"
    if code == ErrorCode.RATE_LIMITED and type(retry_after) is int and retry_after > 0:
        headers["Retry-After"] = str(retry_after)
    if allow is not None:
        headers["Allow"] = allow
    body = ErrorEnvelope(error=ErrorBody(code=code, message=message, request_id=request_id))
    return JSONResponse(status_code=status_override or status, content=body.model_dump(mode="json"), headers=headers)


def operational_event(event: str, request_id: UUID) -> None:
    logger.warning(json.dumps({"event": event, "request_id": str(request_id)}))


def install_http_safety(app: FastAPI, *, production: bool) -> None:
    app.add_middleware(RequestSafety, production=production)

    @app.exception_handler(APIError)
    async def application_error(request: Request, exc: APIError):
        if exc.code in (ErrorCode.DEPENDENCY_UNAVAILABLE, ErrorCode.RATE_LIMITED):
            operational_event(exc.code.value.lower(), request.state.request_id)
        return error_response(request.state.request_id, exc.code, retry_after=exc.retry_after)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        return error_response(request.state.request_id, ErrorCode.VALIDATION_ERROR)

    @app.exception_handler(HTTPException)
    async def framework_error(request: Request, exc: HTTPException):
        code = STATUS_CODES.get(exc.status_code, ErrorCode.INTERNAL_ERROR)
        allow = None
        if exc.status_code == 405:
            methods = (exc.headers or {}).get("Allow", "").split(",")
            if all(method.strip() in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}
                   for method in methods):
                allow = ", ".join(method.strip() for method in methods)
        return error_response(request.state.request_id, code, allow=allow,
                              status_override=exc.status_code if exc.status_code == 400 else None)


class RequestSafety:
    def __init__(self, app, *, production: bool):
        self.app = app
        self.production = production

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = uuid4()
        scope.setdefault("state", {})["request_id"] = request_id
        started = False
        finished = False

        async def send_with_headers(message):
            nonlocal started, finished
            if message["type"] == "http.response.start":
                started = True
                headers = MutableHeaders(scope=message)
                headers["X-Request-ID"] = str(request_id)
                headers["X-Content-Type-Options"] = "nosniff"
                headers["Referrer-Policy"] = "no-referrer"
                headers["X-Frame-Options"] = "DENY"
                headers["Cache-Control"] = "no-store"
                if self.production and scope.get("scheme") == "https":
                    headers["Strict-Transport-Security"] = "max-age=31536000"
            elif message["type"] == "http.response.body" and not message.get("more_body", False):
                finished = True
            await send(message)

        path, method = scope["path"], scope["method"]
        suffix = path.removeprefix("/api/v1/assessments/")
        closed = (method == "POST" and path == "/api/v1/assessments") or (
            method == "GET" and suffix != path and bool(suffix) and "/" not in suffix
        )
        query = scope.get("query_string", b"")
        try:
            if not closed and (len(path) > 2048 or len(query) > 2048 or query.count(b"&") >= 32):
                response = error_response(request_id, ErrorCode.VALIDATION_ERROR)
                await response(scope, receive, send_with_headers)
                return
            await self.app(scope, receive, send_with_headers)
        except Exception:
            operational_event("request_failed", request_id)
            if not started:
                response = error_response(request_id, ErrorCode.INTERNAL_ERROR)
                await response(scope, receive, send_with_headers)
            elif not finished:
                # Close the stream without forwarding exception details to server logs.
                await send_with_headers({"type": "http.response.body", "body": b"", "more_body": False})


async def read_bounded_json(request: Request, *, max_bytes: int = 16384) -> dict:
    """Opt-in for authorized JSON routes. The closed routes never call this."""
    if not 1 <= max_bytes <= 65536:
        raise ValueError("Invalid request limit")
    if (request.headers.get("content-type", "").partition(";")[0].strip().lower() != "application/json"
        or request.headers.get("content-encoding", "identity").lower() != "identity"):
        raise APIError(ErrorCode.UNSUPPORTED_MEDIA_TYPE)
    declared = request.headers.get("content-length")
    if declared is not None:
        if len(declared) > 10 or not declared.isascii() or not declared.isdecimal():
            raise APIError(ErrorCode.VALIDATION_ERROR)
        if int(declared) > max_bytes:
            raise APIError(ErrorCode.PAYLOAD_TOO_LARGE)
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > max_bytes:
            raise APIError(ErrorCode.PAYLOAD_TOO_LARGE)
        data.extend(chunk)

    def unique_object(pairs):
        result = dict(pairs)
        if len(result) != len(pairs):
            raise ValueError("Duplicate object key")
        return result

    def invalid_constant(_):
        raise ValueError("Unsupported JSON constant")

    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=unique_object, parse_constant=invalid_constant)
        if not isinstance(value, dict):
            raise ValueError("JSON object required")
        pending = [(value, 1)]
        while pending:
            item, depth = pending.pop()
            if depth > 12:
                raise ValueError("JSON nesting limit exceeded")
            if isinstance(item, dict):
                pending.extend((part, depth + 1) for part in item.values())
                pending.extend((key, depth + 1) for key in item)
            elif isinstance(item, list):
                pending.extend((part, depth + 1) for part in item)
            elif isinstance(item, str) and any(
                (ord(char) < 32 and char not in "\n\r\t") or 0xD800 <= ord(char) <= 0xDFFF
                for char in item
            ):
                raise ValueError("Invalid text encoding")
            elif isinstance(item, float) and not math.isfinite(item):
                raise ValueError("Unsupported numeric value")
        return value
    except (ValueError, RecursionError):
        raise APIError(ErrorCode.VALIDATION_ERROR) from None
