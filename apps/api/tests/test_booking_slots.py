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


@pytest.mark.parametrize("change", ["closure", "appointment", "cancel", "doctor_withdrawal",
                                     "revision", "source_revocation", "hospital_withdrawal"])
def test_bk02_atomic_read_snapshot(change, slots, doctors, governed_fixtures):
    from sqlalchemy import event

    candidate, writer, proof, read, registry = slots
    submit(writer, proof())
    initial = read()
    slot = initial.days[0].slots[0]
    appointment = (doctors.appointment(candidate, slot.start_utc, slot.end_utc, "pending")
                   if change == "cancel" else None)
    revised = None
    if change == "revision":
        revised = doctors.candidate(doctor_id=candidate.doctor_id, registration=candidate.registration,
                                    revision=2, schedule=candidate.schedule)
        doctors.stage(revised)
        for kind in ("credential", "schedule"):
            doctors.approve(revised, kind)
    search = doctors.read()
    executed = False

    def update_after_snapshot(conn, cursor, statement, parameters, context, executemany):
        nonlocal executed
        if executed or "SELECT c.revision,c.hospital_revision" not in statement:
            return
        executed = True
        if change == "closure":
            submit(writer, proof(state="closure", sessions=(), sequence=2, source_version=2))
        elif change == "appointment":
            doctors.appointment(candidate, slot.start_utc, slot.end_utc, "pending")
        elif change == "cancel":
            with engine.begin() as owner:
                owner.execute(text("UPDATE roghyar.appointments SET status='cancelled' WHERE id=:id"),
                              {"id": appointment})
        elif change == "doctor_withdrawal":
            doctors.services["operator"].withdraw(candidate.doctor_id, candidate.hospital_id, candidate.revision)
        elif change == "hospital_withdrawal":
            governed_fixtures.services["operator"].withdraw(candidate.hospital_id)
        elif change == "revision":
            doctors.publish(revised)
        else:
            registry.view = TrustSnapshot(registry.view.authorities,
                                          withdrawn_sources=frozenset({(candidate.hospital_id, "synthetic_provider")}))

    event.listen(search.engine, "after_cursor_execute", update_after_snapshot)
    try:
        snapshot = BookingCalendar(search, registry).calendar(CalendarQuery(
            doctor_id=candidate.doctor_id, hospital_id=candidate.hospital_id, start=proof().day))
    finally:
        event.remove(search.engine, "after_cursor_execute", update_after_snapshot)
    assert executed
    latest = read()
    if change == "source_revocation":
        assert snapshot.status == "provider_unavailable" and latest.days[0].status == "schedule_unconfirmed"
    else:
        assert snapshot.doctor_revision == 1 and snapshot.days[0].confirmation_revision == 1
        assert snapshot.days[0].slots[0].status == ("occupied" if change == "cancel" else "provisional_unoccupied")
        if change in ("appointment", "cancel"):
            assert latest.days[0].slots[0].status == ("occupied" if change == "appointment" else "provisional_unoccupied")
        elif change in ("doctor_withdrawal", "hospital_withdrawal"):
            assert latest.status == "ineligible"
        else:
            assert latest.days[0].status == ("closed_by_exception" if change == "closure" else "schedule_unconfirmed")


def test_bk02_replacement_exact_binding(slots, doctors):
    candidate, writer, proof, read, _ = slots
    day = proof().day
    from roghyar.doctor_schedules import SessionHours

    replacement = (SessionHours(start=time(11), end=time(12), minutes=25),)
    updated = candidate.schedule.model_copy(update={"exceptions": (
        ScheduleException(start_date=day, end_date=day, action="replacement", sessions=replacement),)})
    revised = doctors.candidate(doctor_id=candidate.doctor_id, registration=candidate.registration,
                                revision=2, schedule=updated)
    doctors.activate(revised)
    assert read().days[0].status == "schedule_unconfirmed"
    submit(writer, proof(doctor_revision=2, schedule_digest=fingerprint(updated.model_dump(mode="json")),
                         state="replacement", sessions=replacement))
    result = read().days[0]
    assert result.status == "provisional" and len(result.slots) == 2 and result.slots[0].start_local.hour == 11


def test_bk02_live_calendar_measurements(slots, doctors):
    from statistics import median
    from time import perf_counter

    candidate, writer, proof, read, _ = slots
    schedule = candidate.schedule
    for offset in range(31):
        day = proof().day + timedelta(days=offset)
        sessions = working_sessions(schedule, day)
        if sessions:
            submit(writer, proof(day=day, sessions=sessions))
    outputs = {}

    def measure(name, days):
        spans = []
        for _ in range(10):
            start = perf_counter()
            result = read(days=days)
            spans.append((perf_counter() - start) * 1000)
            assert result.status == "calendar" and len(result.days) == days
        outputs[name] = (len(spans), median(spans), sorted(spans)[-1])

    measure("one", 1)
    measure("seven", 7)
    measure("full", 31)
    for offset in (1, 2, 3):
        submit(writer, proof(day=proof().day + timedelta(days=offset), state="closure", sessions=()))
    measure("exceptions", 7)
    for day in read(days=31).days:
        for slot in day.slots:
            doctors.appointment(candidate, slot.start_utc, slot.end_utc, "confirmed")
    measure("occupied", 31)
    start = perf_counter()
    with ThreadPoolExecutor(4) as pool:
        spans = list(pool.map(lambda _: timed_read(read), range(12)))
    assert perf_counter() >= start and len(spans) == 12
    outputs["parallel"] = (len(spans), median(spans), sorted(spans)[-1])
    with doctors.runtime.begin() as conn:
        conn.execute(text("SET LOCAL enable_seqscan=off"))
        plan = conn.execute(text("""
            EXPLAIN SELECT sequence FROM roghyar.slot_confirmations
            WHERE doctor_id=:doctor AND hospital_id=:hospital AND day=:day ORDER BY sequence DESC LIMIT 1
        """), {"doctor": candidate.doctor_id, "hospital": candidate.hospital_id, "day": proof().day}).scalars()
        assert any("slot_confirmations_pkey" in line for line in plan)
    print("BK02 measurements " + " ".join(f"{name}={n},{p50:.3f},{p95:.3f}"
          for name, (n, p50, p95) in outputs.items()))


def timed_read(read):
    from time import perf_counter

    start = perf_counter()
    result = read(days=31)
    assert result.status == "calendar"
    return (perf_counter() - start) * 1000


@pytest.mark.parametrize("mode", ["accepted", "redirect", "wrong_type", "oversize", "timeout"])
def test_bk02_gateway_contract(mode, monkeypatch):
    import httpx
    from pydantic import SecretStr

    from roghyar.schedule_gateway import ScheduleGateway

    def respond(request):
        assert request.method == "GET" and request.headers["Authorization"] == "Bearer synthetic-token"
        if mode == "timeout":
            raise httpx.ReadTimeout("synthetic")
        raw = b'{"document":"e30=","evidence":"eA=="}' if mode != "oversize" else b"x"*100001
        return httpx.Response(302 if mode == "redirect" else 200, stream=httpx.ByteStream(raw),
                              headers={"content-type": "text/html" if mode == "wrong_type" else "application/json"})

    client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client(transport=httpx.MockTransport(respond), **kwargs))
    gateway = ScheduleGateway("https://8.8.8.8/v1/schedule-confirmation", frozenset({"8.8.8.8"}),
                              SecretStr("synthetic-token"))
    if mode == "accepted":
        assert gateway.retrieve() == (b"{}", b"x")
    else:
        with pytest.raises(ConfirmationDenied, match="^provider_unavailable$"):
            gateway.retrieve()


def test_bk02_append_rollback_and_history(slots):
    from sqlalchemy import event
    from sqlalchemy.exc import SQLAlchemyError

    _, writer, proof, read, _ = slots
    submit(writer, proof())
    changed = proof(sequence=2, source_version=2, state="closure", sessions=())

    def fail_after_insert(conn, cursor, statement, parameters, context, executemany):
        if "INSERT INTO roghyar.slot_confirmations" in statement:
            raise SQLAlchemyError("synthetic post-insert failure")

    event.listen(writer.engine, "after_cursor_execute", fail_after_insert)
    try:
        with pytest.raises(ConfirmationDenied, match="^confirmation_storage_unavailable$"):
            submit(writer, changed)
    finally:
        event.remove(writer.engine, "after_cursor_execute", fail_after_insert)
    assert read().days[0].confirmation_revision == 1
    assert submit(writer, changed) == 2
    with engine.begin() as owner:
        rows = owner.execute(text("""
            SELECT sequence,actor FROM roghyar.slot_confirmations
            WHERE doctor_id=:id ORDER BY sequence
        """), {"id": changed.doctor_id}).all()
        assert rows == [(1, "roghyar_slot_writer"), (2, "roghyar_slot_writer")]
    assert read().days[0].status == "closed_by_exception"


def test_bk02_source_sync_races_with_schedule_correction(slots, doctors):
    candidate, writer, proof, read, _ = slots
    stale = proof()
    revised = doctors.candidate(doctor_id=candidate.doctor_id, registration=candidate.registration,
                                revision=2, schedule=candidate.schedule)

    class RacingSource:
        def retrieve(self):
            doctors.activate(revised)
            return stale.model_dump_json().encode(), EVIDENCE

    writer.synchronize(RacingSource())
    day = read().days[0]
    assert day.status == "schedule_unconfirmed" and not day.slots
    submit(writer, proof(sequence=2, source_version=2, doctor_revision=2))
    fresh = read()
    assert fresh.doctor_revision == 2 and fresh.days[0].confirmation_revision == 2


def test_bk02_restricted_cross_facility_insert(slots, governed_fixtures):
    candidate, writer, proof, read, registry = slots
    hospital = governed_fixtures.candidate()
    governed_fixtures.publish(hospital)
    key, reviewer = Ed25519PrivateKey.generate(), uuid4()
    signed = proof(hospital_id=hospital.hospital_id, reviewer=reviewer)
    signed = signed.model_copy(update={"signature": key.sign(signed.signing_bytes()).hex()})
    registry.view = TrustSnapshot({reviewer: Authority(key.public_key(), hospital.hospital_id, signed.organization,
        signed.confirmed_at - timedelta(days=1), signed.expires_at)})
    with pytest.raises(ConfirmationDenied, match="^confirmation_storage_unavailable$"):
        submit(writer, signed)
    assert not read().days[0].slots
    with engine.begin() as conn:
        assert conn.scalar(text("SELECT count(*) FROM roghyar.slot_confirmations WHERE doctor_id=:id"),
                           {"id": candidate.doctor_id}) == 0
