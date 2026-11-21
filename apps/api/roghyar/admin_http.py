"""Private administration routes. The normal FastAPI application does not mount them."""
from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Request
from fastapi.encoders import jsonable_encoder
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

from .admin_identity import AdminDenied, Page, Proposal, StaffLogin, StaffService
from .http_safety import APIError, ErrorCode, read_bounded_json
from .red_flags import canonical


def private_admin_router(login: StaffLogin, service: StaffService, *, origin: str):
    address=urlsplit(origin)
    if address.scheme!="https" or not address.hostname or address.username or origin!=f"https://{address.netloc}":
        raise ValueError("Explicit HTTPS origin required")
    router=APIRouter(prefix="/admin")

    def check_origin(request):
        if request.url.scheme!="https" or request.headers.getlist("origin")!=[origin]:
            raise APIError(ErrorCode.FORBIDDEN)

    def token(request):
        check_origin(request)
        values=request.headers.getlist("authorization")
        if len(values)!=1 or not values[0].startswith("Bearer ") or len(values[0])>64:
            raise APIError(ErrorCode.UNAUTHENTICATED)
        return values[0][7:]

    async def call(operation,*args):
        try:
            return await run_in_threadpool(operation,*args)
        except ValidationError:
            raise APIError(ErrorCode.VALIDATION_ERROR) from None
        except AdminDenied as exc:
            codes={"unauthenticated":ErrorCode.UNAUTHENTICATED,"forbidden":ErrorCode.FORBIDDEN,
                   "not_found":ErrorCode.NOT_FOUND,"stale_revision":ErrorCode.CONFLICT,
                   "idempotency_conflict":ErrorCode.CONFLICT}
            raise APIError(codes.get(str(exc),ErrorCode.DEPENDENCY_UNAVAILABLE)) from None

    def reply(value,status=200):
        return JSONResponse(jsonable_encoder(value),status_code=status,headers={"Cache-Control":"no-store"})

    @router.post("/session",include_in_schema=False)
    async def session(request:Request):
        check_origin(request)
        data=await read_bounded_json(request,max_bytes=4096)
        if type(data) is not dict or set(data)!={"proof"} or type(data["proof"]) is not str:
            raise APIError(ErrorCode.VALIDATION_ERROR)
        return reply({"token":await call(login.login,data["proof"]),"token_type":"Bearer"})

    @router.delete("/session",include_in_schema=False)
    async def logout(request:Request):
        await call(service.logout,token(request))
        return reply({"status":"revoked"})

    @router.get("/{section}",include_in_schema=False)
    async def get(section:Literal["doctors","schedules","appointments","audit"],request:Request):
        proof=token(request)
        if set(request.query_params)-{"limit"} or len(request.query_params.multi_items())>1:
            raise APIError(ErrorCode.VALIDATION_ERROR)
        raw=request.query_params.get("limit","20")
        if not raw.isascii() or not raw.isdecimal() or len(raw)>2:
            raise APIError(ErrorCode.VALIDATION_ERROR)
        try:
            query=Page(section=section,limit=int(raw))
        except ValidationError:
            raise APIError(ErrorCode.VALIDATION_ERROR) from None
        return reply(await call(service.page,proof,query))

    @router.post("/{section}/proposals",include_in_schema=False)
    async def proposal(section:Literal["doctors","schedules"],request:Request):
        proof=token(request)
        keys=request.headers.getlist("idempotency-key")
        if len(keys)!=1:
            raise APIError(ErrorCode.VALIDATION_ERROR)
        payload=await read_bounded_json(request,max_bytes=8192)
        try:
            query=Proposal.model_validate_json(canonical(payload))
        except (ValueError, UnicodeError):
            raise APIError(ErrorCode.VALIDATION_ERROR) from None
        if not query.action.startswith("doctor_" if section=="doctors" else "schedule_"):
            raise APIError(ErrorCode.VALIDATION_ERROR)
        request_id=await call(service.propose,proof,query,keys[0])
        return reply({"id":request_id,"status":"submitted_for_review"},202)

    return router
