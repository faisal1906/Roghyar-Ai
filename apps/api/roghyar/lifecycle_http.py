"""Private and unmounted appointment lifecycle HTTP contracts."""
from datetime import datetime
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import APIRouter, Request
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

from .appointment_lifecycle import AppointmentLifecycle, HistoryQuery, LifecycleDenied, Mutation
from .http_safety import APIError, ErrorCode, operational_event, read_bounded_json
from .red_flags import canonical

PRIVATE_ERRORS = {
    "invalid_transition": (409, "INVALID_TRANSITION", "The appointment can no longer be changed."),
    "cutoff_passed": (409, "CHANGE_CUTOFF_PASSED", "The appointment change deadline has passed."),
    "slot_unavailable": (409, "SLOT_UNAVAILABLE", "The requested slot is unavailable."),
    "idempotency_conflict": (409, "IDEMPOTENCY_CONFLICT", "This key is bound to another appointment request."),
    "lifecycle_outcome_unknown": (503, "LIFECYCLE_OUTCOME_UNKNOWN", "Retry the same request and key."),
}


def private_lifecycle_router(service: AppointmentLifecycle, *, origin: str):
    parsed = urlsplit(origin)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or origin != f"https://{parsed.netloc}":
        raise ValueError("Explicit HTTPS origin required")
    router = APIRouter()

    def bearer(request):
        if request.url.scheme != "https" or request.headers.getlist("origin") != [origin]:
            raise APIError(ErrorCode.FORBIDDEN)
        auth = request.headers.getlist("authorization")
        if len(auth) != 1 or not auth[0].startswith("Bearer ") or not 8 <= len(auth[0]) <= 2055:
            raise APIError(ErrorCode.UNAUTHENTICATED)
        return auth[0][7:]

    async def respond(request, operation, *args):
        try:
            result = await run_in_threadpool(operation, *args)
        except LifecycleDenied as exc:
            if str(exc) in PRIVATE_ERRORS:
                status, code, message = PRIVATE_ERRORS[str(exc)]
                if status == 503:
                    operational_event("lifecycle_outcome_unknown", request.state.request_id)
                return JSONResponse({"error": {"code": code, "message": message,
                                               "request_id": str(request.state.request_id), "details": None}},
                                    status_code=status, headers={"Cache-Control": "no-store"})
            error = {"unauthenticated": ErrorCode.UNAUTHENTICATED, "not_found": ErrorCode.NOT_FOUND,
                     "invalid_request": ErrorCode.VALIDATION_ERROR}
            raise APIError(error.get(str(exc), ErrorCode.DEPENDENCY_UNAVAILABLE)) from None
        return JSONResponse(result.model_dump(mode="json"), headers={"Cache-Control": "no-store"})

    @router.get("/appointments/mine", include_in_schema=False)
    async def mine(request: Request):
        token = bearer(request)
        if len(request.scope.get("query_string", b"")) > 256:
            raise APIError(ErrorCode.VALIDATION_ERROR)
        query = request.query_params
        try:
            if len(dict(query)) != len(query.multi_items()) or set(query) - {"limit", "before_start", "before_id"}:
                raise ValueError()
            options = HistoryQuery(limit=int(query.get("limit", "20")),
                before_start=datetime.fromisoformat(query["before_start"]) if "before_start" in query else None,
                before_id=UUID(query["before_id"]) if "before_id" in query else None)
        except (ValidationError, ValueError):
            raise APIError(ErrorCode.VALIDATION_ERROR) from None
        return await respond(request, service.mine, token, options)

    @router.patch("/appointments/{appointment_id}", include_in_schema=False)
    async def mutate(appointment_id: UUID, request: Request):
        token = bearer(request)
        keys = request.headers.getlist("idempotency-key")
        if len(keys) != 1 or request.query_params:
            raise APIError(ErrorCode.VALIDATION_ERROR)
        data = await read_bounded_json(request, max_bytes=512)
        try:
            body = Mutation.model_validate_json(canonical(data))
        except (ValidationError, ValueError, UnicodeError):
            raise APIError(ErrorCode.VALIDATION_ERROR) from None
        return await respond(request, service.mutate, token, appointment_id, body, keys[0])

    return router
