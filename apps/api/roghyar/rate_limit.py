"""Shared fixed-window quotas for future enabled endpoints."""

import hashlib
import hmac
import ipaddress
import math
import re
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit

from fastapi import Request
from pydantic import SecretStr
from redis import Redis
from redis.backoff import NoBackoff
from redis.exceptions import RedisError
from redis.retry import Retry

from .http_safety import APIError, ErrorCode
from .scoped_session import AuthorizationDenied, CredentialVerifier, PrincipalKind

WINDOW = """
local count = redis.call('GET', KEYS[1])
if not count then
    redis.call('SET', KEYS[1], 1, 'PX', ARGV[2])
    return {1, tonumber(ARGV[2])}
end
local ttl = redis.call('PTTL', KEYS[1])
local used = tonumber(count)
if ttl < 0 or not used or used < 1 or used ~= math.floor(used) then
    return redis.error_reply('invalid limiter state')
end
if used >= tonumber(ARGV[1]) then
    return {0, ttl}
end
redis.call('INCR', KEYS[1])
return {1, ttl}
"""


@dataclass(frozen=True, slots=True)
class Policy:
    name: str
    quota: int
    window_seconds: int

    def __post_init__(self):
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,47}", self.name):
            raise ValueError("Invalid quota policy name")
        if type(self.quota) is not int or not 1 <= self.quota <= 100000:
            raise ValueError("Invalid quota")
        if type(self.window_seconds) is not int or not 1 <= self.window_seconds <= 86400:
            raise ValueError("Invalid quota window")


class CounterUnavailable(RuntimeError):
    pass


class Counter(Protocol):
    def consume(self, key: str, policy: Policy) -> tuple[bool, int]: ...


class RedisCounter:
    def __init__(self, client: Redis):
        self._client = client
        self._script = client.register_script(WINDOW)

    @classmethod
    def from_url(cls, url: SecretStr, *, require_tls: bool = True):
        target = url.get_secret_value()
        tls = target.startswith("rediss://")
        if "?" in target or "#" in target or (not tls and (require_tls or not target.startswith("redis://"))):
            raise CounterUnavailable("Invalid shared quota configuration")
        try:
            if not urlsplit(target).hostname:
                raise ValueError("Quota host required")
            tls_options = {"ssl_cert_reqs": "required", "ssl_check_hostname": True} if tls else {}
            return cls(Redis.from_url(target, socket_connect_timeout=0.5, socket_timeout=0.5,
                                      retry=Retry(NoBackoff(), 0), max_connections=64, **tls_options))
        except (ValueError, RedisError):
            raise CounterUnavailable("Invalid shared quota configuration") from None

    def consume(self, key: str, policy: Policy) -> tuple[bool, int]:
        try:
            allowed, ttl = self._script(keys=[key], args=[policy.quota, policy.window_seconds * 1000])
            if allowed not in (0, 1) or not 0 <= ttl <= policy.window_seconds * 1000:
                raise ValueError("Invalid quota response")
            return bool(allowed), ttl
        except (RedisError, ValueError, TypeError):
            raise CounterUnavailable("Shared quota service unavailable") from None

    def close(self) -> None:
        self._client.close()


class RateLimiter:
    def __init__(self, counter: Counter, key: SecretStr):
        self._secret = key.get_secret_value().encode("utf-8")
        if len(self._secret) < 32:
            raise ValueError("Invalid quota key configuration")
        self._counter = counter

    def enforce_peer(self, request: Request, policy: Policy) -> None:
        # The ASGI server must disable proxy-header rewriting, or explicitly trust
        # only the ingress that sanitizes the original forwarding headers.
        try:
            address = ipaddress.ip_address(request.client.host if request.client else "")
        except ValueError:
            raise APIError(ErrorCode.DEPENDENCY_UNAVAILABLE) from None
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
            address = address.ipv4_mapped
        self._enforce(policy, "peer", str(address))

    def enforce_identity(self, policy: Policy, kind: str, identity: str) -> None:
        """Apply a shared quota to a server-derived identity."""
        self._enforce(policy, kind, identity)

    def enforce_authenticated(self, credential: str | None, verifier: CredentialVerifier,
                              policy: Policy, *, required_kind: PrincipalKind) -> None:
        try:
            principal = verifier.verify(credential)
            if principal.subject is None or principal.kind != required_kind:
                raise AuthorizationDenied()
        except AuthorizationDenied:
            raise APIError(ErrorCode.UNAUTHENTICATED) from None
        self._enforce(policy, principal.kind, str(principal.subject))

    def _enforce(self, policy: Policy, kind: str, identity: str) -> None:
        scope = f"{policy.name}:{policy.quota}:{policy.window_seconds}:{kind}:{identity}"
        digest = hmac.new(self._secret, scope.encode(), hashlib.sha256).hexdigest()
        try:
            allowed, milliseconds = self._counter.consume("roghyar:quota:v1:" + digest, policy)
        except CounterUnavailable:
            raise APIError(ErrorCode.DEPENDENCY_UNAVAILABLE) from None
        if not allowed:
            raise APIError(ErrorCode.RATE_LIMITED, retry_after=max(1, math.ceil(milliseconds / 1000)))
