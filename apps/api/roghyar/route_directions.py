"""Private routing orchestration, one-shot disclosure, and validated route results."""
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import BoundedSemaphore, Lock
from typing import Annotated, Literal, Protocol
from urllib.parse import urlencode, urlsplit
from uuid import UUID, uuid4

from pydantic import AwareDatetime, Field, ValidationError

from .directory_governance import Key, fingerprint
from .hospital_search import SearchModel
from .routing_destination import Destination, DestinationDenied, DestinationReader
from .routing_provider import MAX_ROUTE_AGE, Point, ProviderDenied, ProviderReply, TravelMode

MAX_FIX_AGE = timedelta(seconds=30)
MAX_ACCURACY_METERS = 250


class DestinationRequest(SearchModel):
    hospital_id: UUID
    revision: Annotated[int, Field(ge=1, le=2147483647)]
    doctor_id: UUID | None = None


class RouteRequest(SearchModel):
    origin: Point = Field(repr=False)
    origin_observed_at: AwareDatetime
    accuracy_meters: Annotated[float, Field(ge=0, le=100_000, allow_inf_nan=False)]
    destination: DestinationRequest
    mode: TravelMode
    timeout_seconds: Annotated[float, Field(ge=0.1, le=8, allow_inf_nan=False)] = 3.0


class Estimate(SearchModel):
    duration_seconds: float
    provider: Key
    observed_at: AwareDatetime
    expires_at: AwareDatetime
    mode: TravelMode
    traffic: bool | None = None
    status: Literal["estimate"] = "estimate"


class RouteResult(SearchModel):
    status: Literal["route_available", "provider_unconfigured", "provider_unavailable", "provider_timeout",
                    "no_route", "invalid_origin", "stale_origin", "invalid_destination",
                    "destination_unverified", "destination_changed", "destination_database_unavailable",
                    "affiliation_unavailable", "unsupported_mode", "consent_required", "disclosure_not_approved",
                    "routing_disabled", "invalid_provider_response", "request_cancelled", "busy"]
    provider: Key | None = None
    route_id: Key | None = None
    destination: Destination | None = None
    mode: TravelMode | None = None
    meters: float | None = None
    observed_at: AwareDatetime | None = None
    expires_at: AwareDatetime | None = None
    geometry: tuple[Point, ...] | None = Field(default=None, repr=False)
    eta_status: Literal["estimate", "eta_unavailable", "eta_stale"] = "eta_unavailable"
    eta: Estimate | None = None


class Provider(Protocol):
    provider: str
    modes: frozenset[TravelMode]

    def route(self, origin: Point, destination: Point, mode: TravelMode, request_id: UUID,
              timeout: float, *, cancelled: Callable[[], bool] | None = None) -> ProviderReply: ...


@dataclass(frozen=True)
class Consent:
    nonce: UUID
    target: str
    request_digest: str
    expires_at: datetime


class RouteSession:
    """Private per-owner foreground session, created by trusted server composition.

    UI integration must cancel it on stop, revocation, navigation or selection changes.
    Only the current authorized intent can finish, and each disclosure is used once.
    """

    def __init__(self):
        self._lock = Lock()
        self._current = None
        self._used = False

    def authorize(self, request: RouteRequest | DestinationRequest, *, recipient: str, now: datetime,
                  user_confirmed: bool):
        with self._lock:
            self._current = None
            self._used = False
            if user_confirmed is not True:
                return None
            self._current = Consent(uuid4(), recipient, fingerprint(request.model_dump(mode="json")),
                                    now + MAX_FIX_AGE)
            return self._current

    def claim(self, grant, request, recipient, now):
        with self._lock:
            if (grant is None or self._current is not grant or self._used or grant.target != recipient
                    or now >= grant.expires_at or grant.request_digest != fingerprint(request.model_dump(mode="json"))):
                return False
            self._used = True
            return True

    def current(self, grant, now):
        with self._lock:
            return self._current is grant and now < grant.expires_at

    def abandon(self, grant):
        with self._lock:
            if self._current is grant:
                self._current = None
                self._used = False

    def cancel(self):
        with self._lock:
            self._current = None
            self._used = False


def bound_reply(reply, request, destination, request_id, now):
    if ((reply.request_id, reply.mode, reply.origin, reply.destination) !=
            (request_id, request.mode, request.origin, destination.point)
            or not now - MAX_ROUTE_AGE <= reply.observed_at <= now < reply.expires_at
            or reply.expires_at - reply.observed_at > MAX_ROUTE_AGE):
        raise ProviderDenied("invalid_provider_response")
    if reply.status == "no_route":
        if any(value is not None for value in (reply.meters, reply.geometry, reply.duration_seconds,
                                               reply.eta_expires_at, reply.traffic)):
            raise ProviderDenied("invalid_provider_response")
        return
    if (reply.meters is None
            or reply.geometry is not None and (reply.geometry[0], reply.geometry[-1]) !=
               (request.origin, destination.point)
            or (reply.duration_seconds is None) != (reply.eta_expires_at is None)
            or reply.duration_seconds is None and reply.traffic is not None
            or reply.eta_expires_at is not None and
               not reply.observed_at < reply.eta_expires_at <= reply.expires_at):
        raise ProviderDenied("invalid_provider_response")


class Routing:
    def __init__(self, destinations: DestinationReader, *, provider: Provider | None = None,
                 enabled: bool = False, disclosure_approved: bool = False, clock=lambda: datetime.now(UTC)):
        self.destinations = destinations
        self.provider = provider
        self.enabled = enabled
        self.disclosure_approved = disclosure_approved
        self.clock = clock
        self._requests = BoundedSemaphore(4)

    def resolve(self, request, now):
        return self.destinations.resolve(request.hospital_id, request.revision, request.doctor_id, now)

    def route(self, query, session: RouteSession, grant: Consent | None, *, online=True):
        try:
            request = RouteRequest.model_validate(query.model_dump() if isinstance(query, RouteRequest) else query)
        except ValidationError as exc:
            fields = {error["loc"][0] if error["loc"] else None for error in exc.errors(include_input=False)}
            status = ("invalid_destination" if fields == {"destination"} else
                      "unsupported_mode" if fields == {"mode"} else "invalid_origin")
            return RouteResult(status=status)
        now = self.clock()
        if (request.accuracy_meters > MAX_ACCURACY_METERS or request.origin_observed_at > now):
            return RouteResult(status="invalid_origin")
        if now - request.origin_observed_at >= MAX_FIX_AGE:
            return RouteResult(status="stale_origin")
        if self.provider is None:
            return RouteResult(status="provider_unconfigured")
        if not self.enabled:
            return RouteResult(status="routing_disabled")
        if not self.disclosure_approved:
            return RouteResult(status="disclosure_not_approved")
        if not online:
            session.abandon(grant)
            return RouteResult(status="provider_unavailable")
        if request.mode not in self.provider.modes:
            return RouteResult(status="unsupported_mode")
        if not self._requests.acquire(blocking=False):
            return RouteResult(status="busy")
        try:
            if not session.claim(grant, request, "route:" + self.provider.provider, now):
                return RouteResult(status="consent_required")
            destination = self.resolve(request.destination, now)
            if not session.current(grant, self.clock()) or self.clock() - request.origin_observed_at >= MAX_FIX_AGE:
                return RouteResult(status="request_cancelled")
            request_id = uuid4()
            reply = self.provider.route(request.origin, destination.point, request.mode, request_id,
                                        request.timeout_seconds,
                                        cancelled=lambda: not session.current(grant, self.clock()))
            reply = ProviderReply.model_validate(reply.model_dump())
            now = self.clock()
            if not session.current(grant, now) or now - request.origin_observed_at >= MAX_FIX_AGE:
                return RouteResult(status="request_cancelled")
            if self.resolve(request.destination, now) != destination:
                return RouteResult(status="destination_changed")
            now = self.clock()
            if not session.current(grant, now):
                return RouteResult(status="request_cancelled")
            bound_reply(reply, request, destination, request_id, now)
            if reply.status == "no_route":
                return RouteResult(status="no_route")
            expires = min(reply.expires_at, destination.expires_at, grant.expires_at,
                          request.origin_observed_at + MAX_FIX_AGE)
            if now >= expires:
                return RouteResult(status="request_cancelled")
            eta = None
            eta_status = "eta_unavailable"
            if reply.eta_expires_at is not None:
                if reply.eta_expires_at <= now:
                    eta_status = "eta_stale"
                else:
                    eta_status = "estimate"
                    eta = Estimate(duration_seconds=reply.duration_seconds, provider=self.provider.provider,
                                   observed_at=reply.observed_at, expires_at=min(reply.eta_expires_at, expires),
                                   mode=request.mode, traffic=reply.traffic)
            return RouteResult(status="route_available", provider=self.provider.provider, destination=destination,
                               mode=request.mode, route_id=reply.route_id, meters=reply.meters, geometry=reply.geometry,
                               observed_at=reply.observed_at, expires_at=expires, eta_status=eta_status, eta=eta)
        except DestinationDenied as exc:
            return RouteResult(status=str(exc))
        except ProviderDenied as exc:
            return RouteResult(status=str(exc))
        except (ValidationError, AttributeError, TypeError):
            return RouteResult(status="invalid_provider_response")
        finally:
            self._requests.release()


class MapHandoff(SearchModel):
    status: Literal["handoff_ready", "map_unconfigured", "consent_required", "destination_unverified",
                    "destination_changed", "destination_database_unavailable", "affiliation_unavailable"]
    recipient: Key | None = None
    url: str | None = Field(default=None, repr=False)


class ExternalMaps:
    """Build a destination-only link against an independently approved exact template."""
    def __init__(self, reader, *, provider: str | None = None, template: str | None = None,
                 approved_templates: frozenset[str] = frozenset(), clock=lambda: datetime.now(UTC)):
        if template is not None:
            target = urlsplit(template)
            if (not template.isascii() or any(ord(c) < 33 for c in template) or "\\" in template
                    or template not in approved_templates or template != target.geturl() or target.scheme != "https"
                    or not target.hostname or target.username is not None or target.password is not None
                    or target.query or target.fragment or target.port not in (443, None) or not provider):
                raise ValueError("Unapproved external map template")
        self.reader, self.provider, self.template, self.clock = reader, provider, template, clock

    def prepare(self, request: DestinationRequest, session: RouteSession, grant: Consent | None):
        if self.template is None:
            return MapHandoff(status="map_unconfigured")
        now = self.clock()
        if not session.claim(grant, request, "map:" + self.provider, now):
            return MapHandoff(status="consent_required")
        try:
            destination = self.reader.resolve(request.hospital_id, request.revision, request.doctor_id, now)
        except DestinationDenied as exc:
            return MapHandoff(status=str(exc))
        if not session.current(grant, self.clock()):
            return MapHandoff(status="consent_required")
        point = destination.point
        return MapHandoff(status="handoff_ready", recipient=self.provider,
                          url=self.template + "?" + urlencode({"destination": f"{point.latitude},{point.longitude}"}))
