"""Disposable quota tests with no production user identifiers."""

import os
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from time import monotonic, sleep
from uuid import uuid4

import pytest
from fastapi import Request
from fastapi.testclient import TestClient
from pydantic import SecretStr

from roghyar import main
from roghyar.http_safety import APIError, ErrorCode
from roghyar.rate_limit import CounterUnavailable, Policy, RateLimiter, RedisCounter
from test_database_security import VERIFIER, token
from test_http_safety import benchmark

SECRET = SecretStr("SYNTHETIC DEVELOPMENT ONLY quota secret")


class DisposableCounter:
    """In-process fixture; there is no in-memory fallback in application code."""

    def __init__(self):
        self.values = {}
        self.lock = Lock()
        self.now = 0

    def consume(self, key, policy):
        with self.lock:
            count, expiry = self.values.get(key, (0, 0))
            if expiry <= self.now:
                count, expiry = 0, self.now + policy.window_seconds * 1000
            self.values[key] = (count + (count < policy.quota), expiry)
            return count < policy.quota, expiry - self.now


def request(ip="192.0.2.1", spoof="203.0.113.8"):
    return Request({"type": "http", "client": (ip, 8000),
                    "headers": [(b"x-forwarded-for", spoof.encode()), (b"forwarded", spoof.encode())]})


def test_policy_identity_separation_and_spoof_resistance():
    counter = DisposableCounter()
    limiter = RateLimiter(counter, SECRET)
    policy = Policy("assessment", 1, 10)
    limiter.enforce_peer(request(), policy)
    for address in ("192.0.2.1", "::ffff:192.0.2.1"):
        with pytest.raises(APIError) as error:
            limiter.enforce_peer(request(ip=address, spoof="198.51.100.12"), policy)
        assert error.value.code == ErrorCode.RATE_LIMITED and error.value.retry_after == 10
    limiter.enforce_peer(request(ip="192.0.2.2"), policy)
    limiter.enforce_peer(request(), Policy("otp", 1, 10))
    assert len(counter.values) == 3
    assert all(key.startswith("roghyar:quota:v1:") and "192.0.2" not in key for key in counter.values)
    counter.now = 9999
    with pytest.raises(APIError) as last:
        limiter.enforce_peer(request(), policy)
    assert last.value.retry_after == 1
    counter.now = 10000
    limiter.enforce_peer(request(), policy)


def test_authenticated_buckets_verify_subject_and_kind():
    counter = DisposableCounter()
    limiter = RateLimiter(counter, SECRET)
    policy = Policy("verified", 1, 2)
    subject = uuid4()
    credential = token("patient", subject)
    limiter.enforce_authenticated(credential, VERIFIER, policy, required_kind="patient")
    for forged in (None, "arbitrary patient token", token("hospital_admin", subject)):
        with pytest.raises(APIError) as invalid:
            limiter.enforce_authenticated(forged, VERIFIER, policy, required_kind="patient")
        assert invalid.value.code == ErrorCode.UNAUTHENTICATED
    with pytest.raises(APIError) as repeat:
        limiter.enforce_authenticated(credential, VERIFIER, policy, required_kind="patient")
    assert repeat.value.code == ErrorCode.RATE_LIMITED
    assert all(str(subject) not in key and credential not in key for key in counter.values)


def test_backend_outage_fails_closed_and_leaves_closed_routes_independent(caplog):
    class Broken:
        def consume(self, key, policy):
            raise CounterUnavailable("SYNTHETIC private backend details")

    limiter = RateLimiter(Broken(), SECRET)
    app = main.create_app(production=False)

    @app.get("/probe/quota")
    def guarded(request: Request):
        limiter.enforce_peer(request, Policy("probe", 1, 10))
        pytest.fail("Quota outage must prevent endpoint work")

    client = TestClient(app, client=("192.0.2.1", 1234))
    response = client.get("/probe/quota")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE"
    assert "SYNTHETIC" not in response.text + caplog.text
    assert client.get("/api/v1/health/live").status_code == 200
    assert client.post("/api/v1/assessments", content=b"invalid").status_code == 503


def test_redis_configuration_defaults_to_verified_tls():
    for url in ("redis://127.0.0.1:6379", "rediss://localhost?ssl_cert_reqs=none"):
        with pytest.raises(CounterUnavailable):
            RedisCounter.from_url(SecretStr(url))


def test_live_redis_atomic_quota_across_connections():
    port = os.getenv("ROGHYAR_TEST_REDIS_PORT")
    if port is None:
        if os.getenv("GITHUB_ACTIONS") == "true":
            pytest.fail("Hosted verification requires disposable Redis")
        pytest.skip("Set ROGHYAR_TEST_REDIS_PORT for disposable Redis tests")
    assert os.getenv("ROGHYAR_ENVIRONMENT") == "test"
    url = SecretStr(f"redis://127.0.0.1:{int(port)}")
    first, second = [RedisCounter.from_url(url, require_tls=False) for _ in range(2)]
    policy = Policy("synthetic_concurrent", 19, 10)
    key = "roghyar:quota:v1:synthetic:" + uuid4().hex

    def consume(index):
        return (first if index % 2 else second).consume(key, policy)

    try:
        with ThreadPoolExecutor(max_workers=16) as workers:
            results = list(workers.map(consume, range(80)))
        assert sum(allowed for allowed, _ in results) == policy.quota
        assert all(0 < ttl <= 10000 for _, ttl in results)
        assert second.consume(key, policy)[0] is False
        benchmark("shared Redis (loopback, HMAC peer, no auth)", lambda: RateLimiter(first, SECRET).enforce_peer(
            request(), Policy("synthetic_perf", 1000, 1),
        ))
        short = Policy("synthetic_expiry", 1, 1)
        assert first.consume(key + ":expiry", short)[0]
        allowed, ttl = second.consume(key + ":expiry", short)
        assert not allowed and 0 < ttl <= 1000
        deadline = monotonic() + 3
        while monotonic() < deadline:
            if second.consume(key + ":expiry", short)[0]:
                break
            sleep(0.02)
        else:
            pytest.fail("The shared quota did not reset at expiration")
    finally:
        first.close()
        second.close()
