from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from roghyar import main
from roghyar.main import app


def test_liveness():
    response = TestClient(app).get("/api/v1/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["cache-control"] == "no-store"


def test_openapi_exposes_liveness():
    schema = TestClient(app).get("/openapi.json").json()
    endpoint = schema["paths"]["/api/v1/health/live"]["get"]
    assert endpoint["operationId"] == "getLiveness"
    assert endpoint["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/Health"
    }


def test_readiness_hides_database_details(monkeypatch, caplog):
    def unavailable():
        raise OperationalError(None, None, ValueError("sensitive-db-details"))

    monkeypatch.setattr(main, "database_ready", unavailable)
    response = TestClient(app).get("/api/v1/health/ready")
    assert response.status_code == 503
    assert response.headers["cache-control"] == "no-store"
    error = response.json()["error"]
    assert error["code"] == "DEPENDENCY_UNAVAILABLE"
    assert UUID(error["request_id"])
    assert error["request_id"] in caplog.text
    assert "sensitive-db-details" not in response.text + caplog.text
