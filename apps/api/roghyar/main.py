import json
import logging
from typing import Literal
from uuid import UUID, uuid4

from fastapi import FastAPI, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.pool import NullPool

from .database import database_url
from .settings import Settings


class Health(BaseModel):
    status: Literal["ok"]


class ReadinessError(BaseModel):
    code: Literal["DEPENDENCY_UNAVAILABLE"]
    message: str
    request_id: UUID
    details: None = None


class ErrorEnvelope(BaseModel):
    error: ReadinessError


logger = logging.getLogger("uvicorn.error")
settings = Settings()
engine = create_engine(database_url(settings), poolclass=NullPool, hide_parameters=True)
app = FastAPI(title="ROGHYAR AI", version="0.1.0")


@app.post("/api/v1/assessments", status_code=503, response_model=ErrorEnvelope,
          operation_id="assessmentIntakeUnavailable")
@app.get("/api/v1/assessments/{assessment_id}", status_code=503, response_model=ErrorEnvelope,
         operation_id="assessmentLookupUnavailable", openapi_extra={"parameters": [{
             "name": "assessment_id", "in": "path", "required": True,
             "schema": {"type": "string", "format": "uuid"},
         }]})
def assessment_unavailable(response: Response) -> ErrorEnvelope:
    # No body or credential dependency. The service is never wired into public HTTP.
    response.headers["Cache-Control"] = "no-store"
    return ErrorEnvelope(error=ReadinessError(
        code="DEPENDENCY_UNAVAILABLE", message="Assessment storage is unavailable.", request_id=uuid4(),
    ))


@app.get("/api/v1/health/live", response_model=Health, operation_id="getLiveness")
def liveness(response: Response) -> Health:
    response.headers["Cache-Control"] = "no-store"
    return Health(status="ok")


def database_ready() -> bool:
    with engine.connect() as connection:
        return bool(connection.execute(text("""
            SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'postgis')
                AND EXISTS (SELECT 1 FROM public.alembic_version WHERE version_num = '0006_formulary_attestation')
        """)).scalar_one())


@app.get(
    "/api/v1/health/ready",
    response_model=Health,
    responses={503: {"model": ErrorEnvelope, "description": "Database is unavailable or needs migration"}},
    operation_id="getReadiness",
)
def readiness(response: Response) -> Health | JSONResponse:
    response.headers["Cache-Control"] = "no-store"
    try:
        if database_ready():
            return Health(status="ok")
    except SQLAlchemyError:
        # Driver exceptions can contain credentials and connection details.
        pass

    request_id = uuid4()
    logger.warning(json.dumps({"event": "database_unready", "request_id": str(request_id)}))
    body = ErrorEnvelope(error=ReadinessError(
        code="DEPENDENCY_UNAVAILABLE",
        message="The database is not ready.",
        request_id=request_id,
    ))
    return JSONResponse(
        status_code=503, content=body.model_dump(mode="json"), headers={"Cache-Control": "no-store"}
    )
