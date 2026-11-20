"""BK-01 restricted live PostgreSQL and Redis with an isolated SMS double."""
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
import os
from contextlib import contextmanager
from threading import Barrier, Event
from uuid import uuid4

import jwt
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from redis import Redis
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from starlette.requests import Request

from roghyar.field_encryption import FieldEncryption
from roghyar.http_safety import APIError
from roghyar.main import app, create_app, engine
from roghyar.otp_http import private_otp_router
from roghyar.otp_service import OTPDenied, OTPRequest, OTPService, OTPVerify, normalize_phone
from roghyar.otp_provider import SMSUnavailable
from roghyar.rate_limit import CounterUnavailable, Policy, RedisCounter
from roghyar.scoped_session import AuthorizationDenied, CredentialVerifier
from test_database_security import database

LOCAL = "03012345678"
CANONICAL = "+923012345678"
ORIGIN = "https://synthetic.example.invalid"
PEER = Request({"type": "http", "client": ("192.0.2.10", 1000), "headers": []})


@pytest.mark.parametrize("phone", ["", "+913012345678", "02112345678", "0301234567", "030123456789",
                                 "030-12345678", "03012345678\u200e", "03012345678' OR 1=1", 3012345678, None],
                         ids=("empty","foreign","landline","short","long","separator","unicode","injection","int","null"))
def test_bk01_invalid_phone(phone):
    assert normalize_phone(LOCAL) == normalize_phone(CANONICAL)
    with pytest.raises(ValidationError):
        OTPRequest(phone=phone, purpose="booking")


class SMS:
    def __init__(self):
        self.sent = []
        self.mode = "accepted"
        self.enter = Event()
        self.release = Event()

    def send(self, phone, code, challenge):
        self.sent.append((phone, code, challenge))
        self.enter.set()
        if self.mode == "blocked":
            assert self.release.wait(5)
        if self.mode == "timeout":
            raise SMSUnavailable()
        return self.mode != "rejected"


class Fixture:
    def __init__(self, writer, redis):
        self.writer, self.redis = writer, redis
        self.counter = RedisCounter(redis)
        self.provider = SMS()
        self.now = datetime.now(UTC)
        self.encryption = FieldEncryption(os.urandom(32), os.urandom(32))
        self.keys = dict(otp_key=os.urandom(32), signing_key=os.urandom(32), quota_key=os.urandom(32))
        self.service = self.configure()

    def configure(self, **options):
        return OTPService(self.writer, self.encryption, options.pop("counter", self.counter),
            **(self.keys | {"clock": lambda: self.now, "provider": self.provider} | options))

    def request(self, phone=LOCAL, purpose="booking"):
        self.service.request(OTPRequest(phone=phone, purpose=purpose), PEER)

    def verify(self, phone=CANONICAL, purpose="booking", code=None):
        if code is None:
            code = self.provider.sent[-1][1]
        return self.service.verify(OTPVerify(phone=phone, purpose=purpose, code=code), PEER)

    def wrong(self):
        return "000000" if self.provider.sent[-1][1] != "000000" else "999999"

    def clear_resend(self):
        from roghyar.otp_service import RESEND
        import hashlib
        import hmac
        lookup = self.encryption.phone_lookup(CANONICAL).hex()
        scope = f"{RESEND.name}:{RESEND.quota}:{RESEND.window_seconds}:phone:{lookup}"
        key = "roghyar:quota:v1:" + hmac.new(self.keys["quota_key"].hex().encode(),scope.encode(),hashlib.sha256).hexdigest()
        self.redis.delete(key)

    def rows(self, table):
        with engine.connect() as owner:
            return list(owner.execute(text(f"SELECT * FROM roghyar.{table}")).mappings())


@pytest.fixture
def otp(database):
    port = os.getenv("ROGHYAR_TEST_REDIS_PORT")
    if not port:
        if os.getenv("GITHUB_ACTIONS") == "true":
            pytest.fail("BK-01 requires live Redis on hosted CI")
        pytest.skip("Disposable Redis is required")
    with engine.begin() as owner:
        owner.exec_driver_sql("ALTER ROLE roghyar_otp_writer PASSWORD 'synthetic-otp-local'")
    writer = create_engine(engine.url.set(username="roghyar_otp_writer", password="synthetic-otp-local"),
                           hide_parameters=True)
    client = Redis(host="127.0.0.1", port=int(port), socket_timeout=1)
    fixture = Fixture(writer, client)
    try:
        yield fixture
    finally:
        fixture.provider.release.set()
        client.close()
        writer.dispose()
        with engine.begin() as owner:
            for table in ("otp_sessions", "otp_identities", "otp_challenges"):
                owner.execute(text(f"DELETE FROM roghyar.{table}"))


def test_bk01_identity_no_patient_creation_and_token_revocation(otp):
    with engine.connect() as owner:
        count = owner.scalar(text("SELECT count(*) FROM roghyar.patients"))
    otp.request()
    lookup = otp.encryption.phone_lookup(CANONICAL)
    [challenge] = otp.rows("otp_challenges")
    assert challenge["phone_lookup"] == lookup and len(challenge["verifier"]) == 32
    assert challenge["state"] == "active" and otp.rows("otp_identities") == []
    token = otp.verify()
    current = otp.service.authorize(token, "booking")
    with pytest.raises(OTPDenied, match="invalid_verification"):
        otp.verify()
    with pytest.raises(OTPDenied, match="invalid_session"):
        otp.service.authorize(token, "appointments")
    with pytest.raises(AuthorizationDenied):
        CredentialVerifier(None).verify(token)
    otp.service.revoke(token, "booking")
    with pytest.raises(OTPDenied, match="invalid_session"):
        otp.service.authorize(token, "booking")
    otp.clear_resend()
    otp.request(purpose="appointments")
    next_token = otp.verify(purpose="appointments")
    assert otp.service.authorize(next_token, "appointments").subject == current.subject
    with engine.connect() as owner:
        assert owner.scalar(text("SELECT count(*) FROM roghyar.patients")) == count


@pytest.mark.parametrize("mode", ["rejected", "timeout", "unconfigured"])
def test_bk01_provider_failures_never_activate(mode, otp):
    otp.provider.mode = mode
    service = otp.configure(provider=None) if mode == "unconfigured" else otp.service
    with pytest.raises(OTPDenied, match="provider_"):
        service.request(OTPRequest(phone=LOCAL,purpose="booking"), PEER)
    with pytest.raises(OTPDenied, match="invalid_verification"):
        otp.verify(code="123456")
    assert otp.rows("otp_sessions") == []
    assert [row["state"] for row in otp.rows("otp_challenges")] == ([] if mode == "unconfigured" else ["invalidated"])


@pytest.mark.parametrize("mode", ["expired", "attempt_cap", "wrong_purpose", "wrong_phone", "superseded"])
def test_bk01_invalid_verifications_are_indistinguishable(mode, otp):
    otp.request()
    code = otp.provider.sent[-1][1]
    params = {}
    if mode == "expired":
        otp.now += timedelta(minutes=5)
    if mode == "attempt_cap":
        for _ in range(5):
            with pytest.raises(OTPDenied, match="invalid_verification"):
                otp.verify(code=otp.wrong())
        assert otp.rows("otp_challenges")[0]["state"] == "locked"
    if mode == "wrong_purpose":
        params["purpose"] = "appointments"
    if mode == "wrong_phone":
        params["phone"] = "03012345679"
    if mode == "superseded":
        otp.clear_resend()
        otp.request()
        assert otp.provider.sent[-1][1] != code
    with pytest.raises(OTPDenied, match="invalid_verification"):
        otp.verify(code=code, **params)
    assert otp.rows("otp_sessions") == []


@pytest.mark.parametrize("case", ["two_correct", "final_attempt_race", "resend_race"])
def test_bk01_concurrent_consumption_is_atomic(case, otp):
    otp.request()
    correct = otp.provider.sent[-1][1]
    if case == "final_attempt_race":
        for _ in range(4):
            with pytest.raises(OTPDenied):
                otp.verify(code=otp.wrong())
    if case == "resend_race":
        otp.clear_resend()
    barrier = Barrier(2, timeout=5)

    def attempt(second):
        barrier.wait()
        try:
            if case == "resend_race" and second:
                otp.request()
                return "sent"
            wrong = case == "final_attempt_race" and second
            otp.verify(code=otp.wrong() if wrong else correct)
            return "verified"
        except OTPDenied:
            return "denied"

    with ThreadPoolExecutor(2) as executor:
        outcomes = list(executor.map(attempt, (False, True)))
    assert outcomes.count("verified") == len(otp.rows("otp_sessions")) <= 1
    if case == "two_correct":
        assert sorted(outcomes) == ["denied", "verified"]


def test_bk01_parallel_resends_use_shared_redis(otp):
    ready = Barrier(2, timeout=5)
    services = (otp.service, otp.configure(counter=RedisCounter(otp.redis)))

    def request(service):
        ready.wait()
        try:
            service.request(OTPRequest(phone=LOCAL,purpose="booking"), PEER)
            return "sent"
        except APIError as exc:
            assert exc.code == "RATE_LIMITED" and 0 < exc.retry_after <= 45
            return "limited"

    with ThreadPoolExecutor(2) as executor:
        assert sorted(executor.map(request, services)) == ["limited", "sent"]
    assert len(otp.provider.sent) == 1


def test_bk01_redis_disconnected_and_global_budget(otp, monkeypatch):
    from roghyar import otp_service
    monkeypatch.setattr(otp_service, "SEND_BUDGET", Policy("otp_provider_budget", 1, 10))
    otp.request()
    with pytest.raises(APIError, match="RATE_LIMITED"):
        otp.request(phone="03012345679")
    assert len(otp.provider.sent) == 1

    class Down:
        def consume(self, key, policy):
            raise CounterUnavailable()

    service = otp.configure(counter=Down())
    for operation, query in ((service.request, OTPRequest(phone=LOCAL,purpose="booking")),
                             (service.verify, OTPVerify(phone=LOCAL,purpose="booking",code=otp.provider.sent[0][1]))):
        with pytest.raises(APIError, match="DEPENDENCY_UNAVAILABLE"):
            operation(query, PEER)
    assert otp.rows("otp_sessions") == []


def test_bk01_late_ack_cannot_revive_superseded_challenge(otp):
    otp.provider.mode = "blocked"
    with ThreadPoolExecutor(1) as executor:
        old = executor.submit(otp.request)
        assert otp.provider.enter.wait(timeout=3)
        otp.clear_resend()
        otp.provider.mode = "accepted"
        otp.request()
        otp.provider.release.set()
        with pytest.raises(OTPDenied, match="provider_unavailable"):
            old.result(timeout=5)
    assert sorted(row["state"] for row in otp.rows("otp_challenges")) == ["active", "superseded"]
    with pytest.raises(OTPDenied, match="invalid_verification"):
        otp.verify(code=otp.provider.sent[0][1])
    assert otp.verify()


@pytest.mark.parametrize("case", ["issuer", "audience", "key", "type", "future", "expired", "unknown_subject",
                                  "admin", "signature", "algorithm", "revoked_identity"])
def test_bk01_token_denials_require_persisted_session(case, otp):
    otp.request()
    token = otp.verify()
    assert otp.service.authorize(token, "booking")
    claims = jwt.decode(token, options={"verify_signature": False})
    headers = jwt.get_unverified_header(token)
    override = {"issuer": {"iss": "wrong"}, "audience": {"aud": "wrong"},
                "future": {"iat": claims["iat"] + 60}, "expired": {"exp": claims["iat"]},
                "unknown_subject": {"sub": str(uuid4())}, "admin": {"kind": "hospital_admin"}}
    if case == "key":
        headers["kid"] = "unknown"
    if case == "type":
        headers["typ"] = "JWT"
    if case == "algorithm":
        headers["alg"] = "HS384"
    if case == "revoked_identity":
        with engine.begin() as owner:
            owner.execute(text("UPDATE roghyar.otp_identities SET revoked_at=now()"))
    alternate = jwt.encode(claims | override.get(case, {}),
                           os.urandom(32) if case == "signature" else otp.keys["signing_key"],
                           algorithm=headers["alg"], headers=headers)
    with pytest.raises(OTPDenied, match="invalid_session"):
        otp.service.authorize(alternate, "booking")


def test_bk01_private_http_and_rls(otp, database):
    for path in ("/otp/request", "/otp/verify", "/api/v1/otp/request", "/api/v1/otp/verify"):
        assert TestClient(app).post(path, json={"phone": LOCAL}).status_code == 404
    private = create_app(production=False)
    private.include_router(private_otp_router(otp.service, origin=ORIGIN))
    client = TestClient(private, base_url=ORIGIN, client=("192.0.2.20", 12345))
    body = {"phone": LOCAL, "purpose": "booking"}
    assert client.post("/otp/request", json=body).status_code == 403
    headers = {"Origin": ORIGIN}
    assert client.post("/otp/request", content="not JSON", headers=headers).status_code == 415
    assert client.post("/otp/request", json=body | {"extra": "x"*600}, headers=headers).status_code == 413
    assert client.post("/otp/request", json=body | {"extra": "x"}, headers=headers).status_code == 422
    assert client.post("/otp/request", json=body | {"purpose": "admin"}, headers=headers).status_code == 422
    duplicate = '{"phone":"03012345678","phone":"03012345678","purpose":"booking"}'
    assert client.post("/otp/request", content=duplicate,
                       headers=headers | {"Content-Type": "application/json"}).status_code == 422
    assert not otp.provider.sent
    result = client.post("/otp/request", json=body, headers=headers)
    assert result.status_code == 202 and result.json() == {"status": "accepted"}
    assert result.headers["cache-control"] == "no-store" and "set-cookie" not in result.headers
    denial = client.post("/otp/verify", json=body | {"code": otp.wrong()}, headers=headers)
    assert denial.status_code == 401 and LOCAL not in denial.text and "request_id" in denial.json()["error"]
    issued = client.post("/otp/verify", json=body | {"code": otp.provider.sent[0][1]}, headers=headers)
    assert issued.status_code == 200 and issued.headers["cache-control"] == "no-store"
    assert otp.service.authorize(issued.json()["token"], "booking")
    for connection, query in ((database, "SELECT * FROM roghyar.otp_challenges"),
                              (database, "SELECT * FROM roghyar.otp_sessions"),
                              (otp.writer, "UPDATE roghyar.patients SET age=0"),
                              (otp.writer, "UPDATE roghyar.appointments SET status='cancelled'"),
                              (otp.writer, "UPDATE roghyar.otp_challenges SET state='active'")):
        with pytest.raises(DBAPIError):
            with connection.begin() as session:
                session.execute(text(query))



@pytest.mark.parametrize("mode", ["accepted", "reject", "redirect", "wrong_id", "duplicate", "oversize", "timeout"])
def test_bk01_gateway_contract(mode, monkeypatch):
    import json

    import httpx
    from pydantic import SecretStr

    from roghyar import otp_provider

    challenge = uuid4()
    endpoint = "https://8.8.8.8/v1/otp"
    gateway = otp_provider.SMSGateway(endpoint, approved_hosts=frozenset({"8.8.8.8"}),
                                      secret=SecretStr("synthetic-provider-secret"))
    calls = []

    def send(request):
        calls.append(request)
        assert str(request.url) == endpoint and request.method == "POST"
        assert json.loads(request.content) == {"phone": CANONICAL, "code": "123456", "request_id": str(challenge)}
        assert request.headers["authorization"] == "Bearer synthetic-provider-secret"
        if mode == "timeout":
            raise httpx.ReadTimeout("synthetic")
        if mode in {"reject", "redirect"}:
            return httpx.Response(307 if mode == "redirect" else 403, headers={"Location": "https://example.invalid"})
        if mode == "duplicate":
            return httpx.Response(202, headers={"Content-Type": "application/json"},
                                  stream=httpx.ByteStream(b'{"status":"wrong","status":"accepted"}'))
        result = {"status": "accepted", "request_id": str(uuid4() if mode == "wrong_id" else challenge)}
        if mode == "oversize":
            result["padding"] = "x" * 2100
        return httpx.Response(202, headers={"Content-Type": "application/json"},
                              stream=httpx.ByteStream(json.dumps(result).encode()))

    client_class = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client_class(transport=httpx.MockTransport(send), **kwargs))
    if mode in {"timeout", "duplicate"}:
        with pytest.raises(SMSUnavailable):
            gateway.send(CANONICAL, "123456", challenge)
    else:
        assert gateway.send(CANONICAL, "123456", challenge) is (mode == "accepted")
    assert len(calls) == 1



def test_bk01_postgresql_rollback_after_ack(otp, monkeypatch, caplog):
    transaction = otp.service.transaction
    starts = 0

    @contextmanager
    def fail_second_commit(lookup=None):
        nonlocal starts
        starts += 1
        if starts != 2:
            with transaction(lookup) as session:
                yield session
            return
        with pytest.raises(RuntimeError, match="synthetic commit failure"):
            with transaction(lookup) as session:
                yield session
                raise RuntimeError("synthetic commit failure")
        raise OTPDenied("database_unavailable")

    monkeypatch.setattr(otp.service, "transaction", fail_second_commit)
    with pytest.raises(OTPDenied, match="database_unavailable"):
        otp.request()
    code = otp.provider.sent[0][1]
    row, = otp.rows("otp_challenges")
    assert row["state"] == "pending"
    assert len(row["verifier"]) == 32 and bytes(row["verifier"]) != code.encode()
    with pytest.raises(OTPDenied, match="invalid_verification"):
        otp.verify()
    assert not otp.rows("otp_sessions")
    assert LOCAL not in caplog.text and CANONICAL not in caplog.text and code not in caplog.text


def test_bk01_writer_cannot_undo_session_revocation(otp):
    otp.request()
    token = otp.verify()
    otp.service.revoke(token, "booking")
    with pytest.raises(DBAPIError):
        with otp.writer.begin() as session:
            session.execute(text("UPDATE roghyar.otp_sessions SET revoked_at=NULL"))
    with pytest.raises(OTPDenied, match="invalid_session"):
        otp.service.authorize(token, "booking")
