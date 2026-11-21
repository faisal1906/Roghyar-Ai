"""Private My Appointments and atomic patient lifecycle actions."""
from datetime import timedelta
from hashlib import sha256
import hmac
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, ValidationError, model_validator
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError, SQLAlchemyError

from .booking_reservations import AppointmentBooking, BookingDenied, BookingRequest, Key, constraint
from .directory_governance import fingerprint
from .doctor_schedules import LOCAL
from .hospital_search import SearchModel
from .otp_service import OTPDenied
from .red_flags import canonical
from .scoped_session import AuthorizationDenied, Principal, _scoped_transaction

Status = Literal["pending", "confirmed", "cancelled", "rescheduled", "completed", "no_show"]
ACTIVE = frozenset(("pending", "confirmed"))
APPOINTMENT_COLUMNS = ("id,booking_code,doctor_id,hospital_id,slot_start,slot_end,status,created_at,status_changed_at")
OWNED = text(f"SELECT {APPOINTMENT_COLUMNS},patient_id,consent_at FROM roghyar.appointments WHERE id=:id")
LOCKED = text(OWNED.text + " FOR UPDATE")


class LifecycleDenied(ValueError):
    pass


class SlotTarget(SearchModel):
    doctor_id: UUID
    slot_start: AwareDatetime
    reason: None = None


class Mutation(SearchModel):
    action: Literal["cancel", "reschedule"]
    new_slot_start: AwareDatetime | None = None

    @model_validator(mode="after")
    def validate_target(self):
        if self.action == "cancel":
            if "new_slot_start" in self.model_fields_set:
                raise ValueError("Cancellation does not accept a slot")
        elif self.new_slot_start is None:
            raise ValueError("A target slot is required")
        else:
            object.__setattr__(self, "new_slot_start", BookingRequest.minute_boundary(self.new_slot_start))
        return self


class MutationInput(SearchModel):
    appointment_id: UUID
    key: Key = Field(repr=False)


class HistoryQuery(SearchModel):
    limit: Annotated[int, Field(ge=1, le=50)] = 20
    before_start: AwareDatetime | None = None
    before_id: UUID | None = None

    @model_validator(mode="after")
    def paired_cursor(self):
        if (self.before_id is None) != (self.before_start is None):
            raise ValueError("Incomplete page cursor")
        return self


class AppointmentView(SearchModel):
    id: UUID
    booking_code: str
    doctor_id: UUID
    hospital_id: UUID
    status: Status
    slot_start: AwareDatetime
    slot_end: AwareDatetime
    local_start: AwareDatetime
    local_end: AwareDatetime
    timezone: Literal["Asia/Karachi"] = "Asia/Karachi"
    created_at: AwareDatetime
    status_changed_at: AwareDatetime
    predecessor_id: UUID | None = None
    successor_id: UUID | None = None
    provider_eligibility: Literal["not_evaluated"] = "not_evaluated"
    notification_status: Literal["unavailable"] = "unavailable"
    provider_ack_status: Literal["unavailable"] = "unavailable"


class History(SearchModel):
    appointments: tuple[AppointmentView, ...]
    before_start: AwareDatetime | None = None
    before_id: UUID | None = None


class MutationResult(SearchModel):
    action: Literal["cancel", "reschedule"]
    appointment: AppointmentView
    successor: AppointmentView | None = None


def view(row, *, predecessor=None, successor=None):
    values = {name: getattr(row, name) for name in APPOINTMENT_COLUMNS.split(",")}
    return AppointmentView(**values, predecessor_id=predecessor, successor_id=successor,
                           local_start=row.slot_start.astimezone(LOCAL), local_end=row.slot_end.astimezone(LOCAL))


class AppointmentLifecycle:
    def __init__(self, engine, booking: AppointmentBooking, *, idempotency_key: bytes,
                 cutoff=timedelta(minutes=30), clock=None):
        otp = booking.otp
        if (engine.echo or not engine.hide_parameters or len(idempotency_key) != 32
                or idempotency_key in (booking.digest_key, otp.otp_key, otp.signing_key, otp.encryption._lookup_key,
                                       otp.encryption._encryption_key)
                or not timedelta(0) <= cutoff <= timedelta(days=7)):
            raise ValueError("Unsafe lifecycle configuration")
        self.engine = engine.execution_options(isolation_level="READ COMMITTED")
        self.booking, self.otp, self.cutoff = booking, otp, cutoff
        self.clock = clock or booking.schedules.clock
        self.key = idempotency_key

    def credential(self, token):
        try:
            return self.otp.decode_credential(token, "appointments")
        except OTPDenied:
            raise LifecycleDenied("unauthenticated") from None

    def authenticate(self, db, credential):
        identity, issued, expires = credential
        db.execute(text("SET LOCAL statement_timeout='2500ms'"))
        db.execute(text("SET LOCAL lock_timeout='1800ms'"))
        db.execute(text("""
            SELECT set_config('roghyar.management_session', :id, true),
                   set_config('roghyar.management_proof', :proof, true)
        """), {"id": str(identity.session), "proof": self.otp.management_proof(identity.session)})
        row = db.execute(text("SELECT * FROM roghyar.appointment_identity()")).one_or_none()
        if (row is None or row.subject != identity.subject or row.signing_key != self.otp.signing_key_id
                or row.issued_at.timestamp() != issued or row.expires_at.timestamp() != expires):
            raise LifecycleDenied("unauthenticated")
        return row

    def mine(self, token: str, query: HistoryQuery) -> History:
        credential = self.credential(token)
        query = HistoryQuery.model_validate(query.model_dump())
        try:
            with _scoped_transaction(self.engine, Principal("anonymous", None), "roghyar_appointment_writer",
                                     readonly=False) as db:
                auth = self.authenticate(db, credential)
                # RLS scopes the implicit patient lookup. The tuple cursor is stable under status changes.
                predicate = ("AND (a.slot_start,a.id)<(:before_start,:before_id)"
                             if query.before_start is not None else "")
                columns = ",".join("a." + name for name in APPOINTMENT_COLUMNS.split(","))
                rows = db.execute(text(f"""
                    SELECT {columns},f.successor_id,p.appointment_id AS predecessor_id
                    FROM roghyar.patients owner JOIN roghyar.appointments a ON a.patient_id=owner.id
                    LEFT JOIN roghyar.appointment_lifecycle_events f ON f.appointment_id=a.id
                    LEFT JOIN roghyar.appointment_lifecycle_events p ON p.successor_id=a.id
                    WHERE owner.auth_subject=:subject {predicate}
                    ORDER BY a.slot_start DESC,a.id DESC LIMIT :count
                """), query.model_dump() | {"count": query.limit + 1, "subject": auth.subject}).all()
                if len(rows) > query.limit:
                    boundary = rows[query.limit-1]
                    cursor = {"before_start": boundary.slot_start, "before_id": boundary.id}
                else:
                    cursor = {}
                if self.otp.clock() >= auth.expires_at:
                    raise LifecycleDenied("unauthenticated")
                return History(appointments=tuple(view(row, predecessor=row.predecessor_id, successor=row.successor_id)
                                                  for row in rows[:query.limit]), **cursor)
        except (SQLAlchemyError, AuthorizationDenied, ValidationError):
            raise LifecycleDenied("lifecycle_unavailable") from None

    def mutate(self, token: str, appointment_id: UUID, mutation: Mutation, key: str) -> MutationResult:
        try:
            parsed = MutationInput(appointment_id=appointment_id, key=key)
            mutation = Mutation.model_validate(mutation.model_dump(exclude_unset=True))
        except (ValidationError, AttributeError):
            raise LifecycleDenied("invalid_request") from None
        credential = self.credential(token)
        subject = credential[0].subject
        digest = hmac.digest(self.key, b"lifecycle:key:v1\0" + subject.bytes + parsed.key.encode("ascii"), sha256)
        input_hash = hmac.digest(self.key, b"lifecycle:request:v1\0" + parsed.appointment_id.bytes
                                 + canonical(mutation.model_dump(mode="json")), sha256)
        params = {"id": parsed.appointment_id, "subject": subject, "key": digest, "fingerprint": input_hash}
        finishing = False
        try:
            with _scoped_transaction(self.engine, Principal("anonymous", None), "roghyar_appointment_writer",
                                     readonly=False) as db:
                auth = self.authenticate(db, credential)
                db.execute(text("SELECT pg_advisory_xact_lock(:key)"),
                           {"key": int.from_bytes(digest[:8], "big", signed=True)})
                previous = db.execute(text("""
                    SELECT appointment_id,successor_id,action,fingerprint,result
                    FROM roghyar.appointment_lifecycle_events WHERE subject=:subject AND key_digest=:key
                """), params).one_or_none()
                if self.otp.clock() >= auth.expires_at:
                    raise LifecycleDenied("unauthenticated")
                if previous is not None:
                    if not hmac.compare_digest(previous.fingerprint, input_hash):
                        raise LifecycleDenied("idempotency_conflict")
                    response = MutationResult.model_validate_json(canonical(previous.result))
                    if (previous.appointment_id != response.appointment.id or previous.action != response.action
                            or previous.successor_id != (response.successor.id if response.successor else None)):
                        raise LifecycleDenied("lifecycle_unavailable")
                    finishing = True
                    return response
                response = self.apply(db, params, mutation, auth.expires_at)
                finishing = True
            return response
        except BookingDenied as exc:
            raise LifecycleDenied(str(exc)) from None
        except IntegrityError as exc:
            if constraint(exc) in ("appointments_active_overlap", "ux_appointments_active_slot"):
                raise LifecycleDenied("slot_unavailable") from None
            raise LifecycleDenied("lifecycle_unavailable") from None
        except (SQLAlchemyError, AuthorizationDenied, ValidationError) as exc:
            if finishing and isinstance(exc, DBAPIError):
                raise LifecycleDenied("lifecycle_outcome_unknown") from None
            raise LifecycleDenied("lifecycle_unavailable") from None

    def transition_allowed(self, row, now):
        if row.status not in ACTIVE:
            raise LifecycleDenied("invalid_transition")
        if now >= row.slot_start - self.cutoff:
            raise LifecycleDenied("cutoff_passed")

    def apply(self, db, params, mutation, auth_expires):
        row = db.execute(OWNED, params).one_or_none()
        if row is None:
            raise LifecycleDenied("not_found")
        self.transition_allowed(row, self.clock())
        target = None
        if mutation.action == "reschedule":
            target = SlotTarget(doctor_id=row.doctor_id, slot_start=mutation.new_slot_start)
            current, day, slot, trust = self.booking.authorize_slot(db, target)
            if current.hospital_id != row.hospital_id:
                raise LifecycleDenied("schedule_authority_unavailable")
        else:
            db.execute(text("SELECT pg_advisory_xact_lock(13301,hashtext(CAST(:doctor AS text)))"),
                       {"doctor": row.doctor_id})
        locked = db.execute(LOCKED, params).one_or_none()
        if locked is None:
            # The update policy excludes a row retired by a committed competing transaction.
            raise LifecycleDenied("invalid_transition")
        now = self.clock()
        self.transition_allowed(locked, now)
        if (row.doctor_id, row.hospital_id, row.slot_start, row.slot_end, row.patient_id) != (
                locked.doctor_id, locked.hospital_id, locked.slot_start, locked.slot_end, locked.patient_id):
            raise LifecycleDenied("invalid_transition")
        status = "rescheduled" if target is not None else "cancelled"
        db.execute(text("UPDATE roghyar.appointments SET status=:status WHERE id=:id"),
                   params | {"status": status})
        successor = None
        evidence = {"revision": None, "hospital_revision": None, "schedule": None, "sequence": None, "deadline": None}
        if target is not None:
            created = self.booking.insert_appointment(db, target, current, slot, locked.patient_id, locked.consent_at)
            successor = view(db.execute(OWNED, {"id": created.id}).one(), predecessor=locked.id)
            evidence = {"revision": current.revision, "hospital_revision": current.hospital_revision,
                        "schedule": fingerprint(current.schedule), "sequence": day.confirmation_revision,
                        "deadline": day.deadline}
        predecessor = db.scalar(text("""
            SELECT appointment_id FROM roghyar.appointment_lifecycle_events WHERE successor_id=:id
        """), params)
        result = MutationResult(action=mutation.action,
            appointment=view(db.execute(OWNED, params).one(), predecessor=predecessor,
                             successor=successor.id if successor else None), successor=successor)
        db.execute(text("""
            INSERT INTO roghyar.appointment_lifecycle_events(appointment_id,successor_id,subject,key_digest,fingerprint,
                action,prior_status,result,doctor_revision,hospital_revision,schedule_digest,confirmation_sequence,evidence_deadline)
            VALUES(:id,:successor,:subject,:key,:fingerprint,:action,:prior,CAST(:result AS jsonb),
                   :revision,:hospital_revision,:schedule,:sequence,:deadline)
        """), params | evidence | {"successor": successor.id if successor else None, "action": mutation.action,
                                   "prior": locked.status, "result": result.model_dump_json()})
        self.transition_allowed(locked, self.clock())
        if self.otp.clock() >= auth_expires:
            raise LifecycleDenied("unauthenticated")
        if target is not None:
            self.booking.recheck(target, slot.start_utc, day.deadline, auth_expires, trust)
        return result
