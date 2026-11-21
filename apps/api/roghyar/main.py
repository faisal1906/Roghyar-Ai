from typing import Literal

from fastapi import APIRouter, FastAPI, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.pool import NullPool

from .database import database_url
from .http_safety import ErrorCode, ErrorEnvelope, error_response, install_http_safety, operational_event
from .settings import Settings


class Health(BaseModel):
    status: Literal["ok"]


settings = Settings()
engine = create_engine(database_url(settings), poolclass=NullPool, hide_parameters=True)
routes = APIRouter()


def create_app(*, production: bool) -> FastAPI:
    api = FastAPI(
        title="ROGHYAR AI", version="0.1.0", redirect_slashes=False,
        openapi_url=None if production else "/openapi.json",
    )
    install_http_safety(api, production=production)
    api.include_router(routes)
    return api


@routes.post("/api/v1/assessments", status_code=503, response_model=ErrorEnvelope,
          operation_id="assessmentIntakeUnavailable")
@routes.get("/api/v1/assessments/{assessment_id}", status_code=503, response_model=ErrorEnvelope,
         operation_id="assessmentLookupUnavailable", openapi_extra={"parameters": [{
             "name": "assessment_id", "in": "path", "required": True,
             "schema": {"type": "string", "format": "uuid"},
         }]})
def assessment_unavailable(request: Request) -> JSONResponse:
    # No body or credential dependency. The service is never wired into public HTTP.
    return error_response(request.state.request_id, ErrorCode.DEPENDENCY_UNAVAILABLE)


@routes.get("/api/v1/health/live", response_model=Health, operation_id="getLiveness")
def liveness(response: Response) -> Health:
    response.headers["Cache-Control"] = "no-store"
    return Health(status="ok")


def database_ready() -> bool:
    with engine.connect() as connection:
        return bool(connection.execute(text("""
            SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'postgis')
                AND EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm')
                AND EXISTS (SELECT 1 FROM public.alembic_version WHERE version_num = '0015_notification_outbox')
        """)).scalar_one())


@routes.get(
    "/api/v1/health/ready",
    response_model=Health,
    responses={503: {"model": ErrorEnvelope, "description": "Database is unavailable or needs migration"}},
    operation_id="getReadiness",
)
def readiness(request: Request, response: Response) -> Health | JSONResponse:
    response.headers["Cache-Control"] = "no-store"
    try:
        if database_ready():
            return Health(status="ok")
    except SQLAlchemyError:
        # Driver exceptions can contain credentials and connection details.
        pass

    operational_event("database_unready", request.state.request_id)
    return error_response(request.state.request_id, ErrorCode.DEPENDENCY_UNAVAILABLE)


app = create_app(production=settings.environment == "production")
