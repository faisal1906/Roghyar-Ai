"""Callback route factory used only by an isolated private test application."""
from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

from .http_safety import APIError, ErrorCode, operational_event, read_bounded_json
from .notification_receipts import Receipts
from .notification_templates import NotificationDenied
from .red_flags import canonical


def private_receipt_router(receipts: Receipts):
    router = APIRouter()

    @router.post("/private/notification-receipts", include_in_schema=False)
    async def record(request: Request):
        if request.url.scheme != "https":
            raise APIError(ErrorCode.FORBIDDEN)
        data = await read_bounded_json(request, max_bytes=4096)
        try:
            await run_in_threadpool(receipts.record, canonical(data))
        except NotificationDenied as exc:
            if str(exc) == "receipt_unavailable":
                raise APIError(ErrorCode.DEPENDENCY_UNAVAILABLE) from None
            operational_event("notification_receipt_rejected",request.state.request_id)
            raise APIError(ErrorCode.FORBIDDEN) from None
        except (UnicodeError,ValueError):
            raise APIError(ErrorCode.VALIDATION_ERROR) from None
        return JSONResponse({"status":"recorded"},headers={"Cache-Control":"no-store"})

    return router
