"""Private, read-only hospital proximity search. No HTTP or location disclosure path."""
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError, field_validator
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError, SQLAlchemyError

from .scoped_session import AuthorizationDenied, CredentialVerifier, scoped_session

RADII_KM = (5, 15, 30, 60)
Radius = Literal[5, 15, 30, 60]
Identifier = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,63}$")]


class SearchModel(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid", hide_input_in_errors=True)


class HospitalSearchQuery(SearchModel):
    latitude: Annotated[float, Field(ge=-90, le=90, allow_inf_nan=False, repr=False)]
    longitude: Annotated[float, Field(ge=-180, le=180, allow_inf_nan=False, repr=False)]
    radius_km: Radius | None = None
    emergency_24_7: bool | None = None
    type: Identifier | None = None
    open_now: bool | None = None
    specialty: Identifier | None = None
    sort: Literal["nearest", "emergency_first", "specialty_match"] = "nearest"
    limit: Annotated[int, Field(ge=1, le=50)] = 20

    @field_validator("radius_km", mode="before")
    @classmethod
    def require_integer_radius(cls, value):
        if value is not None and type(value) is not int:
            raise ValueError("Radius must be an integer")
        return value


class HospitalMatch(SearchModel):
    id: UUID
    name: str
    address: str
    city: str
    district: str
    province: str
    distance_km: Annotated[float, Field(ge=0, allow_inf_nan=False)]
    is_emergency_24_7: bool
    verified: Literal[True]
    published: Literal[True]
    verified_on: datetime


class HospitalSearchResult(SearchModel):
    status: Literal["found", "no_matches", "directory_unavailable"]
    radius_km: Radius
    expanded: bool
    qualifying_count: Annotated[int, Field(ge=0)]
    hospitals: tuple[HospitalMatch, ...] = ()


class HospitalSearchFailure(SearchModel):
    status: Literal["invalid_query", "unsupported_filter", "database_unavailable", "query_timeout", "untrusted_connection"]
    unsupported_filters: tuple[Literal["type", "open_now", "specialty", "specialty_match"], ...] = ()


NEARBY = text("""
    SELECT h.id, h.name, h.address, h.city, h.district, h.province, h.verified,
           h.published, h.verified_on, h.is_emergency_24_7,
           ST_Distance(h.location, point.origin, true) / 1000.0 AS distance_km,
           count(*) OVER () AS qualifying_count
    FROM roghyar.hospitals h JOIN roghyar.directory_current c ON c.hospital_id=h.id
      AND c.expires_at > statement_timestamp()
    CROSS JOIN (SELECT ST_SetSRID(ST_MakePoint(:longitude, :latitude), 4326)::geography AS origin) point
    WHERE h.verified AND h.published AND h.verified_on IS NOT NULL
      AND (CAST(:emergency AS boolean) IS NULL OR h.is_emergency_24_7 = :emergency)
      AND ST_DWithin(h.location, point.origin, :radius_m, true)
    ORDER BY CASE WHEN :emergency_first THEN h.is_emergency_24_7 ELSE false END DESC, distance_km, h.id
    LIMIT :limit
""")
HAS_DIRECTORY = text("""
    SELECT EXISTS (
        SELECT 1 FROM roghyar.hospitals h JOIN roghyar.directory_current c ON c.hospital_id=h.id
        WHERE h.verified AND h.published AND h.verified_on IS NOT NULL AND c.expires_at > statement_timestamp()
    )
""")


class HospitalSearch:
    """Search the public eligibility scope through the dedicated runtime login."""
    def __init__(self, runtime_engine: Engine):
        if not runtime_engine.hide_parameters or runtime_engine.echo:
            raise ValueError("Hospital search requires a non-echoing engine with hidden parameters")
        self._engine = runtime_engine.execution_options(isolation_level="REPEATABLE READ")

    def search(self, query: object) -> HospitalSearchResult | HospitalSearchFailure:
        try:
            query = HospitalSearchQuery.model_validate(
                query.model_dump() if isinstance(query, HospitalSearchQuery) else query)
        except ValidationError:
            return HospitalSearchFailure(status="invalid_query")

        unsupported = tuple(key for key in ("type", "open_now", "specialty") if getattr(query, key) is not None)
        if query.sort == "specialty_match":
            unsupported += ("specialty_match",)
        if unsupported:
            return HospitalSearchFailure(status="unsupported_filter", unsupported_filters=unsupported)

        radii = (query.radius_km,) if query.radius_km is not None else RADII_KM
        try:
            # Keep all directory reads in one read-only anonymous snapshot.
            with scoped_session(self._engine, CredentialVerifier(None), None) as session:
                session.execute(text("SET LOCAL statement_timeout = '1500ms'"))
                for radius in radii:
                    rows = session.execute(NEARBY, {
                        "longitude": query.longitude, "latitude": query.latitude, "radius_m": radius * 1000,
                        "emergency": query.emergency_24_7, "emergency_first": query.sort == "emergency_first",
                        "limit": query.limit,
                    }).mappings().all()
                    if rows:
                        return HospitalSearchResult(
                            status="found", radius_km=radius, expanded=radius != radii[0],
                            qualifying_count=rows[0]["qualifying_count"],
                            hospitals=tuple(HospitalMatch.model_validate(
                                {key: value for key, value in row.items() if key != "qualifying_count"}) for row in rows),
                        )
                directory = session.scalar(HAS_DIRECTORY)
                return HospitalSearchResult(
                    status="no_matches" if directory else "directory_unavailable",
                    radius_km=radii[-1], expanded=len(radii) > 1, qualifying_count=0,
                )
        except AuthorizationDenied:
            return HospitalSearchFailure(status="untrusted_connection")
        except DBAPIError as exc:
            reason = "query_timeout" if getattr(exc.orig, "sqlstate", None) == "57014" else "database_unavailable"
            return HospitalSearchFailure(status=reason)
        except (SQLAlchemyError, ValidationError):
            return HospitalSearchFailure(status="database_unavailable")
