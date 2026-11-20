"""Private booking calendar. A calendar snapshot cannot reserve a slot."""
from datetime import date, datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, ValidationError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from .directory_governance import fingerprint
from .doctor_schedules import LOCAL, ZONE, ProvisionalSlot, Schedule, SessionHours, local_window, provisional_slots
from .doctor_search import ELIGIBLE, FREE, DoctorSearch
from .hospital_search import SearchModel
from .red_flags import canonical
from .schedule_confirmation import Confirmation, ConfirmationDenied, TrustRegistry, authenticate
from .scoped_session import AuthorizationDenied, CredentialVerifier, scoped_session


class CalendarQuery(SearchModel):
    doctor_id: UUID
    hospital_id: UUID
    start: date
    days: Annotated[int, Field(ge=1, le=31)] = 1


class BookingSlot(ProvisionalSlot):
    status: Literal["provisional_unoccupied", "occupied"] = "provisional_unoccupied"


class CalendarDay(SearchModel):
    day: date
    status: Literal["provisional", "occupied", "schedule_unconfirmed", "schedule_expired", "schedule_withdrawn",
                    "provider_unavailable", "closed_by_exception", "no_working_session", "no_complete_slots"]
    confirmation_revision: int | None = None
    deadline: datetime | None = None
    sessions: tuple[SessionHours, ...] = ()
    slots: tuple[BookingSlot, ...] = ()


class CalendarResult(SearchModel):
    status: Literal["calendar", "invalid_query", "outside_booking_window", "ineligible", "query_timeout",
                    "database_unavailable", "provider_unavailable", "snapshot_expired"]
    timezone: Literal["Asia/Karachi"] = ZONE
    doctor_id: UUID | None = None
    hospital_id: UUID | None = None
    doctor_revision: int | None = None
    hospital_revision: int | None = None
    schedule_digest: str | None = None
    observed_at: datetime | None = None
    days: tuple[CalendarDay, ...] = ()


CURRENT = text("SELECT c.revision,c.hospital_revision,c.schedule,least(c.expires_at,hc.expires_at) AS expires_at "
               + ELIGIBLE + " AND c.doctor_id=:doctor_id AND c.hospital_id=:hospital_id")
CONFIRMATIONS = text("""
    SELECT DISTINCT ON(day) day,sequence,doctor_revision,hospital_revision,proof
    FROM roghyar.slot_confirmations
    WHERE doctor_id=:doctor_id AND hospital_id=:hospital_id AND day BETWEEN :first AND :last
    ORDER BY day,sequence DESC
""")


def working_sessions(schedule, day):
    return tuple(SessionHours(start=row.start, end=row.end, minutes=row.minutes)
                 for row in schedule.sessions_for(day))


def confirmation_for(schedule, digest, current, scope, day, row, trust, now):
    exception = next((item for item in schedule.exceptions if item.start_date <= day <= item.end_date), None)
    sessions = working_sessions(schedule, day)
    if exception is not None and not sessions:
        return CalendarDay(day=day, status="closed_by_exception"), ()
    if row is None:
        if not schedule.effective_from <= day <= schedule.effective_until:
            return CalendarDay(day=day, status="schedule_expired"), ()
        return CalendarDay(day=day, status="schedule_unconfirmed" if sessions else "no_working_session"), ()
    try:
        proof = Confirmation.model_validate_json(row.proof)
        if (proof.doctor_id != scope["doctor_id"] or proof.hospital_id != scope["hospital_id"]
                or proof.day != day or proof.sequence != row.sequence
                or proof.doctor_revision != row.doctor_revision or proof.hospital_revision != row.hospital_revision):
            return CalendarDay(day=day, status="schedule_unconfirmed"), ()
        authority_expiry = authenticate(proof, trust, now)
    except (ValidationError, ConfirmationDenied):
        return CalendarDay(day=day, status="schedule_unconfirmed"), ()
    if proof.state == "withdrawn" and now >= proof.effective_at:
        return CalendarDay(day=day, status="schedule_withdrawn"), ()
    if (proof.doctor_revision != current.revision or proof.hospital_revision != current.hospital_revision
            or proof.schedule_digest != digest or now < proof.effective_at):
        return CalendarDay(day=day, status="schedule_unconfirmed"), ()
    if not schedule.effective_from <= day <= schedule.effective_until or now >= proof.expires_at:
        return CalendarDay(day=day, status="schedule_expired"), ()
    if proof.state == "unavailable":
        return CalendarDay(day=day, status="provider_unavailable"), ()
    if proof.state in ("closure", "leave", "holiday"):
        return CalendarDay(day=day, status="closed_by_exception"), ()
    expected = "replacement" if exception is not None else "working"
    if proof.state != expected or proof.sessions != sessions:
        return CalendarDay(day=day, status="schedule_unconfirmed"), ()
    deadline = min(current.expires_at, authority_expiry, proof.expires_at)
    return CalendarDay(day=day, status="provisional", confirmation_revision=proof.sequence,
                       deadline=deadline, sessions=sessions), sessions


class BookingCalendar:
    def __init__(self, schedules: DoctorSearch, trust: TrustRegistry):
        self.schedules, self.trust = schedules, trust

    def calendar(self, query: object) -> CalendarResult:
        now = self.schedules.clock()
        try:
            query = CalendarQuery.model_validate(query.model_dump() if isinstance(query, CalendarQuery) else query)
            last = query.start + timedelta(days=query.days - 1)
            if not local_window(query.start, now) or not local_window(last, now):
                return CalendarResult(status="outside_booking_window")
        except (ValidationError, ValueError, OverflowError):
            return CalendarResult(status="invalid_query")
        taxonomy = self.schedules.taxonomy(now)
        if taxonomy is None:
            return CalendarResult(status="ineligible")
        try:
            trust = self.trust.snapshot()
        except Exception:
            return CalendarResult(status="provider_unavailable")
        scope = {"doctor_id": query.doctor_id, "hospital_id": query.hospital_id}
        try:
            with scoped_session(self.schedules.engine, CredentialVerifier(None), None) as session:
                session.execute(text("SET LOCAL statement_timeout = '1500ms'"))
                current = session.execute(CURRENT, scope | taxonomy).one_or_none()
                if current is None:
                    return CalendarResult(status="ineligible")
                schedule = Schedule.model_validate_json(canonical(current.schedule))
                digest = fingerprint(schedule.model_dump(mode="json"))
                proofs = {row.day: row for row in session.execute(
                    CONFIRMATIONS, scope | {"first": query.start, "last": last})}
                days, slots = [], []
                for offset in range(query.days):
                    day = query.start + timedelta(days=offset)
                    result, sessions = confirmation_for(schedule, digest, current, scope, day, proofs.get(day),
                                                        trust, now)
                    candidates = provisional_slots(schedule, day, now, self.schedules.lead_time) if sessions else ()
                    days.append((result, len(slots), len(candidates)))
                    slots.extend(candidates)
                # One bounded batch of intervals. Only ordinals cross the appointment privacy boundary.
                params = scope | {"starts": [slot.start_utc for slot in slots], "ends": [slot.end_utc for slot in slots]}
                free = set(session.scalars(FREE, params)) if slots else set()
                results = []
                for result, start, count in days:
                    if result.status == "provisional":
                        intervals = tuple(BookingSlot(**(slots[i].model_dump() | {
                            "status": "provisional_unoccupied" if i + 1 in free else "occupied"
                        })) for i in range(start, start + count))
                        status = ("no_complete_slots" if not intervals else
                                  "provisional" if any(i + 1 in free for i in range(start, start + count)) else "occupied")
                        result = result.model_copy(update={"status": status, "slots": intervals})
                    results.append(result)
            done = self.schedules.clock()
            if (done.astimezone(LOCAL).date() != now.astimezone(LOCAL).date() or done >= current.expires_at
                    or any(day.deadline is not None and done >= day.deadline for day in results)
                    or any(slot.start_utc <= done or slot.start_utc < done + self.schedules.lead_time
                           for day in results for slot in day.slots)):
                return CalendarResult(status="snapshot_expired")
            # Recheck trust before release. A revoked artifact is never served on a stale trust snapshot.
            try:
                if trust != self.trust.snapshot():
                    return CalendarResult(status="provider_unavailable")
            except Exception:
                return CalendarResult(status="provider_unavailable")
            return CalendarResult(status="calendar", **scope, observed_at=now, days=tuple(results),
                                  doctor_revision=current.revision, hospital_revision=current.hospital_revision,
                                  schedule_digest=digest)
        except (AuthorizationDenied, SQLAlchemyError, ValidationError) as exc:
            return CalendarResult(status="query_timeout" if DoctorSearch.error(exc) == "query_timeout"
                                  else "database_unavailable")
