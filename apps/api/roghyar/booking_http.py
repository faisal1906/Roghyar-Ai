"""Private, unmounted booking HTTP contract."""
from urllib.parse import urlsplit

from fastapi import APIRouter, Request
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

from .booking_reservations import AppointmentBooking, BookingDenied, BookingRequest
from .http_safety import APIError, ErrorCode, operational_event, read_bounded_json
from .red_flags import canonical

ERRORS = {
    "invalid_request": ErrorCode.VALIDATION_ERROR,
    "unauthenticated": ErrorCode.UNAUTHENTICATED,
    "phone_mismatch": ErrorCode.FORBIDDEN,
    "minor_policy_unavailable": ErrorCode.FORBIDDEN,
    "assessment_link_unavailable": ErrorCode.FORBIDDEN,
    "identity_conflict": ErrorCode.CONFLICT,
}

PRIVATE_ERRORS = {
    "slot_unavailable": (409, "SLOT_UNAVAILABLE", "The requested slot is unavailable."),
    "idempotency_conflict": (409, "IDEMPOTENCY_CONFLICT", "This key is bound to another booking request."),
    "booking_outcome_unknown": (503, "BOOKING_OUTCOME_UNKNOWN", "Retry the same booking request and key."),
}


def private_booking_router(service: AppointmentBooking, *, origin: str) -> APIRouter:
    parsed = urlsplit(origin)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or origin != f"https://{parsed.netloc}":
        raise ValueError("Explicit HTTPS origin required")
    router = APIRouter()

    @router.post("/appointments", include_in_schema=False)
    async def reserve(request: Request):
        if request.url.scheme != "https" or request.headers.getlist("origin") != [origin]:
            raise APIError(ErrorCode.FORBIDDEN)
        auth = request.headers.getlist("authorization")
        if len(auth) != 1 or not auth[0].startswith("Bearer ") or not 8 <= len(auth[0]) <= 2055:
            raise APIError(ErrorCode.UNAUTHENTICATED)
        keys = request.headers.getlist("idempotency-key")
        if len(keys) != 1:
            raise APIError(ErrorCode.VALIDATION_ERROR)
        data = await read_bounded_json(request, max_bytes=4096)
        try:
            body = BookingRequest.model_validate_json(canonical(data))
        except (ValidationError, ValueError, UnicodeError):
            raise APIError(ErrorCode.VALIDATION_ERROR) from None
        try:
            result = await run_in_threadpool(service.reserve, auth[0][7:], body, keys[0])
        except BookingDenied as exc:
            if str(exc) in PRIVATE_ERRORS:
                status, code, message = PRIVATE_ERRORS[str(exc)]
                if status == 503:
                    operational_event("booking_outcome_unknown", request.state.request_id)
                return JSONResponse({"error": {"code": code, "message": message,
                                               "request_id": str(request.state.request_id), "details": None}},
                                    status_code=status, headers={"Cache-Control": "no-store"})
            raise APIError(ERRORS.get(str(exc), ErrorCode.DEPENDENCY_UNAVAILABLE)) from None
        return JSONResponse(result.model_dump(mode="json"), status_code=201, headers={"Cache-Control": "no-store"})

    return router
