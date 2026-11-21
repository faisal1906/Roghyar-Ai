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
            scope = [fixture.doctor.doctor_id]
            if hasattr(fixture, "additional_doctor"):
                scope.append(fixture.additional_doctor)
            parameters = {"doctors": scope}
            owner.execute(text("DELETE FROM roghyar.booking_events WHERE doctor_id=ANY(:doctors)"), parameters)
            owner.execute(text("""
                DELETE FROM roghyar.booking_requests r USING roghyar.appointments a
                WHERE a.id=r.appointment_id AND a.doctor_id=ANY(:doctors)
            """), parameters)
            owner.execute(text("""
                DELETE FROM roghyar.appointments a USING roghyar.patients p,roghyar.otp_identities i
                WHERE a.patient_id=p.id AND p.auth_subject=i.subject AND a.doctor_id=ANY(:doctors)
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
], ids=("uuid","naive","precision","name_empty","name_long","control","boolean_age","age_range",
         "minor_guardian","phone","consent_false","consent_integer","reason_long","unicode",
         "assessment_uuid","hospital_injection","slot_end_injection","verification_injection"))
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


@pytest.mark.parametrize("change", ["otp_revoked", "doctor_withdrawn", "hospital_withdrawn", "closure", "reviewer_revoked", "schedule_replaced"])
def test_bk03_authority_wins_before_reservation(change, booking, governed_fixtures):
    token = booking.token()
    proof = booking.proof()
    key = new_key()
    advanced = False
    revised = None
    if change == "schedule_replaced":
        old = booking.doctor
        revised = booking.doctors.candidate(doctor_id=old.doctor_id, registration=old.registration,
                                            revision=2, schedule=old.schedule)
        booking.doctors.stage(revised)
        for kind in ("credential", "schedule"):
            booking.doctors.approve(revised, kind)

    def race(conn, cursor, statement, parameters, context, executemany):
        nonlocal advanced
        point = {
            "otp_revoked": "SELECT subject,phone_lookup,verified_at",
            "doctor_withdrawn": "pg_advisory_xact_lock_shared(79350919)",
            "hospital_withdrawn": "pg_advisory_xact_lock_shared(79350919)",
            "closure": "roghyar.booking_day_lock",
            "reviewer_revoked": "INSERT INTO roghyar.booking_events",
            "schedule_replaced": "pg_advisory_xact_lock_shared(79350919)",
        }[change]
        if advanced or point not in statement:
            return
        advanced = True
        if change == "otp_revoked":
            booking.otp.service.revoke(token, "booking")
        elif change == "doctor_withdrawn":
            booking.doctors.services["operator"].withdraw(proof.doctor_id, proof.hospital_id, proof.doctor_revision)
        elif change == "hospital_withdrawn":
            governed_fixtures.services["operator"].withdraw(proof.hospital_id)
        elif change == "schedule_replaced":
            booking.doctors.publish(revised)
        elif change == "closure":
            submit(booking.confirmation_writer,
                   booking.proof(sequence=2, source_version=2, state="closure", sessions=()))
        else:
            booking.trust.view = TrustSnapshot(booking.trust.view.authorities, revoked=frozenset({proof.reviewer}))

    event.listen(booking.service.engine, "before_cursor_execute", race)
    try:
        with pytest.raises(BookingDenied, match={"otp_revoked": "unauthenticated", "reviewer_revoked": "schedule_authority_unavailable",
                    "schedule_replaced": "schedule_authority_unavailable"}.get(change, "slot_unavailable")):
            booking.reserve(token, key=key)
    finally:
        event.remove(booking.service.engine, "before_cursor_execute", race)
    assert advanced and booking.counts() == (0, 0, 0)


def test_bk03_revocation_waits_for_commit(booking):
    token = booking.token()
    credential = booking.otp.service.authorize(token, "booking")
    attempted = False

    def blocked_writer(conn, cursor, statement, parameters, context, executemany):
        nonlocal attempted
        if attempted or "INSERT INTO roghyar.booking_events" not in statement:
            return
        attempted = True
        with pytest.raises(DBAPIError) as rejected, booking.otp.writer.begin() as db:
            db.execute(text("SET LOCAL lock_timeout='150ms'"))
            db.execute(text("UPDATE roghyar.otp_sessions SET revoked_at=now() WHERE id=:id"),
                       {"id": credential.session})
        assert rejected.value.orig.sqlstate == "55P03"

    event.listen(booking.service.engine, "after_cursor_execute", blocked_writer)
    try:
        assert booking.reserve(token).status == "pending"
    finally:
        event.remove(booking.service.engine, "after_cursor_execute", blocked_writer)
    assert attempted
    booking.otp.service.revoke(token, "booking")
    with pytest.raises(BookingDenied, match="unauthenticated"):
        booking.reserve(token, i=1)


def test_bk03_booking_code_collision_retries(booking, monkeypatch):
    from roghyar import booking_reservations

    token = booking.token()
    first = booking.reserve(token)
    values = iter((first.booking_code, "RG-" + "B"*24))
    monkeypatch.setattr(booking_reservations, "booking_code", lambda: next(values))
    second = booking.reserve(token, i=1)
    assert second.booking_code == "RG-" + "B"*24 and booking.counts() == (2, 2, 2)


def test_bk03_restricted_sql_and_tampered_result(booking, database):
    token, key = booking.token(), new_key()
    first = booking.reserve(token, key=key)
    other = booking.token(OTHER)
    credential = booking.otp.service.authorize(other, "booking")
    for connection, sql in ((database, "INSERT INTO roghyar.booking_requests DEFAULT VALUES"),
                            (booking.writer, "DELETE FROM roghyar.booking_events"),
                            (booking.writer, "UPDATE roghyar.appointments SET status='confirmed'"),
                            (booking.writer, "UPDATE roghyar.doctor_current SET revision=900"),
                            (booking.writer, "SELECT * FROM roghyar.assessments"),
                            (booking.writer, "SELECT * FROM roghyar.otp_sessions")):
        with pytest.raises(DBAPIError), connection.begin() as db:
            db.execute(text(sql))
    with booking.writer.begin() as db:
        db.execute(text("SELECT set_config('roghyar.booking_session',:id,true)"),
                   {"id": str(credential.session)})
        assert db.scalar(text("SELECT count(*) FROM roghyar.booking_requests")) == 0
        assert db.scalar(text("SELECT count(id) FROM roghyar.appointments")) == 0
        assert db.scalar(text("SELECT count(id) FROM roghyar.patients")) == 0
        with pytest.raises(DBAPIError) as denied, db.begin_nested():
            db.execute(text("""
                INSERT INTO roghyar.appointments(id,booking_code,doctor_id,hospital_id,patient_id,
                                                  slot_start,slot_end,status,consent_at)
                VALUES(:id,'SYNTHETIC INVALID',:doctor,:hospital,:victim,:start,:end,'pending',now())
            """), {"id": uuid4(), "doctor": first.doctor_id, "hospital": first.hospital_id,
                   "victim": patient_of(first.id), "start": booking.intervals[1].start_utc,
                   "end": booking.intervals[1].end_utc})
        assert denied.value.orig.sqlstate == "42501"
    with engine.begin() as db:
        db.execute(text("""
            UPDATE roghyar.booking_requests
            SET result=jsonb_set(result,'{booking_code}',to_jsonb(CAST(:different AS text)))
            WHERE appointment_id=:id
        """), {"id": first.id, "different": "RG-" + "F"*24})
    with pytest.raises(BookingDenied, match="booking_unavailable"):
        booking.reserve(token, key=key)


def patient_of(appointment):
    with engine.begin() as db:
        return db.scalar(text("SELECT patient_id FROM roghyar.appointments WHERE id=:id"), {"id": appointment})


def test_bk03_live_measurements(booking):
    from statistics import median
    from time import perf_counter

    token, key = booking.token(), new_key()
    values = {}

    def measure(name, call, count):
        durations = []
        for i in range(count):
            start = perf_counter()
            call(i)
            durations.append((perf_counter() - start) * 1000)
        values[name] = (len(durations), median(durations), sorted(durations)[-1])

    base = booking.proof().day
    for offset in (7, 14, 21):
        submit(booking.confirmation_writer, booking.proof(day=base + timedelta(days=offset)))
    for i in range(24):
        occupied_at = booking.intervals[0].start_utc + timedelta(days=35, hours=i)
        booking.doctors.appointment(booking.doctor, occupied_at, occupied_at + timedelta(minutes=20), "confirmed")

    def commit(i):
        if i == 0:
            booking.reserve(token, key=key)
        else:
            week, slot = divmod(i - 1, 3)
            start = booking.intervals[slot].start_utc + timedelta(days=7 * (week + 1))
            booking.service.reserve(token, booking.payload(slot_start=start), new_key())

    measure("commit", commit, 10)
    measure("replay", lambda _: booking.reserve(token, key=key), 10)

    def contention(_):
        with pytest.raises(BookingDenied, match="slot_unavailable"):
            booking.reserve(token)

    measure("conflict", contention, 10)
    start = booking.intervals[0].start_utc

    def overlap(_):
        with pytest.raises(IntegrityError) as conflict:
            booking.doctors.appointment(booking.doctor, start + timedelta(minutes=5),
                                        start + timedelta(minutes=15), "confirmed")
        assert conflict.value.orig.sqlstate == "23P01"

    measure("overlap", overlap, 10)
    with engine.begin() as db:
        db.execute(text("SET LOCAL enable_seqscan=off"))
        plan = db.execute(text("""
            EXPLAIN SELECT id FROM roghyar.appointments WHERE doctor_id=:doctor
            AND tstzrange(slot_start,slot_end,'[)') && tstzrange(:start,:finish,'[)')
            AND status IN ('pending','confirmed')
        """), {"doctor": booking.doctor.doctor_id, "start": start, "finish": start+timedelta(minutes=20)}).scalars()
        assert any("Index" in row for row in plan)
        for relation, predicate in (
            ("booking_requests", "subject=:subject AND key_digest=:key"),
            ("patients", "auth_subject=:subject"),
            ("doctor_current", "doctor_id=:doctor"),
            ("slot_confirmations", "doctor_id=:doctor AND hospital_id=:hospital AND day=:day"),
        ):
            probe = db.execute(text(f"EXPLAIN SELECT 1 FROM roghyar.{relation} WHERE {predicate}"),
                               {"subject": uuid4(), "key": b"x"*32, "doctor": booking.doctor.doctor_id,
                                "hospital": booking.doctor.hospital_id, "day": base}).scalars()
            assert any("Index" in row for row in probe)

    other = booking.token(OTHER)
    barrier = Barrier(2, timeout=5)

    def concurrent(i):
        worker = booking.new_worker()
        payload = booking.payload(i + 1, phone=OTHER if i else PHONE)
        barrier.wait()
        begin = perf_counter()
        assert worker.reserve(other if i else token, payload, new_key()).status == "pending"
        return (perf_counter()-begin)*1000

    with ThreadPoolExecutor(2) as pool:
        concurrent_times = list(pool.map(concurrent, (0, 1)))
    values["parallel"] = (2, median(concurrent_times), max(concurrent_times))
    print("\nBK03 measurements " + " ".join(
        f"{name}={count},{p50:.3f},{p95:.3f}" for name, (count, p50, p95) in values.items()))


def test_bk03_different_doctors_confirm_at_same_time(booking, doctors):
    alternate = doctors.candidate(schedule=booking.doctor.schedule)
    doctors.activate(alternate)
    submit(booking.confirmation_writer, booking.proof(doctor_id=alternate.doctor_id))
    token = booking.token()
    barrier = Barrier(2, timeout=5)
    # Track this appointment for cleanup in the booking fixture.
    booking.additional_doctor = alternate.doctor_id

    def reserve(doctor):
        worker = booking.new_worker()
        barrier.wait()
        return worker.reserve(token, booking.payload(doctor_id=doctor), new_key())

    with ThreadPoolExecutor(2) as pool:
        first, second = pool.map(reserve, (booking.doctor.doctor_id, alternate.doctor_id))
    assert first.doctor_id != second.doctor_id and first.slot_start == second.slot_start


def test_bk03_migration_blocks_legacy_overlap_and_nonempty_history(booking):
    import importlib
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    migration = importlib.import_module("migrations.versions.0013_atomic_reservations")
    result = booking.reserve(booking.token())
    with pytest.raises(DBAPIError, match="Booking history requires approved archival"), engine.begin() as db:
        with Operations.context(MigrationContext.configure(db)):
            migration.downgrade()
    assert booking.counts() == (1, 1, 1)
    with pytest.raises(DBAPIError, match="Existing active reservations overlap"), engine.begin() as db:
        db.execute(text("ALTER TABLE roghyar.appointments DROP CONSTRAINT appointments_active_overlap"))
        db.execute(text("""
            INSERT INTO roghyar.appointments(id,booking_code,doctor_id,hospital_id,patient_id,
                                                slot_start,slot_end,status,consent_at)
            SELECT :id,'SYNTHETIC LEGACY',doctor_id,hospital_id,patient_id,
                   slot_start+interval '5 minutes',slot_end+interval '5 minutes','confirmed',consent_at
            FROM roghyar.appointments WHERE id=:existing
        """), {"id": uuid4(), "existing": result.id})
        with Operations.context(MigrationContext.configure(db)):
            migration.upgrade()
    assert booking.counts() == (1, 1, 1)


def test_bk03_database_dependency_failure(booking):
    token = booking.token()
    offline = create_engine(booking.writer.url.set(port=1).update_query_dict({"connect_timeout": "1"}),
                            hide_parameters=True)
    try:
        with pytest.raises(BookingDenied, match="^booking_unavailable$"):
            booking.new_worker(writer=offline).reserve(token, booking.payload(), new_key())
    finally:
        offline.dispose()
    assert booking.counts() == (0, 0, 0)
