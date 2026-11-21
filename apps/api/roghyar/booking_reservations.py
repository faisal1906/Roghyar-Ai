"""Private BK-03 booking transaction. No notification or provider I/O."""
from datetime import UTC, datetime
from hashlib import sha256
import hmac
import secrets
from typing import Annotated, Literal
from unicodedata import category
from uuid import UUID, uuid4

from pydantic import AwareDatetime, Field, StringConstraints, ValidationError, field_validator, model_validator
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError, SQLAlchemyError

from .booking_slots import CONFIRMATIONS, confirmation_for
from .directory_governance import fingerprint
from .doctor_schedules import LOCAL, Schedule, local_window, provisional_slots
from .doctor_search import ELIGIBLE, DoctorSearch
from .hospital_search import SearchModel
from .otp_service import OTPDenied, OTPService, normalize_phone
from .red_flags import canonical
from .schedule_confirmation import TrustRegistry
from .scoped_session import AuthorizationDenied, Principal, _scoped_transaction

Name = Annotated[str, StringConstraints(min_length=3, max_length=80)]
Reason = Annotated[str, StringConstraints(min_length=1, max_length=600)]
Key = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9._~-]{16,128}$")]


class BookingRequest(SearchModel):
    doctor_id: UUID
    slot_start: AwareDatetime
    full_name: Name = Field(repr=False)
    age: Annotated[int, Field(ge=0, le=120)]
    phone: Annotated[str, Field(max_length=13, repr=False)]
    consent: Literal[True]
    guardian_name: Name | None = Field(default=None, repr=False)
    gender: Literal["female", "male", "other", "prefer_not_to_say"] | None = None
    reason: Reason | None = Field(default=None, repr=False)
    assessment_id: UUID | None = None

    @field_validator("consent", mode="before")
    @classmethod
    def explicit_consent(cls, value):
        if value is not True:
            raise ValueError("Explicit consent is required")
        return value

    @field_validator("slot_start")
    @classmethod
    def minute_boundary(cls, value):
        if not 2000 <= value.year <= 2200 or value.second or value.microsecond:
            raise ValueError("Invalid slot timestamp")
        return value.astimezone(UTC)

    @field_validator("full_name", "guardian_name", "reason")
    @classmethod
    def safe_text(cls, value):
        if value is not None and (not value.strip() or any(
            category(c).startswith("C") and c not in "\u200c\u200d" for c in value
        )):
            raise ValueError("Invalid booking text")
        return value

    @field_validator("phone")
    @classmethod
    def canonical_phone(cls, value):
        return normalize_phone(value)

    @model_validator(mode="after")
    def guardian_policy(self):
        if self.age < 18 and self.guardian_name is None:
            raise ValueError("Guardian information is required by the product contract")
        if self.age >= 18 and self.guardian_name is not None:
            raise ValueError("Guardian information is unsupported for this request")
        return self


class Reservation(SearchModel):
    id: UUID
    booking_code: Annotated[str, StringConstraints(pattern=r"^RG-[A-F0-9]{24}$")]
    status: Literal["pending"] = "pending"
    doctor_id: UUID
    hospital_id: UUID
    slot_start: AwareDatetime
    slot_end: AwareDatetime
    notification_status: Literal["unavailable"] = "unavailable"
    provider_ack_status: Literal["unavailable"] = "unavailable"


class BookingInput(SearchModel):
    request: BookingRequest
    idempotency_key: Key = Field(repr=False)


class BookingDenied(ValueError):
    pass


CURRENT = text("SELECT c.doctor_id,c.hospital_id,c.revision,c.hospital_revision,c.schedule,"
               "least(c.expires_at,hc.expires_at) AS expires_at " + ELIGIBLE + " AND c.doctor_id=:doctor_id")
IDENTITY = text("SELECT subject,phone_lookup,verified_at,issued_at,expires_at,signing_key FROM roghyar.booking_identity()")


def booking_code():
    return "RG-" + secrets.token_hex(12).upper()


def constraint(exc):
    return getattr(getattr(exc.orig, "diag", None), "constraint_name", None)


class AppointmentBooking:
    def __init__(self, engine, otp: OTPService, schedules: DoctorSearch, trust: TrustRegistry, *, idempotency_key: bytes):
        if (engine.echo or not engine.hide_parameters or len(idempotency_key) != 32
                or idempotency_key in (otp.otp_key, otp.signing_key, otp.encryption._lookup_key,
                                       otp.encryption._encryption_key)):
            raise ValueError("Unsafe booking configuration")
        self.engine = engine.execution_options(isolation_level="READ COMMITTED")
        self.otp, self.schedules, self.trust = otp, schedules, trust
        self.digest_key = idempotency_key

    def reserve(self, token: str, query: object, key: str) -> Reservation:
        try:
            parsed = BookingInput(request=query.model_dump() if isinstance(query, BookingRequest) else query,
                                  idempotency_key=key)
        except ValidationError:
            raise BookingDenied("invalid_request") from None
        request = parsed.request
        try:
            identity, issued, expires = self.otp.decode_credential(token, "booking")
        except OTPDenied:
            raise BookingDenied("unauthenticated") from None
        if request.age < 18:
            raise BookingDenied("minor_policy_unavailable")
        if request.assessment_id is not None:
            raise BookingDenied("assessment_link_unavailable")
        lookup = self.otp.encryption.phone_lookup(request.phone)
        digest = hmac.digest(self.digest_key, b"book:key:v1\0" + identity.subject.bytes
                             + parsed.idempotency_key.encode("ascii"), sha256)
        request_digest = hmac.digest(self.digest_key, b"book:request:v1\0" + canonical(
            request.model_dump(mode="json")), sha256)
        scope = {"subject": identity.subject, "key": digest, "fingerprint": request_digest}
        finishing = False
        try:
            with _scoped_transaction(self.engine, Principal("anonymous", None), "roghyar_booking_writer",
                                     readonly=False) as session:
                session.execute(text("SET LOCAL statement_timeout='2500ms'"))
                session.execute(text("SET LOCAL lock_timeout='1800ms'"))
                session.execute(text("SELECT set_config('roghyar.booking_session', :proof, true)"),
                                {"proof": str(identity.session)})
                auth = session.execute(IDENTITY).one_or_none()
                if (auth is None or auth.subject != identity.subject or auth.signing_key != self.otp.signing_key_id
                        or auth.issued_at.timestamp() != issued or auth.expires_at.timestamp() != expires):
                    raise BookingDenied("unauthenticated")
                if not hmac.compare_digest(auth.phone_lookup, lookup):
                    raise BookingDenied("phone_mismatch")
                session.execute(text("SELECT pg_advisory_xact_lock(:key)"),
                                {"key": int.from_bytes(digest[:8], "big", signed=True)})
                prior = session.execute(text("""
                    SELECT r.fingerprint,r.result,a.id,a.booking_code,a.doctor_id,a.hospital_id,a.slot_start,a.slot_end
                    FROM roghyar.booking_requests r JOIN roghyar.appointments a ON a.id=r.appointment_id
                    WHERE r.subject=:subject AND r.key_digest=:key
                """), scope).one_or_none()
                if prior is not None:
                    if not hmac.compare_digest(prior.fingerprint, request_digest):
                        raise BookingDenied("idempotency_conflict")
                    replay = Reservation.model_validate_json(canonical(prior.result))
                    if replay != Reservation(id=prior.id, booking_code=prior.booking_code,
                                             doctor_id=prior.doctor_id, hospital_id=prior.hospital_id,
                                             slot_start=prior.slot_start, slot_end=prior.slot_end):
                        raise BookingDenied("booking_unavailable")
                    finishing = True
                    return replay
                current, day, slot, trust = self.authorize_slot(session, request)
                now = self.schedules.clock()
                patient = self.patient(session, request, identity.subject, lookup, auth.verified_at, now)
                reservation = self.insert_appointment(session, request, current, slot, patient, now)
                session.execute(text("""
                    INSERT INTO roghyar.booking_requests(subject,key_digest,fingerprint,appointment_id,result)
                    VALUES(:subject,:key,:fingerprint,:id,CAST(:result AS jsonb))
                """), scope | {"id": reservation.id, "result": reservation.model_dump_json()})
                session.execute(text("""
                    INSERT INTO roghyar.booking_events(appointment_id,subject,key_digest,doctor_id,hospital_id,
                        doctor_revision,hospital_revision,schedule_digest,confirmation_sequence,slot_start,slot_end,evidence_deadline)
                    VALUES(:id,:subject,:key,:doctor,:hospital,:revision,:hospital_revision,:schedule,:sequence,:start,:end,:deadline)
                """), scope | {"id": reservation.id, "doctor": current.doctor_id, "hospital": current.hospital_id,
                               "revision": current.revision, "hospital_revision": current.hospital_revision,
                               "schedule": fingerprint(current.schedule), "sequence": day.confirmation_revision,
                               "start": slot.start_utc, "end": slot.end_utc, "deadline": day.deadline})
                self.recheck(request, slot.start_utc, day.deadline, auth.expires_at, trust)
                finishing = True
            return reservation
        except BookingDenied:
            raise
        except IntegrityError as exc:
            if constraint(exc) in ("appointments_active_overlap", "ux_appointments_active_slot"):
                raise BookingDenied("slot_unavailable") from None
            if constraint(exc) in ("patients_auth_subject_key", "patients_phone_lookup_key"):
                raise BookingDenied("identity_conflict") from None
            raise BookingDenied("booking_unavailable") from None
        except (SQLAlchemyError, AuthorizationDenied, ValidationError) as exc:
            if finishing and isinstance(exc, DBAPIError):
                raise BookingDenied("booking_outcome_unknown") from None
            raise BookingDenied("booking_unavailable") from None

    def authorize_slot(self, session, request):
        day = request.slot_start.astimezone(LOCAL).date()
        # LOC-04 and LOC-05 use this same key exclusively for publication, withdrawal and revocation.
        session.execute(text("SELECT pg_advisory_xact_lock_shared(79350919)"))
        now = self.schedules.clock()
        taxonomy = self.schedules.taxonomy(now)
        if taxonomy is None:
            raise BookingDenied("schedule_authority_unavailable")
        if not local_window(day, now):
            raise BookingDenied("slot_unavailable")
        current = session.execute(CURRENT, taxonomy | {"doctor_id": request.doctor_id}).one_or_none()
        if current is None:
            raise BookingDenied("slot_unavailable")
        scope = {"doctor_id": current.doctor_id, "hospital_id": current.hospital_id}
        session.execute(text("""
            SELECT pg_advisory_xact_lock_shared(roghyar.booking_day_lock(:doctor_id,:hospital_id,:day))
        """), scope | {"day": day})
        row = session.execute(CONFIRMATIONS, scope | {"first": day, "last": day}).one_or_none()
        schedule = Schedule.model_validate_json(canonical(current.schedule))
        trust = self.trust_snapshot()
        result, sessions = confirmation_for(schedule, fingerprint(schedule.model_dump(mode="json")),
                                             current, scope, day, row, trust, now)
        if not sessions:
            raise BookingDenied("schedule_authority_unavailable" if result.status in (
                "provider_unavailable", "schedule_unconfirmed") else "slot_unavailable")
        slot = next((slot for slot in provisional_slots(schedule, day, now, self.schedules.lead_time)
                     if slot.start_utc == request.slot_start), None)
        if slot is None or session.scalar(text("""
            SELECT roghyar.doctor_slot_occupied(:doctor_id,:hospital_id,:start,:end)
        """), scope | {"start": slot.start_utc, "end": slot.end_utc}):
            raise BookingDenied("slot_unavailable")
        return current, result, slot, trust

    def trust_snapshot(self):
        try:
            return self.trust.snapshot()
        except Exception:
            raise BookingDenied("schedule_authority_unavailable") from None

    def recheck(self, request, slot_start, deadline, auth_expires, trust):
        now = self.schedules.clock()
        if self.otp.clock() >= auth_expires:
            raise BookingDenied("unauthenticated")
        if self.trust_snapshot() != trust:
            raise BookingDenied("schedule_authority_unavailable")
        if (now >= deadline or not local_window(request.slot_start.astimezone(LOCAL).date(), now)
                or slot_start <= now or slot_start < now + self.schedules.lead_time):
            raise BookingDenied("slot_unavailable")

    def patient(self, session, request, subject, lookup, verified_at, now):
        # Separate keys may legitimately target different slots for the same new patient.
        session.execute(text("SELECT pg_advisory_xact_lock(:key)"),
                        {"key": int.from_bytes(sha256(b"booking-patient-v1\0" + subject.bytes).digest()[:8],
                                               "big", signed=True)})
        existing = session.execute(text("""
            SELECT id,phone_verified_at FROM roghyar.patients WHERE auth_subject=:subject AND phone_lookup=:lookup
        """), {"subject": subject, "lookup": lookup}).one_or_none()
        if existing is not None:
            if existing.phone_verified_at is None:
                raise BookingDenied("identity_conflict")
            return existing.id
        patient = uuid4()
        encrypt = self.otp.encryption.encrypt
        session.execute(text("""
            INSERT INTO roghyar.patients(id,auth_subject,full_name_ciphertext,phone_ciphertext,phone_lookup,
                                         phone_verified_at,age,gender,consent_at)
            VALUES(:id,:subject,:name,:phone,:lookup,:verified,:age,:gender,:now)
        """), {"id": patient, "subject": subject, "lookup": lookup, "verified": verified_at, "now": now,
               "name": encrypt(request.full_name, record_id=patient, field_name="full_name"),
               "phone": encrypt(request.phone, record_id=patient, field_name="phone"),
               "age": request.age, "gender": request.gender})
        return patient

    def insert_appointment(self, session, request, current, slot, patient, now):
        appointment = uuid4()
        reason = (self.otp.encryption.encrypt(request.reason, record_id=appointment, field_name="reason")
                  if request.reason is not None else None)
        for _ in range(3):
            code = booking_code()
            try:
                with session.begin_nested():
                    session.execute(text("""
                        INSERT INTO roghyar.appointments(id,booking_code,doctor_id,hospital_id,patient_id,
                                                slot_start,slot_end,reason_ciphertext,status,consent_at)
                        VALUES(:id,:code,:doctor,:hospital,:patient,:start,:end,:reason,'pending',:now)
                    """), {"id": appointment, "code": code, "doctor": current.doctor_id, "hospital": current.hospital_id,
                           "patient": patient, "start": slot.start_utc, "end": slot.end_utc, "reason": reason, "now": now})
            except IntegrityError as exc:
                if constraint(exc) != "appointments_booking_code_key":
                    raise
            else:
                return Reservation(id=appointment, booking_code=code, doctor_id=current.doctor_id,
                                   hospital_id=current.hospital_id, slot_start=slot.start_utc, slot_end=slot.end_utc)
        raise BookingDenied("booking_unavailable")
