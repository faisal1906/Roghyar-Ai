"""SYNTHETIC DEVELOPMENT ONLY. BK-03 tests run against real restricted roles."""
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
import os
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import DBAPIError, IntegrityError, OperationalError

from directory_fixtures import directory_actors, governed_fixtures
from test_booking_slots import slots, submit
from test_database_security import database
from test_doctor_governance import doctor_actors, doctors
from test_otp_service import otp
from roghyar.booking_http import private_booking_router
from roghyar.booking_reservations import AppointmentBooking, BookingDenied, BookingInput, BookingRequest
from roghyar.main import app, create_app, engine
from roghyar.schedule_confirmation import TrustSnapshot

NAME = "SYNTHETIC DEVELOPMENT ONLY Person"
PHONE = "03012345678"
OTHER = "03012345679"
ORIGIN = "https://synthetic-booking.example.invalid"


def new_key():
    return uuid4().hex


def make_payload(doctor, start, **kwargs):
    return {"doctor_id": doctor, "slot_start": start, "full_name": NAME, "phone": PHONE, "age": 30,
            "consent": True, "reason": "SYNTHETIC DEVELOPMENT ONLY"} | kwargs


class Bookings:
    def __init__(self, otp, doctors, slots, writer):
        doctor, confirmation_writer, proof, read, self.trust = slots
        self.otp, self.doctors, self.doctor, self.writer = otp, doctors, doctor, writer
        self.proof, self.confirmation_writer = proof, confirmation_writer
        submit(confirmation_writer, proof())
        self.intervals = read().days[0].slots
        assert len(self.intervals) == 3
        self.key = os.urandom(32)
        self.service = self.new_worker()

    def new_worker(self, writer=None):
        return AppointmentBooking(writer or self.writer, self.otp.service, self.doctors.read(), self.trust,
                                  idempotency_key=self.key)

    def token(self, phone=PHONE, purpose="booking"):
        self.otp.request(phone=phone, purpose=purpose)
        return self.otp.verify(phone=phone, purpose=purpose)

    def payload(self, i=0, **changes):
        return make_payload(self.doctor.doctor_id, self.intervals[i].start_utc, **changes)

    def reserve(self, token, i=0, key=None, **changes):
        return self.service.reserve(token, self.payload(i, **changes), key or new_key())

    def counts(self):
        parameters = {"doctor": self.doctor.doctor_id}
        with engine.begin() as db:
            return tuple(db.scalar(text(sql), parameters) for sql in (
                "SELECT count(*) FROM roghyar.appointments WHERE doctor_id=:doctor",
                "SELECT count(*) FROM roghyar.booking_events WHERE doctor_id=:doctor",
                "SELECT count(*) FROM roghyar.booking_requests r JOIN roghyar.appointments a ON a.id=r.appointment_id "
                "WHERE a.doctor_id=:doctor",
            ))


@pytest.fixture
def booking(slots, doctors, otp):
    with engine.begin() as owner:
        owner.execute(text("ALTER ROLE roghyar_booking_writer PASSWORD 'synthetic-booking-local'"))
    writer = create_engine(engine.url.set(username="roghyar_booking_writer", password="synthetic-booking-local"),
                           pool_size=5, max_overflow=1, hide_parameters=True)
    fixture = Bookings(otp, doctors, slots, writer)
    try:
        yield fixture
    finally:
        writer.dispose()
        with engine.begin() as owner:
            parameters = {"doctor": fixture.doctor.doctor_id}
            owner.execute(text("DELETE FROM roghyar.booking_events WHERE doctor_id=:doctor"), parameters)
            owner.execute(text("""
                DELETE FROM roghyar.booking_requests r USING roghyar.appointments a
                WHERE a.id=r.appointment_id AND a.doctor_id=:doctor
            """), parameters)
            owner.execute(text("""
                DELETE FROM roghyar.appointments a USING roghyar.patients p,roghyar.otp_identities i
                WHERE a.patient_id=p.id AND p.auth_subject=i.subject AND a.doctor_id=:doctor
            """), parameters)
            owner.execute(text("""
                DELETE FROM roghyar.patients p USING roghyar.otp_identities i WHERE p.auth_subject=i.subject
            """))


@pytest.mark.parametrize("field,invalid", [
    ("doctor_id", "not-a-uuid"), ("slot_start", datetime(2030, 1, 1)),
    ("slot_start", datetime(2030, 1, 1, 9, 0, 1, tzinfo=UTC)),
    ("full_name", ""), ("full_name", "x"*81), ("full_name", "bad\x00name"), ("age", True),
    ("age", 121), ("age", 17), ("phone", "030-12345678"), ("consent", False), ("consent", 1),
    ("reason", "x"*601), ("reason", "\ud800"), ("assessment_id", "no"), ("hospital_id", uuid4()),
    ("slot_end", datetime(2030, 1, 1, tzinfo=UTC)), ("verified", True),
])
def test_bk03_strict_validation(field, invalid):
    valid = make_payload(uuid4(), datetime(2030, 1, 1, tzinfo=UTC))
    assert BookingRequest.model_validate(valid).phone == "+923012345678"
    with pytest.raises(ValidationError):
        BookingRequest.model_validate(valid | {field: invalid})


@pytest.mark.parametrize("race", ["same_slot", "same_key", "adjacent", "different_subject_same_key"])
def test_bk03_concurrent_booking(race, booking):
    token = booking.token()
    other = booking.token(OTHER) if race in ("same_slot", "different_subject_same_key") else token
    key = new_key()
    barrier = Barrier(2, timeout=5)

    def worker(i):
        worker = booking.new_worker()
        payload = booking.payload(i if race in ("adjacent", "different_subject_same_key") else 0,
                                  phone=OTHER if i and other != token else PHONE)
        barrier.wait()
        try:
            return worker.reserve(token if i == 0 else other, payload, key if race in (
                "same_key", "different_subject_same_key") else new_key())
        except BookingDenied as exc:
            return str(exc)

    with ThreadPoolExecutor(2) as pool:
        a, b = pool.map(worker, (0, 1))
    if race == "same_slot":
        assert [r for r in (a, b) if isinstance(r, str)] == ["slot_unavailable"]
        assert booking.counts() == (1, 1, 1)
    elif race == "same_key":
        assert a == b and a.status == "pending" and booking.counts() == (1, 1, 1)
    else:
        assert a.id != b.id and a.slot_end == b.slot_start and booking.counts() == (2, 2, 2)
    with engine.begin() as owner:
        patients = owner.scalar(text("""
            SELECT count(*) FROM roghyar.patients p JOIN roghyar.otp_identities i ON i.subject=p.auth_subject
        """))
        assert patients == (2 if race == "different_subject_same_key" else 1)


def test_bk03_overlap_constraint_and_status_reuse(booking, doctors):
    doctor = booking.doctor
    start = booking.intervals[0].start_utc
    barrier = Barrier(2, timeout=5)

    def insert(offset):
        barrier.wait()
        try:
            return doctors.appointment(doctor, start + timedelta(minutes=offset),
                                       start + timedelta(minutes=offset + 20), "pending")
        except IntegrityError as exc:
            assert exc.orig.sqlstate == "23P01"
            return None

    with ThreadPoolExecutor(2) as pool:
        result = list(pool.map(insert, (0, 10)))
    winner, = [row for row in result if row is not None]
    cancelled = doctors.appointment(doctor, start + timedelta(minutes=5), start + timedelta(minutes=18), "cancelled")
    with pytest.raises(IntegrityError) as conflict, engine.begin() as db:
        db.execute(text("UPDATE roghyar.appointments SET status='confirmed' WHERE id=:id"), {"id": cancelled})
    assert conflict.value.orig.sqlstate == "23P01"
    with engine.begin() as db:
        db.execute(text("UPDATE roghyar.appointments SET status='cancelled' WHERE id=:id"), {"id": winner})
    assert doctors.appointment(doctor, start + timedelta(minutes=5), start + timedelta(minutes=18), "confirmed")


def test_bk03_restart_replays_durable_encrypted_commit(booking):
    token, key = booking.token(), new_key()
    r = booking.reserve(token, key=key)
    assert booking.new_worker().reserve(token, booking.payload(), key) == r
    assert r.status == "pending" and r.notification_status == r.provider_ack_status == "unavailable"
    assert booking.counts() == (1, 1, 1)
    with engine.begin() as owner:
        patient = owner.execute(text("""
            SELECT p.id,p.full_name_ciphertext,p.phone_ciphertext,a.reason_ciphertext
            FROM roghyar.patients p JOIN roghyar.appointments a ON a.patient_id=p.id WHERE a.id=:id
        """), {"id": r.id}).one()
        enc = booking.otp.encryption
        assert enc.decrypt(patient.full_name_ciphertext, record_id=patient.id, field_name="full_name") == NAME
        assert enc.decrypt(patient.phone_ciphertext, record_id=patient.id, field_name="phone") == "+923012345678"
        assert enc.decrypt(patient.reason_ciphertext, record_id=r.id, field_name="reason") == booking.payload()["reason"]
        event_row = owner.execute(text("SELECT * FROM roghyar.booking_events WHERE appointment_id=:id"),
                                  {"id": r.id}).one()
        assert event_row.confirmation_sequence == 1 and event_row.doctor_revision == booking.doctor.revision
        assert event_row.slot_start == r.slot_start and event_row.slot_end == r.slot_end
        evidence = owner.scalar(text("SELECT result::text FROM roghyar.booking_requests WHERE appointment_id=:id"),
                                {"id": r.id})
        assert NAME not in evidence and PHONE not in evidence and key not in evidence and token not in evidence
    with pytest.raises(BookingDenied, match="idempotency_conflict"):
        booking.reserve(token, key=key, full_name="SYNTHETIC Changed Person")
    with pytest.raises(BookingDenied, match="slot_unavailable"):
        booking.reserve(token)
    assert booking.counts() == (1, 1, 1)


@pytest.mark.parametrize("committed", [True, False])
def test_bk03_unknown_commit_recovery(committed, booking):
    token, key = booking.token(), new_key()
    def disconnect(conn):
        if committed:
            conn.connection.driver_connection.commit()
        else:
            conn.connection.driver_connection.rollback()
        raise OperationalError(None, None, RuntimeError("SYNTHETIC lost connection"), hide_parameters=True)

    event.listen(booking.service.engine, "commit", disconnect)
    try:
        with pytest.raises(BookingDenied, match="booking_outcome_unknown"):
            booking.reserve(token, key=key)
    finally:
        event.remove(booking.service.engine, "commit", disconnect)
    assert booking.counts() == ((1, 1, 1) if committed else (0, 0, 0))
    result = booking.new_worker().reserve(token, booking.payload(), key)
    assert result.id and booking.counts() == (1, 1, 1)


@pytest.mark.parametrize("attack,denial", [
    ("wrong_purpose", "unauthenticated"), ("expired", "unauthenticated"), ("revoked", "unauthenticated"),
    ("forged", "unauthenticated"), ("mismatch", "phone_mismatch"), ("assessment", "assessment_link_unavailable"),
    ("minor", "minor_policy_unavailable"), ("outside", "slot_unavailable"), ("unknown_doctor", "slot_unavailable"),
    ("offset", "slot_unavailable"), ("missing_confirmation", "schedule_authority_unavailable"),
])
def test_bk03_booking_denials(attack, denial, booking):
    token = booking.token(purpose="appointments" if attack == "wrong_purpose" else "booking")
    payload = booking.payload()
    if attack == "expired":
        booking.otp.now += timedelta(minutes=11)
    elif attack == "revoked":
        booking.otp.service.revoke(token, "booking")
    elif attack == "forged":
        token = token[:-4] + "AAAA"
    elif attack == "assessment":
        payload["assessment_id"] = uuid4()
    elif attack == "minor":
        payload.update(age=17, guardian_name=NAME)
    elif attack == "mismatch":
        payload["phone"] = OTHER
    elif attack == "outside":
        payload["slot_start"] += timedelta(days=31)
    elif attack == "offset":
        payload["slot_start"] += timedelta(minutes=1)
    elif attack == "missing_confirmation":
        payload["slot_start"] += timedelta(days=7)
    elif attack == "unknown_doctor":
        payload["doctor_id"] = uuid4()
    with pytest.raises(BookingDenied, match=f"^{denial}$"):
        booking.service.reserve(token, payload, new_key())
    assert booking.counts() == (0, 0, 0)


def test_bk03_private_http_and_closed_release(booking, caplog):
    normal = TestClient(app)
    assert normal.post("/api/v1/appointments", json={}).status_code == 404
    assert normal.post("/appointments", json={}).status_code == 404
    private = create_app(production=False)
    private.include_router(private_booking_router(booking.service, origin=ORIGIN))
    client = TestClient(private, base_url=ORIGIN)
    token = booking.token()
    shape = BookingRequest.model_validate(booking.payload()).model_dump(mode="json")
    headers = {"Authorization": f"Bearer {token}", "Idempotency-Key": new_key(), "Origin": ORIGIN}
    assert client.post("/appointments", json=shape, headers=headers | {"Origin": "https://other.invalid"}).status_code == 403
    assert client.post("/appointments", json=shape, headers=headers | {"Idempotency-Key": "bad"}).status_code == 422
    assert client.post("/appointments", json=shape | {"reason": "X"*5000}, headers=headers).status_code == 413
    duplicate = '{"consent":true,"consent":false}'
    assert client.post("/appointments", content=duplicate, headers=headers | {"Content-Type": "application/json"}).status_code == 422
    first = client.post("/appointments", json=shape, headers=headers)
    assert first.status_code == 201 and first.headers["cache-control"] == "no-store"
    assert first.json()["provider_ack_status"] == "unavailable"
    assert client.post("/appointments", json=shape, headers=headers).json() == first.json()
    assert client.post("/appointments", json=shape | {"age": 31},
                       headers=headers).json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    contention = client.post("/appointments", json=shape, headers=headers | {"Idempotency-Key": new_key()})
    assert contention.status_code == 409 and contention.json()["error"]["code"] == "SLOT_UNAVAILABLE"
    assert token not in caplog.text and PHONE not in caplog.text and NAME not in caplog.text
