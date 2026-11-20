"""Opt-in private OTP HTTP contract. The shipped application never installs it."""
from urllib.parse import urlsplit

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from .http_safety import APIError, ErrorCode, read_bounded_json
from .otp_service import OTPDenied, OTPRequest, OTPService, OTPVerify


def private_otp_router(service: OTPService, *, origin: str) -> APIRouter:
    target = urlsplit(origin)
    if target.scheme != "https" or not target.hostname or target.username or target.password or origin != (
            f"https://{target.netloc}"):
        raise ValueError("Explicit HTTPS origin required")
    router = APIRouter()

    async def parse(request: Request, contract):
        if request.url.scheme != "https" or request.headers.getlist("origin") != [origin]:
            raise APIError(ErrorCode.FORBIDDEN)
        data = await read_bounded_json(request, max_bytes=512)
        try:
            return contract.model_validate(data)
        except ValidationError:
            raise APIError(ErrorCode.VALIDATION_ERROR) from None

    def failure(exc):
        return APIError(ErrorCode.UNAUTHENTICATED if str(exc) in ("invalid_verification", "invalid_session")
                        else ErrorCode.DEPENDENCY_UNAVAILABLE)

    @router.post("/otp/request", include_in_schema=False)
    async def request_otp(request: Request):
        query = await parse(request, OTPRequest)
        try:
            await run_in_threadpool(service.request, query, request)
        except OTPDenied as exc:
            raise failure(exc) from None
        return JSONResponse({"status": "accepted"}, status_code=202, headers={"Cache-Control": "no-store"})

    @router.post("/otp/verify", include_in_schema=False)
    async def verify_otp(request: Request):
        query = await parse(request, OTPVerify)
        try:
            token = await run_in_threadpool(service.verify, query, request)
        except OTPDenied as exc:
            raise failure(exc) from None
        return JSONResponse({"token": token, "token_type": "Bearer"}, headers={"Cache-Control": "no-store"})

    return router
