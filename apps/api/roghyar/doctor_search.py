"""Private, read-only doctor discovery and proportional occupancy checks."""
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, ValidationError, model_validator
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError, SQLAlchemyError

from .directory_governance import Key, fingerprint
from .doctor_schedules import ZONE, ProvisionalSlot, Schedule, local_window, provisional_slots
from .doctor_specialties import SpecialtyVocabulary
from .hospital_search import SearchModel
from .red_flags import canonical
from .scoped_session import AuthorizationDenied, CredentialVerifier, scoped_session


class DoctorSearchQuery(SearchModel):
    specialty: Key | None = None
    doctor_id: UUID | None = None
    hospital_id: UUID | None = None
    city: Annotated[str, Field(min_length=1, max_length=120, pattern=r"^[^\x00-\x1f\x7f]+$")] | None = None
    latitude: Annotated[float, Field(ge=-90, le=90, allow_inf_nan=False, repr=False)] | None = None
    longitude: Annotated[float, Field(ge=-180, le=180, allow_inf_nan=False, repr=False)] | None = None
    limit: Annotated[int, Field(ge=1, le=25)] = 10

    @model_validator(mode="after")
    def paired_location(self):
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("Both coordinates are required")
        return self


class SlotQuery(SearchModel):
    doctor_id: UUID
    hospital_id: UUID
    day: date


class DoctorMatch(SearchModel):
    doctor_id: UUID
    hospital_id: UUID
    name: str
    specialties: tuple[str, ...]
    qualifications: tuple[str, ...]
    reviewed_at: datetime


class DoctorResult(SearchModel):
    status: Literal["found", "no_matches", "invalid_query", "unknown_specialty", "taxonomy_unavailable",
                    "untrusted_connection", "query_timeout", "database_unavailable"]
    doctors: tuple[DoctorMatch, ...] = ()


class ScheduleResult(SearchModel):
    status: Literal["provisional", "no_slots", "no_schedule", "ineligible", "invalid_query", "out_of_window",
                    "taxonomy_unavailable", "untrusted_connection", "query_timeout", "database_unavailable"]
    day: date | None = None
    doctor_id: UUID | None = None
    hospital_id: UUID | None = None
    timezone: Literal["Asia/Karachi"] = ZONE
    revision: int | None = None
    expires_at: datetime | None = None
    slots: tuple[ProvisionalSlot, ...] = ()


ELIGIBLE = """
    FROM roghyar.doctor_current c JOIN roghyar.doctors d ON d.id=c.doctor_id AND d.hospital_id=c.hospital_id
    JOIN roghyar.directory_current hc ON hc.hospital_id=c.hospital_id AND hc.revision=c.hospital_revision
    JOIN roghyar.hospitals h ON h.id=hc.hospital_id
    WHERE d.verified AND d.published AND h.verified AND h.published
      AND c.expires_at>:now AND hc.expires_at>:now
      AND c.taxonomy_version=:version AND c.taxonomy_digest=:digest
"""
DISCOVERY = text("""
    SELECT c.doctor_id,c.hospital_id,d.full_name AS name,c.specialties,d.qualifications,c.reviewed_at
""" + ELIGIBLE + """
      AND (CAST(:doctor_id AS uuid) IS NULL OR c.doctor_id=:doctor_id)
      AND (CAST(:hospital_id AS uuid) IS NULL OR c.hospital_id=:hospital_id)
      AND (CAST(:specialty AS text) IS NULL OR c.specialties @> ARRAY[CAST(:specialty AS text)])
      AND (CAST(:city AS text) IS NULL OR roghyar.hospital_search_key(h.city)=roghyar.hospital_search_key(:city))
      AND (CAST(:latitude AS double precision) IS NULL OR ST_DWithin(h.location,
           ST_SetSRID(ST_MakePoint(:longitude,:latitude),4326)::geography,60000,true))
    ORDER BY lower(d.full_name),c.doctor_id
    LIMIT :limit
""")
SCHEDULE = text("SELECT c.revision,c.schedule,least(c.expires_at,hc.expires_at) AS expires_at " + ELIGIBLE +
                " AND c.doctor_id=:doctor_id AND c.hospital_id=:hospital_id")
FREE = text("""
    SELECT ordinal FROM unnest(CAST(:starts AS timestamptz[]),CAST(:ends AS timestamptz[]))
       WITH ORDINALITY AS slot(starts,ends,ordinal)
    WHERE NOT roghyar.doctor_slot_occupied(:doctor_id,:hospital_id,starts,ends)
    ORDER BY ordinal
""")


class DoctorSearch:
    def __init__(self, engine: Engine, *, vocabulary: SpecialtyVocabulary | None = None,
                 clock=lambda: datetime.now(UTC), lead_time=timedelta(minutes=15)):
        if engine.echo or not engine.hide_parameters or not timedelta(0) <= lead_time <= timedelta(hours=24):
            raise ValueError("Unsafe doctor search configuration")
        self.engine = engine.execution_options(isolation_level="REPEATABLE READ")
        self.vocabulary = vocabulary
        self.clock = clock
        self.lead_time = lead_time

    def taxonomy(self, now):
        vocabulary = self.vocabulary
        if vocabulary is None or not vocabulary.active(now):
            return None
        return {"version": vocabulary.version, "digest": fingerprint(vocabulary.model_dump(mode="json")), "now": now}

    @staticmethod
    def error(exc):
        if isinstance(exc, AuthorizationDenied):
            return "untrusted_connection"
        if isinstance(exc, DBAPIError) and getattr(exc.orig, "sqlstate", None) == "57014":
            return "query_timeout"
        return "database_unavailable"

    def search(self, query: object) -> DoctorResult:
        try:
            query = DoctorSearchQuery.model_validate(query.model_dump() if isinstance(query, DoctorSearchQuery) else query)
        except ValidationError:
            return DoctorResult(status="invalid_query")
        now = self.clock()
        taxonomy = self.taxonomy(now)
        if taxonomy is None:
            return DoctorResult(status="taxonomy_unavailable")
        specialty = None if query.specialty is None else self.vocabulary.resolve(query.specialty)
        if query.specialty is not None and specialty is None:
            return DoctorResult(status="unknown_specialty")
        try:
            with scoped_session(self.engine, CredentialVerifier(None), None) as session:
                session.execute(text("SET LOCAL statement_timeout = '1500ms'"))
                rows = session.execute(DISCOVERY, query.model_dump() | taxonomy | {"specialty": specialty}).mappings()
                matches = tuple(DoctorMatch.model_validate(dict(row) | {
                    "specialties": tuple(row["specialties"]), "qualifications": tuple(row["qualifications"] or ())
                }) for row in rows)
                return DoctorResult(status="found" if matches else "no_matches", doctors=matches)
        except (AuthorizationDenied, SQLAlchemyError, ValidationError) as exc:
            return DoctorResult(status=self.error(exc))

    def details(self, doctor_id: UUID) -> DoctorResult:
        return self.search({"doctor_id": doctor_id, "limit": 1})

    def slots(self, query: object) -> ScheduleResult:
        try:
            query = SlotQuery.model_validate(query.model_dump() if isinstance(query, SlotQuery) else query)
            now = self.clock()
            if not local_window(query.day, now):
                return ScheduleResult(status="out_of_window")
        except (ValidationError, ValueError):
            return ScheduleResult(status="invalid_query")
        taxonomy = self.taxonomy(now)
        if taxonomy is None:
            return ScheduleResult(status="taxonomy_unavailable")
        try:
            with scoped_session(self.engine, CredentialVerifier(None), None) as session:
                session.execute(text("SET LOCAL statement_timeout = '1500ms'"))
                row = session.execute(SCHEDULE, query.model_dump() | taxonomy).mappings().one_or_none()
                if row is None:
                    return ScheduleResult(status="ineligible")
                schedule = Schedule.model_validate_json(canonical(row["schedule"]))
                if not schedule.effective_from <= query.day <= schedule.effective_until:
                    return ScheduleResult(status="no_schedule")
                candidates = provisional_slots(schedule, query.day, now, self.lead_time)
                params = query.model_dump() | {"starts": [item.start_utc for item in candidates],
                                               "ends": [item.end_utc for item in candidates]}
                available = tuple(candidates[i - 1] for i in session.scalars(FREE, params)) if candidates else ()
                return ScheduleResult(status="provisional" if available else "no_slots", day=query.day,
                                      revision=row["revision"], expires_at=row["expires_at"], slots=available,
                                      doctor_id=query.doctor_id, hospital_id=query.hospital_id)
        except (AuthorizationDenied, SQLAlchemyError, ValidationError) as exc:
            return ScheduleResult(status=self.error(exc))
