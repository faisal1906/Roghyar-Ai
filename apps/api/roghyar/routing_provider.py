"""Private routing wire contract for an explicitly approved HTTPS gateway."""
import asyncio
from collections.abc import Callable
from datetime import timedelta
from ipaddress import ip_address
import json
from typing import Annotated, Literal
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from pydantic import AwareDatetime, Field, SecretStr, ValidationError

from .directory_governance import Key, reject_duplicate_keys
from .hospital_search import SearchModel

MAX_RESPONSE = 96_000
TravelMode = Literal["driving", "walking"]


class Point(SearchModel):
    latitude: Annotated[float, Field(ge=-90, le=90, allow_inf_nan=False, repr=False)]
    longitude: Annotated[float, Field(ge=-180, le=180, allow_inf_nan=False, repr=False)]


class ProviderReply(SearchModel):
    version: Literal[1]
    request_id: UUID
    status: Literal["route", "no_route"]
    mode: TravelMode
    origin: Point = Field(repr=False)
    destination: Point = Field(repr=False)
    observed_at: AwareDatetime
    expires_at: AwareDatetime
    route_id: Key | None = None
    meters: Annotated[float, Field(ge=0, le=5_000_000, allow_inf_nan=False)] | None = None
    geometry: Annotated[tuple[Point, ...], Field(min_length=2, max_length=1024)] | None = Field(default=None, repr=False)
    duration_seconds: Annotated[float, Field(gt=0, le=172_800, allow_inf_nan=False)] | None = None
    eta_expires_at: AwareDatetime | None = None
    traffic: bool | None = None


class ProviderDenied(ValueError):
    """Only a stable machine status crosses the transport boundary."""


class GatewayPolicy(SearchModel):
    provider: Key
    endpoint: str
    modes: frozenset[TravelMode]
    external_disclosure_approved: Literal[True]
    secret: SecretStr | None = Field(default=None, repr=False)


def checked_endpoint(raw: str, approved_hosts: frozenset[str], *, path: str = "/v1/route") -> str:
    if not raw.isascii() or any(ord(c) < 33 for c in raw) or "\\" in raw:
        raise ValueError("Unapproved routing endpoint")
    url = urlsplit(raw)
    # Use an approved public address literal so DNS cannot redirect the connection.
    # The gateway certificate must validate for this address.
    address = ip_address(url.hostname or "")
    if (url.scheme != "https" or url.port not in (None, 443) or url.username is not None
            or url.password is not None or url.query or url.fragment or url.path != path
            or address.version != 4 or not address.is_global or address.is_multicast or address.is_reserved
            or str(address) not in approved_hosts):
        raise ValueError("Unapproved routing endpoint")
    return raw


class RoutingGateway:
    def __init__(self, policy: GatewayPolicy, *, approved_hosts: frozenset[str]):
        policy = GatewayPolicy.model_validate(policy.model_dump())
        self.endpoint = checked_endpoint(policy.endpoint, approved_hosts)
        self.provider = policy.provider
        self.modes = policy.modes
        self.secret = policy.secret

    def client(self, timeout: float):
        return httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=min(timeout, 2.0)), trust_env=False,
                            follow_redirects=False)

    def route(self, origin: Point, destination: Point, mode: TravelMode, request_id: UUID,
              timeout: float, *, cancelled: Callable[[], bool] | None = None) -> ProviderReply:
        return asyncio.run(self.fetch_until_cancelled(origin, destination, mode, request_id, timeout, cancelled))

    async def fetch_until_cancelled(self, origin, destination, mode, request_id, timeout, cancelled):
        if cancelled is None:
            return await self.fetch(origin, destination, mode, request_id, timeout)
        if cancelled():
            raise ProviderDenied("request_cancelled")
        pending = asyncio.create_task(self.fetch(origin, destination, mode, request_id, timeout))
        try:
            while not pending.done():
                await asyncio.wait({pending}, timeout=0.025)
                if not pending.done() and cancelled():
                    raise ProviderDenied("request_cancelled")
            return pending.result()
        finally:
            if not pending.done():
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)

    async def fetch(self, origin, destination, mode, request_id, timeout):
        headers = {"Accept": "application/json", "Accept-Encoding": "identity", "Cache-Control": "no-store"}
        if self.secret is not None:
            headers["Authorization"] = "Bearer " + self.secret.get_secret_value()
        try:
            async with asyncio.timeout(timeout), self.client(timeout) as client, client.stream("POST", self.endpoint, headers=headers, json={
                "version": 1, "request_id": str(request_id), "mode": mode,
                "origin": origin.model_dump(), "destination": destination.model_dump(),
            }) as response:
                if response.status_code != 200:
                    raise ProviderDenied("provider_unavailable")
                if (response.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json"
                        or response.headers.get("content-encoding", "identity").lower() != "identity"):
                    raise ProviderDenied("invalid_provider_response")
                declared = response.headers.get("content-length")
                if declared is not None and (not declared.isdecimal() or len(declared) > 8
                                             or int(declared) > MAX_RESPONSE):
                    raise ProviderDenied("invalid_provider_response")
                payload = bytearray()
                async for chunk in response.aiter_raw():
                    if len(payload) + len(chunk) > MAX_RESPONSE:
                        raise ProviderDenied("invalid_provider_response")
                    payload.extend(chunk)
        except (TimeoutError, httpx.TimeoutException):
            raise ProviderDenied("provider_timeout") from None
        except httpx.HTTPError:
            raise ProviderDenied("provider_unavailable") from None
        try:
            json.loads(payload, object_pairs_hook=reject_duplicate_keys)
            return ProviderReply.model_validate_json(bytes(payload))
        except (ValidationError, ValueError, RecursionError):
            raise ProviderDenied("invalid_provider_response") from None


MAX_ROUTE_AGE = timedelta(minutes=5)
