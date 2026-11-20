"""Synthetic HTTP boundary tests. Test routes exist only in this module."""

import json
from statistics import median, quantiles
from time import perf_counter
from uuid import UUID

import pytest
from fastapi import Request
from fastapi.testclient import TestClient
from sqlalchemy import text

from roghyar import main
from roghyar.assessment import validate_assessment
from roghyar.http_safety import APIError, ERRORS, ErrorCode, read_bounded_json
from test_assessment import data
from test_database_security import database

MARKER = "SYNTHETIC-PRIVATE-VALUE"


def benchmark(label, operation, samples=40):
    durations = []
    for _ in range(samples):
        start = perf_counter()
        operation()
        durations.append((perf_counter() - start) * 1000)
    p95 = quantiles(durations, n=20, method="inclusive")[18]
    print(f"BE-06 benchmark {label} n={samples} median={median(durations):.3f}ms p95={p95:.3f}ms")


@pytest.fixture
def probe_app():
    app = main.create_app(production=False)

    @app.get("/probe/error/{code}")
    async def error(code: ErrorCode):
        raise APIError(code, retry_after=2)

    @app.get("/probe/crash")
    async def crash():
        raise RuntimeError(MARKER)

    @app.get("/probe/id/{record}")
    async def record_id(record: UUID):
        return {"id": record}

    @app.post("/probe/json")
    async def bounded(request: Request):
        return await read_bounded_json(request, max_bytes=256)

    return app


@pytest.mark.parametrize("code", list(ErrorCode))
def test_typed_errors_and_correlation(probe_app, code):
    client = TestClient(probe_app)
    response = client.get(f"/probe/error/{code}", headers={"X-Request-ID": MARKER, "authorization": MARKER})
    assert response.status_code == ERRORS[code][0]
    error = response.json()["error"]
    request_id = response.headers["x-request-id"]
    assert UUID(request_id).version == 4
    assert error == {"code": code, "message": ERRORS[code][1], "request_id": request_id, "details": None}
    assert MARKER not in response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.headers.get("retry-after") == ("2" if code == ErrorCode.RATE_LIMITED else None)
    if code == ErrorCode.UNAUTHENTICATED:
        assert response.headers["www-authenticate"] == "Bearer"
    assert client.get("/api/v1/health/live").headers["x-request-id"] != request_id


def test_unexpected_validation_router_errors_redacted(probe_app, caplog):
    client = TestClient(probe_app, cookies={"session": MARKER})
    for path, status in (("/probe/crash", 500), (f"/probe/id/{MARKER}", 422), (f"/{MARKER}", 404)):
        response = client.get(path + "?error=" + MARKER)
        assert response.status_code == status
        assert MARKER not in response.text and MARKER not in caplog.text
        assert response.json()["error"]["request_id"] == response.headers["x-request-id"]
    events = [json.loads(record.message) for record in caplog.records if record.name == "roghyar.http"]
    assert len(events) == 1 and set(events[0]) == {"event", "request_id"}
    disallowed = client.put("/api/v1/health/live")
    assert disallowed.status_code == 405 and disallowed.headers["allow"] == "GET"


def test_production_api_security_controls():
    app = main.create_app(production=True)
    secure = TestClient(app, base_url="https://testserver")
    for resource in ("/docs", "/redoc", "/openapi.json"):
        assert secure.get(resource).status_code == 404
    response = secure.get("/api/v1/health/live", headers={"Origin": "https://untrusted.example"})
    assert response.headers["strict-transport-security"] == "max-age=31536000"
    for name, value in (("x-content-type-options", "nosniff"), ("referrer-policy", "no-referrer"),
                        ("x-frame-options", "DENY"), ("cache-control", "no-store")):
        assert response.headers[name] == value
    assert "access-control-allow-origin" not in response.headers
    assert "access-control-allow-credentials" not in response.headers
    assert "strict-transport-security" not in TestClient(app).get("/api/v1/health/live").headers


@pytest.mark.parametrize(("content", "media", "status"), [
    (b"invalid", "application/json", 422), (b"{}", "multipart/form-data", 415),
    (b"{}", "application/json; charset=latin1", 415),
    (b'{"key":1,"key":2}', "application/json", 422),
    (b'{"key":"\\ud800"}', "application/json", 422),
    (b'{"key":"\\u0000"}', "application/json", 422),
    (b'{"key":1e999}', "application/json", 422),
    (b'{"key":' + b"[" * 14 + b"0" + b"]" * 14 + b"}", "application/json", 422),
    (b"x" * 257, "application/json", 413),
])
def test_bounded_json_rejects_bad_input(probe_app, content, media, status):
    client = TestClient(probe_app)
    assert client.post("/probe/json", content=content, headers={"content-type": media}).status_code == status


def test_streaming_bounds_and_multilingual_validation(probe_app):
    client = TestClient(probe_app)
    assert client.post("/probe/json", content=iter((b"x" * 160, b"x" * 100)),
                       headers={"content-type": "application/json"}).status_code == 413
    draft = validate_assessment(data())
    assert client.post("/probe/json", json={"text": draft.text}).json() == {"text": draft.text}
    assert client.get("/api/v1/health/live?" + "&".join(["x=y"] * 33)).status_code == 422
    assert client.get("/" + "x" * 2049).status_code == 422


def test_closed_assessment_ignores_body_and_services(monkeypatch):
    def unused(*args, **kwargs):
        pytest.fail("Closed routes must never read the body or call storage, triage or throttling")

    def body():
        unused()
        yield b""

    monkeypatch.setattr("roghyar.assessment.AssessmentService.create", unused)
    monkeypatch.setattr("roghyar.triage.TriageService.evaluate", unused)
    monkeypatch.setattr("roghyar.rate_limit.RedisCounter.consume", unused)
    client = TestClient(main.app)
    request = client.post("/api/v1/assessments", content=body(), headers={
        "Content-Type": "multipart/form-data", "Authorization": MARKER,
    })
    response = client.get(f"/api/v1/assessments/{MARKER}?x={'y' * 3000}")
    for item in (request, response):
        assert item.status_code == 503
        assert item.json()["error"]["request_id"] == item.headers["x-request-id"]
        assert MARKER not in item.text
        assert item.headers["cache-control"] == "no-store"


def test_live_database_disconnect_recovery(database, monkeypatch):
    with database.connect() as connection:
        pid = connection.scalar(text("SELECT pg_backend_pid()"))
    with main.engine.begin() as admin:
        assert admin.scalar(text("SELECT pg_terminate_backend(:pid, 2000)"), {"pid": pid})
        before = admin.scalar(text("SELECT count(*) FROM roghyar.assessments"))
    monkeypatch.setattr(main, "engine", database)
    with TestClient(main.app) as client:
        assert client.get("/api/v1/health/ready").status_code == 503
        assert client.get("/api/v1/health/live").status_code == 200
        assert client.get("/api/v1/health/ready").status_code == 200
        assert client.post("/api/v1/assessments", json=data()).status_code == 503
        benchmark("readiness (live pooled PostgreSQL, no auth)", lambda: client.get("/api/v1/health/ready"))
    monkeypatch.undo()
    with main.engine.begin() as admin:
        assert admin.scalar(text("SELECT count(*) FROM roghyar.assessments")) == before


def test_http_engineering_benchmarks():
    with TestClient(main.app) as client:
        benchmark("liveness (ASGI in process, no DB, no auth)", lambda: client.get("/api/v1/health/live"))
        benchmark("404 envelope (ASGI in process, no DB, no auth)", lambda: client.get("/synthetic-404"))
