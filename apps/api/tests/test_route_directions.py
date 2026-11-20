"""Synthetic routing acceptance. The only HTTP server is a disposable localhost fixture."""
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Event, Thread
from time import perf_counter, sleep
from uuid import uuid4
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from directory_fixtures import directory_actors, governed_fixtures
from roghyar.main import app, engine
from roghyar.route_directions import DestinationRequest, ExternalMaps, RouteRequest, RouteSession, Routing
from roghyar.routing_destination import Destination, DestinationDenied, DestinationReader
from roghyar.routing_provider import GatewayPolicy, Point, RoutingGateway
from roghyar.scoped_session import CredentialVerifier, scoped_session
from test_database_security import database
from test_doctor_governance import doctor_actors, doctors

NOW = datetime(2030, 1, 7, tzinfo=UTC)
DESTINATION = Destination(hospital_id=uuid4(), revision=1, name="Synthetic reference",
    address="Synthetic area", point=Point(latitude=1.1, longitude=2.2),
    verified_on=NOW-timedelta(days=1), expires_at=NOW+timedelta(hours=1))
REQ = RouteRequest(origin=Point(latitude=1, longitude=2), origin_observed_at=NOW-timedelta(seconds=1),
    accuracy_meters=15, destination=DestinationRequest(hospital_id=DESTINATION.hospital_id, revision=1),
    mode="driving")
APPROVED = frozenset({"93.184.215.14"})
ENDPOINT = "https://93.184.215.14/v1/route"


class Reader:
    def resolve(self, hospital, revision, doctor, now):
        assert (hospital, revision, doctor) == (DESTINATION.hospital_id, 1, None)
        return DESTINATION


class LocalTransport(httpx.AsyncHTTPTransport):
    def __init__(self, port):
        super().__init__()
        self.port = port

    async def handle_async_request(self, request):
        # Test-only rewriting of the approved address, with no public networking.
        assert str(request.url) == ENDPOINT
        request.url = request.url.copy_with(scheme="http", host="127.0.0.1", port=self.port)
        return await super().handle_async_request(request)


class LocalGateway(RoutingGateway):
    def __init__(self, port):
        super().__init__(GatewayPolicy(provider="synthetic_stub", endpoint=ENDPOINT,
            modes=frozenset({"driving", "walking"}), external_disclosure_approved=True,
            secret=SecretStr("synthetic-credential")), approved_hosts=APPROVED)
        self.port = port

    def client(self, timeout):
        return httpx.AsyncClient(timeout=timeout, transport=LocalTransport(self.port), trust_env=False,
                            follow_redirects=False)


@pytest.fixture
def stub():
    state = {"case": "valid", "requests": [], "enter": Event(), "release": Event()}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state["requests"].append((self.path, dict(self.headers), data))
            state["enter"].set()
            case = state["case"]
            if case == "timeout":
                sleep(1.0)
            if case == "wait":
                state["release"].wait(timeout=5)
            response = data | dict(status="route", route_id="synthetic_route", meters=24_000,
                geometry=[data["origin"], data["destination"]], duration_seconds=1200,
                observed_at=(NOW-timedelta(seconds=5)).isoformat(), expires_at=(NOW+timedelta(minutes=1)).isoformat(),
                eta_expires_at=(NOW+timedelta(seconds=20)).isoformat())
            code, content_type = 200, "application/json"
            if case == "redirect":
                code = 302
            if case == "unavailable":
                code = 503
            if case == "content_type":
                content_type = "text/plain"
            if case == "no_eta":
                del response["duration_seconds"], response["eta_expires_at"]
            if case == "no_geometry":
                del response["geometry"]
            if case == "stale_eta":
                response["eta_expires_at"] = (NOW-timedelta(seconds=1)).isoformat()
            if case == "stale_route":
                response["expires_at"] = (NOW-timedelta(seconds=1)).isoformat()
            if case == "wrong_mode":
                response["mode"] = "walking"
            if case == "wrong_destination":
                response["destination"] = {"latitude": 0, "longitude": 0}
            if case == "wrong_request":
                response["request_id"] = str(uuid4())
            if case == "wrong_version":
                response["version"] = 2
            if case == "negative_distance":
                response["meters"] = -1
            if case == "incomplete":
                del response["meters"]
            if case in ("negative_eta", "zero_eta", "nan", "infinity", "excessive_eta"):
                response["duration_seconds"] = {"negative_eta": -1, "zero_eta": 0, "nan": float("nan"),
                    "infinity": float("inf"), "excessive_eta": 999_999}[case]
            if case == "geometry":
                response["geometry"] = [data["destination"], data["origin"]]
            if case == "geometry_limit":
                response["geometry"] = [data["origin"]] * 1025
            if case == "bad_coordinate":
                response["geometry"][0] = {"latitude": 91, "longitude": 0}
            if case == "no_route":
                response = data | dict(status="no_route", observed_at=NOW.isoformat(),
                                       expires_at=(NOW+timedelta(seconds=20)).isoformat())
            payload = json.dumps(response).encode()
            if case == "json":
                payload = b"{"
            if case == "duplicate":
                payload = payload[:-1] + b',"status":"route"}'
            if case == "oversize":
                payload = b"x" * 96_001
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            if case == "redirect":
                self.send_header("Location", "http://169.254.169.254")
            self.end_headers()
            try:
                if case == "slow_body":
                    for offset in range(0, len(payload), 8):
                        self.wfile.write(payload[offset:offset + 8])
                        self.wfile.flush()
                        sleep(0.02)
                else:
                    self.wfile.write(payload)
            except ConnectionError:
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
    thread.start()
    try:
        yield state, LocalGateway(server.server_port)
    finally:
        state["release"].set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def call(provider, request=REQ, reader=None, **overrides):
    session = RouteSession()
    grant = session.authorize(request, recipient="route:synthetic_stub", now=NOW, user_confirmed=True)
    service = Routing(reader or Reader(), **(dict(provider=provider, enabled=True, disclosure_approved=True,
                                                  clock=lambda: NOW) | overrides))
    return service.route(request, session, grant)


@pytest.mark.parametrize("case,status", [
    ("valid", "route_available"), ("no_eta", "route_available"), ("no_geometry", "route_available"),
    ("stale_eta", "route_available"), ("no_route", "no_route"), ("timeout", "provider_timeout"),
    ("slow_body", "provider_timeout"),
    ("redirect", "provider_unavailable"), ("unavailable", "provider_unavailable"),
    *[(case, "invalid_provider_response") for case in ("stale_route", "content_type", "json", "duplicate", "oversize",
        "wrong_version", "wrong_request", "wrong_mode", "wrong_destination", "negative_distance", "incomplete",
        "negative_eta", "zero_eta", "nan", "infinity", "excessive_eta", "geometry", "geometry_limit", "bad_coordinate")],
], ids=lambda value: value)
def test_loc06_provider_contract(case, status, stub, caplog):
    state, provider = stub
    state["case"] = case
    request = REQ.model_copy(update={"timeout_seconds": 0.5 if case in ("timeout", "slow_body") else 3.0})
    result = call(provider, request)
    assert result.status == status and len(state["requests"]) == 1
    path, headers, body = state["requests"][0]
    assert path == "/v1/route" and "?" not in path
    assert set(body) == {"version", "request_id", "mode", "origin", "destination"}
    assert headers["Authorization"] == "Bearer synthetic-credential"
    assert "synthetic-credential" not in caplog.text
    if status != "route_available":
        assert result.eta is None and result.geometry is None
        return
    assert result.destination == DESTINATION and result.provider == "synthetic_stub" and result.meters == 24_000
    assert (result.geometry is None) == (case == "no_geometry")
    expected = {"no_eta": "eta_unavailable", "stale_eta": "eta_stale"}.get(case, "estimate")
    assert result.eta_status == expected
    if expected == "estimate":
        assert result.eta.duration_seconds == 1200 and result.eta.traffic is None
    else:
        assert result.eta is None


def test_loc06_walking_binding_and_bounded_provenance(stub):
    _, provider = stub
    result = call(provider, REQ.model_copy(update={"mode": "walking"}))
    assert result.mode == result.eta.mode == "walking"
    assert result.geometry == (REQ.origin, DESTINATION.point)
    assert result.expires_at == REQ.origin_observed_at + timedelta(seconds=30)
    provider.modes = frozenset({"driving"})
    assert call(provider, REQ.model_copy(update={"mode": "walking"})).status == "unsupported_mode"


@pytest.mark.parametrize("endpoint", [
    "http://93.184.215.14/v1/route", "https://169.254.169.254/v1/route", "https://127.0.0.1/v1/route",
    "https://[::1]/v1/route", "https://unapproved.example/v1/route",
    "https://93.184.215.14:444/v1/route", "https://token@93.184.215.14/v1/route",
    "https://93.184.215.14/v1/route?token=synthetic", "https://93.184.215.14/v1/other",
])
def test_loc06_endpoint_policy(endpoint):
    policy = GatewayPolicy(provider="synthetic_stub", endpoint=endpoint, modes=frozenset({"driving"}),
                           external_disclosure_approved=True)
    with pytest.raises(ValueError):
        RoutingGateway(policy, approved_hosts=APPROVED | {"169.254.169.254", "127.0.0.1", "::1"})
    assert RoutingGateway(policy.model_copy(update={"endpoint": ENDPOINT}), approved_hosts=APPROVED).endpoint == ENDPOINT


@pytest.mark.parametrize("case,status", [
    ("lat", "invalid_origin"), ("lng", "invalid_origin"), ("bool", "invalid_origin"), ("nan", "invalid_origin"),
    ("accuracy", "invalid_origin"), ("stale", "stale_origin"), ("destination", "invalid_destination"),
    ("revision", "invalid_destination"), ("mode", "unsupported_mode"), ("options", "invalid_origin"),
], ids=lambda v: v)
def test_loc06_request_validation(case, status, stub):
    state, provider = stub
    edits = {"lat": {"origin": {"latitude": 91, "longitude": 0}},
             "lng": {"origin": {"latitude": 0, "longitude": 181}},
             "bool": {"origin": {"latitude": True, "longitude": 0}},
             "nan": {"origin": {"latitude": 0, "longitude": float("nan")}},
             "accuracy": {"accuracy_meters": 300}, "stale": {"origin_observed_at": NOW-timedelta(seconds=30)},
             "destination": {"destination": {"hospital_id": "bad", "revision": 1}},
             "revision": {"destination": {"hospital_id": DESTINATION.hospital_id}},
             "mode": {"mode": "ambulance"}, "options": {"raw_url": ENDPOINT}}
    service = Routing(Reader(), provider=provider, enabled=True, disclosure_approved=True, clock=lambda: NOW)
    assert service.route(REQ.model_dump() | edits[case], RouteSession(), None).status == status
    assert state["requests"] == []


def test_loc06_disclosure_offline_replay_and_cross_session(stub):
    state, provider = stub
    assert call(None).status == "provider_unconfigured"
    assert call(provider, enabled=False).status == "routing_disabled"
    assert call(provider, disclosure_approved=False).status == "disclosure_not_approved"
    svc = Routing(Reader(), provider=provider, enabled=True, disclosure_approved=True, clock=lambda: NOW)
    session = RouteSession()
    assert svc.route(REQ, session, None).status == "consent_required"
    grant = session.authorize(REQ, recipient="route:synthetic_stub", now=NOW, user_confirmed=True)
    assert svc.route(REQ, RouteSession(), grant).status == "consent_required"
    assert svc.route(REQ.model_copy(update={"mode": "walking"}), session, grant).status == "consent_required"
    assert svc.route(REQ, session, grant, online=False).status == "provider_unavailable"
    assert svc.route(REQ, session, grant).status == "consent_required"
    assert state["requests"] == []
    fresh = session.authorize(REQ, recipient="route:synthetic_stub", now=NOW, user_confirmed=True)
    assert svc.route(REQ, session, fresh).status == "route_available"
    assert svc.route(REQ, session, fresh).status == "consent_required"
    assert len(state["requests"]) == 1


@pytest.mark.parametrize("case", ["permission_revoked", "stopped", "navigation", "different_request"])
def test_loc06_inflight_cancellation(case, stub):
    state, provider = stub
    state["case"] = "wait"
    svc = Routing(Reader(), provider=provider, enabled=True, disclosure_approved=True, clock=lambda: NOW)
    session = RouteSession()
    grant = session.authorize(REQ, recipient="route:synthetic_stub", now=NOW, user_confirmed=True)
    with ThreadPoolExecutor(2) as workers:
        response = workers.submit(svc.route, REQ, session, grant)
        assert state["enter"].wait(2)
        if case == "different_request":
            alternate = REQ.model_copy(update={"mode": "walking"})
            new_grant = session.authorize(alternate, recipient="route:synthetic_stub", now=NOW, user_confirmed=True)
        else:
            session.cancel()
        state["release"].set()
        assert response.result(timeout=5).status == "request_cancelled"
    if case == "different_request":
        state["case"] = "valid"
        assert svc.route(alternate, session, new_grant).mode == "walking"


def test_loc06_external_maps_destination_only():
    template = "https://maps.example.invalid/directions"
    session = RouteSession()
    assert ExternalMaps(Reader(), clock=lambda: NOW).prepare(REQ.destination, session, None).status == "map_unconfigured"
    maps = ExternalMaps(Reader(), provider="synthetic_map", template=template,
                        approved_templates=frozenset({template}), clock=lambda: NOW)
    assert maps.prepare(REQ.destination, session, None).status == "consent_required"
    with pytest.raises(ValueError):
        ExternalMaps(Reader(), provider="synthetic_map", template="javascript:alert(1)",
                     approved_templates=frozenset({"javascript:alert(1)"}))
    grant = session.authorize(REQ.destination, recipient="map:synthetic_map", now=NOW, user_confirmed=True)
    result = maps.prepare(REQ.destination, session, grant)
    assert result.status == "handoff_ready" and result.recipient == "synthetic_map"
    assert parse_qs(urlsplit(result.url).query) == {"destination": ["1.1,2.2"]}
    assert maps.prepare(REQ.destination, session, grant).status == "consent_required"


def test_loc06_provider_connection_failure():
    server = ThreadingHTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
    port = server.server_port
    server.server_close()
    assert call(LocalGateway(port)).status == "provider_unavailable"
    paths = ("/api/v1/routing", "/api/v1/hospitals/directions", "/api/v1/doctors/directions")
    client = TestClient(app)
    assert all(client.post(path, json={"origin": [12, 23]}).status_code == 404 for path in paths)


def test_loc06_governed_destination_and_database_boundaries(database, governed_fixtures):
    fx = governed_fixtures
    candidate = fx.candidate()
    now = datetime.now(UTC)
    reader = DestinationReader(database)
    with pytest.raises(DestinationDenied, match="destination_unverified"):
        reader.resolve(candidate.hospital_id, 1, None, now)
    fx.publish(candidate)
    resolved = reader.resolve(candidate.hospital_id, 1, None, now)
    assert resolved.point == Point(latitude=candidate.latitude, longitude=candidate.longitude)
    assert resolved.revision == 1 and "source" not in resolved.model_dump()
    with pytest.raises(DestinationDenied, match="destination_changed"):
        reader.resolve(candidate.hospital_id, 2, None, now)
    with pytest.raises(DestinationDenied, match="destination_database_unavailable"):
        DestinationReader(engine).resolve(candidate.hospital_id, 1, None, now)
    with pytest.raises(DBAPIError):
        with scoped_session(database, CredentialVerifier(None), None) as tx:
            tx.execute(text("UPDATE roghyar.hospitals SET published=true WHERE id=:id"), {"id": candidate.hospital_id})


@pytest.mark.parametrize("case", ["withdrawal", "source", "key", "expiry", "replacement"])
def test_loc06_governed_destination_invalidation(case, database, governed_fixtures):
    fx = governed_fixtures
    hospital = fx.candidate()
    fx.publish(hospital)
    reader = DestinationReader(database)
    now = datetime.now(UTC)
    assert reader.resolve(hospital.hospital_id, 1, None, now).revision == 1
    if case == "withdrawal":
        fx.services["operator"].withdraw(hospital.hospital_id)
    if case == "source":
        fx.revoke("source", f"{hospital.source.organization}:{hospital.source.record}")
    if case == "key":
        fx.revoke("key", fx.credential)
    if case == "expiry":
        with engine.begin() as owner:
            owner.execute(text("UPDATE roghyar.directory_current SET expires_at=now()-interval '1 second' "
                               "WHERE hospital_id=:id"), {"id": hospital.hospital_id})
    if case == "replacement":
        revision = hospital.model_copy(update={"revision": 2, "longitude": 141.0,
                    "source": hospital.source.model_copy(update={"version": 2})})
        fx.publish(revision)
        assert reader.resolve(hospital.hospital_id, 2, None, now).point.longitude == 141
    with pytest.raises(DestinationDenied, match="destination_changed" if case == "replacement" else "destination_unverified"):
        reader.resolve(hospital.hospital_id, 1, None, now)


def test_loc06_doctor_affiliation_and_withdrawal(doctors, governed_fixtures):
    fx = governed_fixtures
    item = doctors.candidate()
    doctors.activate(item)
    second = fx.candidate()
    fx.publish(second)
    now = datetime.now(UTC)
    reader = DestinationReader(doctors.runtime, vocabulary=doctors.taxonomy)
    assert reader.resolve(item.hospital_id, 1, item.doctor_id, now).doctor_id == item.doctor_id
    with pytest.raises(DestinationDenied, match="affiliation_unavailable"):
        reader.resolve(second.hospital_id, 1, item.doctor_id, now)
    with engine.begin() as owner:
        owner.execute(text("UPDATE roghyar.doctor_current SET expires_at=now()-interval '1 second' WHERE doctor_id=:id"),
                      {"id": item.doctor_id})
    with pytest.raises(DestinationDenied, match="affiliation_unavailable"):
        reader.resolve(item.hospital_id, 1, item.doctor_id, now)
    fx.services["operator"].withdraw(item.hospital_id)
    with pytest.raises(DestinationDenied, match="destination_unverified"):
        reader.resolve(item.hospital_id, 1, item.doctor_id, now)


def test_loc06_withdrawal_during_http_and_adapter_measurement(database, governed_fixtures, stub):
    fx = governed_fixtures
    hospital = fx.candidate(latitude=1.1, longitude=2.2)
    fx.publish(hospital)
    reader = DestinationReader(database)
    now = datetime.now(UTC)
    request = REQ.model_copy(update={"origin_observed_at": NOW,
        "destination": DestinationRequest(hospital_id=hospital.hospital_id, revision=1)})
    state, provider = stub

    class TestClockReader:
        def resolve(self, hospital, revision, doctor, _):
            destination = reader.resolve(hospital, revision, doctor, now)
            return destination.model_copy(update={"expires_at": NOW+timedelta(minutes=1)})

    start = perf_counter()
    for _ in range(5):
        assert reader.resolve(hospital.hospital_id, 1, None, now)
    db_ms = (perf_counter() - start) * 200
    start = perf_counter()
    for _ in range(5):
        assert call(provider, request, reader=TestClockReader()).status == "route_available"
    local_ms = (perf_counter() - start) * 200
    print(f"\nLOC06 synthetic routing n=5 destination_ms={db_ms:.3f} stub_with_checks_ms={local_ms:.3f}")
    state["enter"].clear()
    state["case"] = "wait"
    with ThreadPoolExecutor(1) as worker:
        running = worker.submit(call, provider, request, TestClockReader())
        assert state["enter"].wait(2)
        fx.services["operator"].withdraw(hospital.hospital_id)
        state["release"].set()
        assert running.result(timeout=5).status == "destination_unverified"


def test_loc06_legacy_and_unpublished_destination_denied(database, governed_fixtures):
    fx = governed_fixtures
    candidate = fx.candidate()
    reader = DestinationReader(database)
    with engine.begin() as owner:
        owner.execute(text("""
            INSERT INTO roghyar.hospitals(id,name,address,city,district,province,location,verified,published,
                                         verified_on,verified_by,source)
            VALUES (:id,'Synthetic legacy','Synthetic area','Synthetic city','Synthetic area','Synthetic province',
                ST_SetSRID(ST_MakePoint(10,10),4326)::geography,true,true,now(),gen_random_uuid(),'Synthetic source')
        """), {"id": candidate.hospital_id})
    with pytest.raises(DestinationDenied, match="destination_unverified"):
        reader.resolve(candidate.hospital_id, 1, None, datetime.now(UTC))
    fx.publish(candidate)
    assert reader.resolve(candidate.hospital_id, 1, None, datetime.now(UTC))
    with engine.begin() as owner:
        owner.execute(text("UPDATE roghyar.hospitals SET published=false WHERE id=:id"), {"id": candidate.hospital_id})
    with pytest.raises(DestinationDenied, match="destination_unverified"):
        reader.resolve(candidate.hospital_id, 1, None, datetime.now(UTC))
    with pytest.raises(DestinationDenied, match="destination_unverified"):
        reader.resolve(uuid4(), 1, None, datetime.now(UTC))


def test_loc06_concurrent_requests_cannot_reuse_consent(stub):
    state, provider = stub
    state["case"] = "wait"
    service = Routing(Reader(), provider=provider, enabled=True, disclosure_approved=True, clock=lambda: NOW)
    session = RouteSession()
    grant = session.authorize(REQ, recipient="route:synthetic_stub", now=NOW, user_confirmed=True)
    with ThreadPoolExecutor(1) as worker:
        first = worker.submit(service.route, REQ, session, grant)
        assert state["enter"].wait(2)
        assert service.route(REQ, session, grant).status == "consent_required"
        state["release"].set()
        assert first.result(timeout=5).status == "route_available"
    assert len(state["requests"]) == 1
