import json
import logging
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

from roghyar.main import app
from roghyar.settings import Settings

API_DIR = Path(__file__).resolve().parents[1]
ROOT = API_DIR.parents[1]

# New routes, models and dependencies require a capability review before this inventory changes.
HEALTH_ROUTES = {
    "/api/v1/health/live": {"GET"},
    "/api/v1/health/ready": {"GET"},
}
DOCUMENTATION_ROUTES = {
    "/openapi.json": {"GET", "HEAD"},
    "/docs": {"GET", "HEAD"},
    "/docs/oauth2-redirect": {"GET", "HEAD"},
    "/redoc": {"GET", "HEAD"},
}


def test_registered_routes_and_openapi_are_health_only():
    expected_routes = HEALTH_ROUTES | DOCUMENTATION_ROUTES
    assert len(app.routes) == len(expected_routes)
    assert {route.path: route.methods for route in app.routes} == expected_routes
    schema = json.loads((ROOT / "contracts/openapi.json").read_text(encoding="utf-8"))
    assert schema == app.openapi()
    assert set(schema["paths"]) == HEALTH_ROUTES.keys()
    for operations in schema["paths"].values():
        assert set(operations) == {"get"}
        assert "requestBody" not in operations["get"]
    assert {
        name: set(model["properties"])
        for name, model in schema["components"]["schemas"].items()
    } == {
        "Health": {"status"},
        "ErrorEnvelope": {"error"},
        "ReadinessError": {"code", "message", "request_id", "details"},
    }


def test_assessment_boundary_rejects_upload_without_echo_or_log(caplog):
    marker = "SYNTHETIC-DEVELOPMENT-ONLY-ASSESSMENT"
    caplog.set_level(logging.DEBUG)
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/assessments",
            data={"symptoms": marker},
            files={"photo": ("probe.png", marker.encode(), "image/png")},
        )
    assert response.status_code == 404
    assert marker not in response.text
    assert marker not in caplog.text
    settings = Settings(_env_file=None)
    assert settings.clinical_guidance_enabled is False
    assert settings.appointment_booking_enabled is False


def test_backend_dependencies_and_migration_history_require_review():
    requirements = (API_DIR / "requirements.txt").read_text(encoding="utf-8").splitlines()
    dependencies = {
        canonicalize_name(Requirement(line).name)
        for line in requirements if line.strip() and not line.lstrip().startswith("#")
    }
    assert dependencies == {
        "fastapi", "uvicorn", "pydantic-settings", "psycopg", "alembic", "sqlalchemy",
        "cryptography", "pyjwt",
    }
    config = Config(str(API_DIR / "alembic.ini"))
    scripts = ScriptDirectory.from_config(config)
    assert [revision.revision for revision in scripts.walk_revisions()] == [
        "0003_rls", "0002_core_schema", "0001_postgis",
    ]
