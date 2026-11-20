"""SYNTHETIC—DEVELOPMENT ONLY. Governed slots on disposable PostgreSQL."""
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, time, timedelta
from hashlib import sha256
from threading import Barrier
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from directory_fixtures import directory_actors, governed_fixtures
from test_database_security import database
from test_doctor_governance import doctor_actors, doctors
from roghyar.booking_slots import BookingCalendar, CalendarQuery, working_sessions
from roghyar.directory_governance import fingerprint
from roghyar.doctor_schedules import LOCAL, Schedule, ScheduleException, WeeklySession, local_window, provisional_slots
from roghyar.main import app, engine
from roghyar.schedule_confirmation import Authority, Confirmation, ConfirmationDenied, ConfirmationWriter, TrustSnapshot

EVIDENCE = b"SYNTHETIC DEVELOPMENT ONLY provider evidence"


class Registry:
    def __init__(self, view):
        self.view = view

    def snapshot(self):
        return self.view


@pytest.fixture
def slots(doctors):
    candidate = doctors.candidate()
    doctors.activate(candidate)
    key, reviewer = Ed25519PrivateKey.generate(), uuid4()
    now = doctors.now
    registry = Registry(TrustSnapshot({reviewer: Authority(key.public_key(), candidate.hospital_id,
        "synthetic_provider", now - timedelta(days=1), now + timedelta(days=31))}))
    with engine.begin() as owner:
        owner.execute(text("ALTER ROLE roghyar_slot_writer PASSWORD 'synthetic-slot-writer-local'"))
        owner.execute(text("INSERT INTO roghyar.slot_writer_scope VALUES('roghyar_slot_writer',:hospital)"),
                      {"hospital": candidate.hospital_id})
    writer_engine = create_engine(engine.url.set(username="roghyar_slot_writer", password="synthetic-slot-writer-local"),
                                  hide_parameters=True)
    writer = ConfirmationWriter(writer_engine, registry, clock=lambda: doctors.now)
    reader = BookingCalendar(doctors.read(), registry)

    def proof(**overrides):
        fields = dict(schema_version=1, doctor_id=candidate.doctor_id, hospital_id=candidate.hospital_id,
            day=doctors.day, sequence=1, doctor_revision=candidate.revision, hospital_revision=candidate.hospital_revision,
            schedule_digest=fingerprint(candidate.schedule.model_dump(mode="json")), state="working",
            sessions=working_sessions(candidate.schedule, doctors.day), organization="synthetic_provider",
            source_reference="synthetic_record", source_version=1, evidence_digest=sha256(EVIDENCE).hexdigest(),
            confirmed_at=now, effective_at=now, expires_at=now + timedelta(hours=12),
            reviewer=reviewer, signature="0"*128)
        value = Confirmation(**(fields | overrides))
        return value.model_copy(update={"signature": key.sign(value.signing_bytes()).hex()})

    def read(**overrides):
        query = {"doctor_id": candidate.doctor_id, "hospital_id": candidate.hospital_id, "start": doctors.day}
        return reader.calendar(CalendarQuery(**(query | overrides)))

    try:
        yield candidate, writer, proof, read, registry
    finally:
        writer_engine.dispose()
        with engine.begin() as owner:
            owner.execute(text("DELETE FROM roghyar.slot_confirmations WHERE hospital_id=:hospital"),
                          {"hospital": candidate.hospital_id})
            owner.execute(text("DELETE FROM roghyar.slot_writer_scope WHERE hospital_id=:hospital"),
                          {"hospital": candidate.hospital_id})


def submit(writer, proof):
    return writer.submit(proof.model_dump_json().encode(), EVIDENCE)


@pytest.mark.parametrize("minutes,expected", [(15, 4), (20, 3), (25, 2)])
def test_bk02_whole_intervals_and_rolling_window(minutes, expected):
    now = datetime(2028, 2, 28, 20, tzinfo=UTC)
    day = date(2028, 2, 29)
    assert now.astimezone(LOCAL).date() == day
    assert all(local_window(day + timedelta(days=i), now) == (0 <= i <= 30) for i in (-1, 0, 1, 7, 29, 30, 31))
    assert not local_window(day, now + timedelta(days=1))
    assert local_window(date(2029, 1, 1), datetime(2028, 12, 31, 23, tzinfo=UTC))
    schedule = Schedule(timezone="Asia/Karachi", effective_from=day, effective_until=day,
                        weekly=(WeeklySession(weekday=day.weekday(), start=time(9), end=time(10), minutes=minutes),))
    result = provisional_slots(schedule, day, now, timedelta(0))
    assert len(result) == expected and result[0].start_utc.hour == 4
    assert all((slot.end_utc-slot.start_utc) == timedelta(minutes=minutes) for slot in result)
    cutoff = datetime.combine(day, time(9), tzinfo=LOCAL)
    assert len(provisional_slots(schedule, day, cutoff, timedelta(minutes=minutes))) == expected - 1


def test_bk02_window_and_confirmation(slots):
    candidate, writer, proof, read, _ = slots
    initial = read(days=31)
    assert initial.status == "calendar" and len(initial.days) == 31
    assert initial.days[0].status == "schedule_unconfirmed" and not initial.days[0].slots
    assert read(days=31, start=proof().day + timedelta(days=1)).status == "outside_booking_window"
    assert submit(writer, proof()) == 1
    result = read(days=31)
    day = result.days[0]
    assert day.status == "provisional" and len(day.slots) == 3 and day.confirmation_revision == 1
    assert day.deadline is not None and result.doctor_revision == candidate.revision
    assert not any(day.slots for day in result.days[1:])


@pytest.mark.parametrize("kind", ["signature", "evidence", "reviewer", "organization", "scope", "malformed"])
def test_bk02_untrusted_submissions(kind, slots):
    _, writer, proof, read, _ = slots
    value = proof(**({"reviewer": uuid4()} if kind == "reviewer" else
                    {"organization": "other"} if kind == "organization" else
                    {"hospital_id": uuid4()} if kind == "scope" else {}))
    if kind == "signature":
        value = value.model_copy(update={"signature": "0"*128})
    document = b'{' if kind == "malformed" else value.model_dump_json().encode()
    with pytest.raises(ConfirmationDenied):
        writer.submit(document, b"bad" if kind == "evidence" else EVIDENCE)
    assert not read().days[0].slots


@pytest.mark.parametrize("state,expected", [("closure", "closed_by_exception"), ("leave", "closed_by_exception"),
                                             ("holiday", "closed_by_exception"), ("withdrawn", "schedule_withdrawn"),
                                             ("unavailable", "provider_unavailable")])
def test_bk02_newer_states_override_working(state, expected, slots):
    _, writer, proof, read, _ = slots
    submit(writer, proof())
    submit(writer, proof(sequence=2, source_version=2, state=state, sessions=()))
    assert read().days[0].status == expected and not read().days[0].slots
    with pytest.raises(ConfirmationDenied):
        submit(writer, proof())


@pytest.mark.parametrize("kind", ["doctor_revision", "hospital_revision", "schedule_digest", "sessions", "revoked"])
def test_bk02_mismatched_or_revoked_proof(kind, slots):
    _, writer, proof, read, registry = slots
    overrides = {"doctor_revision": 1, "hospital_revision": 2, "schedule_digest": "0"*64,
                 "sessions": (WeeklySession(weekday=0, start=time(11), end=time(12), minutes=15),)}
    if kind == "doctor_revision":
        # A future or fabricated revision has no matching immutable LOC-05 record.
        with pytest.raises(ConfirmationDenied):
            submit(writer, proof(doctor_revision=2))
    elif kind == "sessions":
        session, = overrides[kind]
        from roghyar.doctor_schedules import SessionHours
        submit(writer, proof(sessions=(SessionHours(start=session.start, end=session.end, minutes=session.minutes),)))
    elif kind == "revoked":
        current = proof()
        submit(writer, current)
        assert read().days[0].slots
        registry.view = TrustSnapshot(registry.view.authorities, revoked=frozenset({current.reviewer}))
    else:
        submit(writer, proof(**{kind: overrides[kind]}))
    assert read().days[0].status == "schedule_unconfirmed"


@pytest.mark.parametrize("state,blocked", [("pending", True), ("confirmed", True), ("cancelled", False),
                                            ("completed", False), ("rescheduled", False), ("no_show", False)])
def test_bk02_interval_occupancy_and_privacy(state, blocked, slots, doctors):
    candidate, writer, proof, read, _ = slots
    submit(writer, proof())
    first = read().days[0].slots[0]
    doctors.appointment(candidate, first.start_utc + timedelta(minutes=10),
                        first.end_utc + timedelta(minutes=10), state)
    result = read()
    assert [item.status == "occupied" for item in result.days[0].slots] == [blocked, blocked, False]
    assert "patient" not in result.model_dump_json() and "appointment" not in result.model_dump_json()


def test_bk02_concurrent_append_and_restricted_roles(slots, doctors):
    _, writer, proof, read, _ = slots
    barrier = Barrier(2, timeout=5)

    def publish(_):
        barrier.wait()
        try:
            return submit(writer, proof())
        except ConfirmationDenied as exc:
            return str(exc)

    with ThreadPoolExecutor(2) as pool:
        assert set(pool.map(publish, range(2))) == {1, "stale_confirmation"}
    assert read().days[0].confirmation_revision == 1
    for db, statement in ((doctors.runtime, "DELETE FROM roghyar.slot_confirmations"),
                          (writer.engine, "UPDATE roghyar.slot_confirmations SET proof='{}'"),
                          (doctors.runtime, "SELECT patient_id FROM roghyar.appointments"),
                          (writer.engine, "SELECT patient_id FROM roghyar.appointments")):
        if statement.startswith("SELECT") and db is doctors.runtime:
            with db.begin() as connection:
                assert connection.execute(text(statement)).all() == []
            continue
        with pytest.raises(DBAPIError), db.begin() as connection:
            connection.execute(text(statement))


def test_bk02_snapshot_expiry_and_provider_failure(slots, doctors):
    _, writer, proof, read, registry = slots

    class Unavailable:
        def retrieve(self):
            raise TimeoutError("synthetic secret")

    with pytest.raises(ConfirmationDenied, match="^provider_unavailable$"):
        writer.synchronize(Unavailable())
    assert read().days[0].status == "schedule_unconfirmed"
    start = doctors.now
    submit(writer, proof(expires_at=start + timedelta(seconds=1)))
    assert read().days[0].slots
    ticks = iter((start, start + timedelta(seconds=1)))
    search = doctors.read(clock=lambda: next(ticks))
    service = BookingCalendar(search, registry)
    signed = proof()
    result = service.calendar(CalendarQuery(doctor_id=signed.doctor_id, hospital_id=signed.hospital_id, start=signed.day))
    assert result.status == "snapshot_expired" and not result.days
    doctors.now += timedelta(seconds=2)
    assert read().days[0].status == "schedule_expired"


def test_bk02_public_routes_closed():
    client = TestClient(app)
    for path in ("/doctors", "/api/v1/doctors"):
        assert client.get(f"{path}/{uuid4()}/slots", params={"date": "2030-02-01"}).status_code == 404
    assert client.post("/api/v1/appointments", json={}).status_code == 404
