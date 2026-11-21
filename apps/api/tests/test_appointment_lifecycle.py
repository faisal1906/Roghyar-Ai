"""SYNTHETIC DEVELOPMENT ONLY. Lifecycle through restricted PostgreSQL and live Redis."""
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
import json
import os
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import DBAPIError, OperationalError

from directory_fixtures import directory_actors, governed_fixtures
from test_booking_reservations import OTHER, PHONE, booking, new_key
from test_booking_slots import slots, submit
from test_database_security import database
from test_doctor_governance import doctor_actors, doctors
from test_otp_service import otp
from roghyar.appointment_lifecycle import AppointmentLifecycle, HistoryQuery, LifecycleDenied, Mutation
from roghyar.booking_reservations import BookingDenied
from roghyar.lifecycle_http import private_lifecycle_router
from roghyar.main import app, create_app, engine
from roghyar.otp_service import normalize_phone

ORIGIN = "https://synthetic-lifecycle.example.invalid"
CANCEL = Mutation(action="cancel")


class Lifecycle:
    def __init__(self, booking, writer):
        self.booking, self.writer = booking, writer
        self.key = os.urandom(32)
        self.service = self.worker()

    def worker(self, **kwargs):
        return AppointmentLifecycle(self.writer, self.booking.service, idempotency_key=self.key, **kwargs)

    def tokens(self, phone=PHONE):
        created = self.booking.token(phone)
        self.booking.otp.clear_resend(normalize_phone(phone))
        return created, self.booking.token(phone, "appointments")

    def target(self, i=1):
        return Mutation(action="reschedule", new_slot_start=self.booking.intervals[i].start_utc)

    def count(self):
        with engine.begin() as conn:
            return conn.scalar(text("""
                SELECT count(*) FROM roghyar.appointment_lifecycle_events e
                JOIN roghyar.appointments a ON e.appointment_id=a.id WHERE a.doctor_id=:doctor
            """), {"doctor": self.booking.doctor.doctor_id})


@pytest.fixture
def lifecycle(booking):
    with engine.begin() as conn:
        conn.execute(text("ALTER ROLE roghyar_appointment_writer PASSWORD 'synthetic-lifecycle-local'"))
    writer = create_engine(engine.url.set(username="roghyar_appointment_writer", password="synthetic-lifecycle-local"),
                           hide_parameters=True)
    fixture = Lifecycle(booking, writer)
    try:
        yield fixture
    finally:
        writer.dispose()
        with engine.begin() as conn:
            conn.execute(text("""
                DELETE FROM roghyar.appointment_lifecycle_events e USING roghyar.appointments a
                WHERE a.id=e.appointment_id AND a.doctor_id=:doctor
            """), {"doctor": booking.doctor.doctor_id})


@pytest.mark.parametrize("raw", [
    {"action":"cancel", "new_slot_start":None}, {"action":"reschedule"}, {"action":"confirm"},
    {"action":"cancel", "patient_id":"other"}, {"action":"reschedule","new_slot_start":"2030-01-02T09:00:00"},
    {"action":"reschedule","new_slot_start":"2030-01-02T09:00:01Z"},
], ids=("cancel_slot","missing_target","action","identity","naive","precision"))
def test_bk04_mutation_validation(raw):
    assert Mutation.model_validate_json('{"action":"cancel"}') == CANCEL
    with pytest.raises(ValidationError):
        Mutation.model_validate_json(json.dumps(raw))


def test_bk04_history_ownership_cursor_and_withdrawal(lifecycle, booking, governed_fixtures):
    first_token, mine = lifecycle.tokens()
    other_token, other = lifecycle.tokens(OTHER)
    assert not lifecycle.service.mine(mine, HistoryQuery()).appointments
    creation_key = new_key()
    own = booking.reserve(first_token, key=creation_key)
    victim = booking.reserve(other_token, i=2, phone=OTHER)
    moved = lifecycle.service.mutate(mine, own.id, lifecycle.target(), new_key())
    governed_fixtures.services["operator"].withdraw(own.hospital_id)
    assert booking.reserve(first_token, key=creation_key) == own
    first = lifecycle.service.mine(mine, HistoryQuery(limit=1))
    second = lifecycle.service.mine(mine, HistoryQuery(limit=1, before_id=first.before_id,
                                                      before_start=first.before_start))
    assert first.appointments[0].id == moved.successor.id and first.appointments[0].predecessor_id == own.id
    assert second.appointments[0].id == own.id and second.appointments[0].successor_id == moved.successor.id
    assert second.appointments[0].status == "rescheduled" and second.before_id is None
    assert first.appointments[0].provider_eligibility == "not_evaluated"
    assert first.appointments[0].local_start.utcoffset() == timedelta(hours=5)
    assert [item.id for item in lifecycle.service.mine(other, HistoryQuery()).appointments] == [victim.id]
    for invalid in (victim.id, uuid4()):
        with pytest.raises(LifecycleDenied, match="^not_found$"):
            lifecycle.service.mutate(mine, invalid, CANCEL, new_key())
    cancelled = lifecycle.service.mutate(mine, moved.successor.id, CANCEL, new_key())
    assert cancelled.appointment.status == "cancelled"


@pytest.mark.parametrize("state,allowed", [("pending",True),("confirmed",True),("completed",False),
                                            ("no_show",False),("cancelled",False),("rescheduled",False)])
def test_bk04_state_machine(state, allowed, lifecycle, booking):
    create, manage = lifecycle.tokens()
    original = booking.reserve(create)
    if state != "pending":
        with engine.begin() as conn:
            conn.execute(text("UPDATE roghyar.appointments SET status=:status WHERE id=:id"),
                         {"status": state, "id": original.id})
    key = new_key()
    if allowed:
        result = lifecycle.service.mutate(manage, original.id, CANCEL, key)
        assert result.appointment.status == "cancelled" and result.successor is None
        assert lifecycle.worker().mutate(manage, original.id, CANCEL, key) == result
        assert lifecycle.count() == 1
        with pytest.raises(LifecycleDenied, match="idempotency_conflict"):
            lifecycle.service.mutate(manage, original.id, lifecycle.target(), key)
        with pytest.raises(LifecycleDenied, match="invalid_transition"):
            lifecycle.service.mutate(manage, original.id, CANCEL, new_key())
        assert booking.reserve(create).id != original.id
    else:
        with pytest.raises(LifecycleDenied, match="invalid_transition"):
            lifecycle.service.mutate(manage, original.id, CANCEL, key)
        assert lifecycle.count() == 0


@pytest.mark.parametrize("action", ["cancel", "reschedule"])
def test_bk04_original_creation_replay_and_successor(action, lifecycle, booking):
    create, manage = lifecycle.tokens()
    key = new_key()
    original = booking.reserve(create, key=key)
    target = lifecycle.target() if action == "reschedule" else CANCEL
    mutation_key = new_key()
    current = lifecycle.service.mutate(manage, original.id, target, mutation_key)
    assert current.appointment.status == ("rescheduled" if action == "reschedule" else "cancelled")
    assert lifecycle.service.mutate(manage, original.id, target, mutation_key) == current
    assert booking.reserve(create, key=key) == original
    if action == "reschedule":
        assert current.successor.status == "pending" and current.successor.booking_code != original.booking_code
        with pytest.raises(LifecycleDenied, match="idempotency_conflict"):
            lifecycle.service.mutate(manage, original.id, lifecycle.target(2), mutation_key)
        assert booking.reserve(create).id != original.id
    assert lifecycle.count() == 1


@pytest.mark.parametrize("race", ["same_cancel", "same_reschedule", "two_patients", "cancel_vs_reschedule"])
def test_bk04_concurrent_lifecycle(race, lifecycle, booking):
    create, manage = lifecycle.tokens()
    first = booking.reserve(create)
    second = first
    different = manage
    if race == "two_patients":
        theirs, different = lifecycle.tokens(OTHER)
        second = booking.reserve(theirs, i=2, phone=OTHER)
    key = new_key()
    barrier = Barrier(2, timeout=5)

    def worker(i):
        api = lifecycle.worker()
        mutation = CANCEL if race == "same_cancel" or (race == "cancel_vs_reschedule" and i == 0) else lifecycle.target()
        barrier.wait()
        try:
            return api.mutate(different if i else manage, second.id if i else first.id,
                              mutation, key if race.startswith("same_") else new_key())
        except LifecycleDenied as exc:
            return str(exc)

    with ThreadPoolExecutor(2) as pool:
        outcomes = list(pool.map(worker, (0, 1)))
    assert lifecycle.count() == 1
    if race.startswith("same_"):
        assert outcomes[0] == outcomes[1] and not isinstance(outcomes[0], str)
    else:
        a, = [item for item in outcomes if not isinstance(item, str)]
        denial, = [item for item in outcomes if isinstance(item, str)]
        assert denial in ({"slot_unavailable"} if race == "two_patients" else {"invalid_transition"})
        if race == "two_patients":
            losing_id = second.id if a.appointment.id == first.id else first.id
            with engine.begin() as conn:
                assert conn.scalar(text("SELECT status FROM roghyar.appointments WHERE id=:id"),
                                   {"id": losing_id}) == "pending"


@pytest.mark.parametrize("committed", [True, False])
def test_bk04_uncertain_commit_recovery(committed, lifecycle, booking):
    create, manage = lifecycle.tokens()
    original = booking.reserve(create)
    key = new_key()

    def fail(conn):
        if committed:
            conn.connection.driver_connection.commit()
        else:
            conn.connection.driver_connection.rollback()
        raise OperationalError(None, None, RuntimeError("synthetic disconnect"), hide_parameters=True)

    event.listen(lifecycle.service.engine, "commit", fail)
    try:
        with pytest.raises(LifecycleDenied, match="lifecycle_outcome_unknown"):
            lifecycle.service.mutate(manage, original.id, lifecycle.target(), key)
    finally:
        event.remove(lifecycle.service.engine, "commit", fail)
    assert lifecycle.count() == int(committed)
    result = lifecycle.worker().mutate(manage, original.id, lifecycle.target(), key)
    assert result.successor and lifecycle.count() == 1


def test_bk04_rollback_after_target_insert(lifecycle, booking):
    create, manage = lifecycle.tokens()
    original = booking.reserve(create)
    key = new_key()

    def fail(conn, cursor, statement, parameters, context, executemany):
        if "INSERT INTO roghyar.appointment_lifecycle_events" in statement:
            raise OperationalError(None, None, RuntimeError("synthetic rollback"), hide_parameters=True)

    event.listen(lifecycle.service.engine, "before_cursor_execute", fail)
    try:
        with pytest.raises(LifecycleDenied, match="lifecycle_unavailable"):
            lifecycle.service.mutate(manage, original.id, lifecycle.target(), key)
    finally:
        event.remove(lifecycle.service.engine, "before_cursor_execute", fail)
    history = lifecycle.service.mine(manage, HistoryQuery())
    assert [item.status for item in history.appointments] == ["pending"] and lifecycle.count() == 0
    with pytest.raises(BookingDenied, match="slot_unavailable"):
        booking.reserve(create)
    assert lifecycle.service.mutate(manage, original.id, lifecycle.target(), key).successor


@pytest.mark.parametrize("reason", ["wrong_purpose","revoked","expired","cutoff","closure","target_busy","target_overlap"])
def test_bk04_boundary_denials(reason, lifecycle, booking):
    create, manage = lifecycle.tokens()
    original = booking.reserve(create)
    worker, mutation = lifecycle.service, lifecycle.target()
    expected = "unauthenticated"
    if reason == "wrong_purpose":
        manage = create
    elif reason == "revoked":
        booking.otp.service.revoke(manage, "appointments")
    elif reason == "expired":
        booking.otp.now += timedelta(minutes=11)
    elif reason == "cutoff":
        worker = lifecycle.worker(clock=lambda: original.slot_start - timedelta(minutes=30))
        mutation, expected = CANCEL, "cutoff_passed"
    elif reason == "closure":
        submit(booking.confirmation_writer, booking.proof(state="closure", sessions=(), sequence=2, source_version=2))
        expected = "slot_unavailable"
    elif reason == "target_busy":
        booking.reserve(create, i=1)
        expected = "slot_unavailable"
    elif reason == "target_overlap":
        target = booking.intervals[1]
        booking.doctors.appointment(booking.doctor, target.start_utc + timedelta(minutes=1),
                                    target.end_utc - timedelta(minutes=1), "confirmed")
        expected = "slot_unavailable"
    with pytest.raises(LifecycleDenied, match=f"^{expected}$"):
        worker.mutate(manage, original.id, mutation, new_key())
    assert lifecycle.count() == 0
    with engine.begin() as conn:
        assert conn.scalar(text("SELECT status FROM roghyar.appointments WHERE id=:id"), {"id": original.id}) == "pending"


def test_bk04_session_capability_and_commit_guard(lifecycle, booking, database):
    create, manage = lifecycle.tokens()
    original = booking.reserve(create)
    identity, _, _ = booking.otp.service.decode_credential(manage, "appointments")
    with lifecycle.writer.begin() as conn:
        conn.execute(text("SELECT set_config('roghyar.management_session',:id,true)"), {"id": str(identity.session)})
        conn.execute(text("SELECT set_config('roghyar.subject',:id,true)"), {"id": str(identity.subject)})
        assert conn.scalar(text("SELECT count(id) FROM roghyar.appointments")) == 0
        conn.execute(text("SELECT set_config('roghyar.management_proof','fake',true)"))
        assert conn.scalar(text("SELECT count(id) FROM roghyar.appointments")) == 0
    with pytest.raises(DBAPIError, match="Lifecycle transition requires history"), lifecycle.writer.begin() as conn:
        conn.execute(text("SELECT set_config('roghyar.management_session',:id,true), "
                          "set_config('roghyar.management_proof',:proof,true)"),
                     {"id": str(identity.session), "proof": booking.otp.service.management_proof(identity.session)})
        assert conn.scalar(text("SELECT count(id) FROM roghyar.appointments")) == 1
        conn.execute(text("UPDATE roghyar.appointments SET status='cancelled' WHERE id=:id"), {"id": original.id})
    for connection, statement in ((database,"SELECT * FROM roghyar.appointment_lifecycle_events"),
                                  (lifecycle.writer,"DELETE FROM roghyar.appointment_lifecycle_events"),
                                  (lifecycle.writer,"UPDATE roghyar.appointments SET doctor_id=doctor_id"),
                                  (lifecycle.writer,"SELECT reason_ciphertext FROM roghyar.appointments"),
                                  (lifecycle.writer,"SELECT * FROM roghyar.otp_sessions"),
                                  (lifecycle.writer,"SELECT * FROM roghyar.assessments")):
        with pytest.raises(DBAPIError), connection.begin() as conn:
            conn.execute(text(statement))


def test_bk04_private_http(lifecycle, booking, caplog):
    create, manage = lifecycle.tokens()
    original = booking.reserve(create)
    normal = TestClient(app)
    assert normal.get("/appointments/mine").status_code == 404
    assert normal.patch(f"/api/v1/appointments/{original.id}", json={}).status_code == 404
    private = create_app(production=False)
    private.include_router(private_lifecycle_router(lifecycle.service, origin=ORIGIN))
    client = TestClient(private, base_url=ORIGIN)
    headers = {"Origin": ORIGIN, "Authorization": f"Bearer {manage}", "Idempotency-Key": new_key()}
    path = f"/appointments/{original.id}"
    assert client.get("/appointments/mine", headers={"Origin": ORIGIN}).status_code == 401
    assert client.get("/appointments/mine?limit=1&limit=2", headers=headers).status_code == 422
    assert client.get("/appointments/mine?patient_id=forged", headers=headers).status_code == 422
    first = client.get("/appointments/mine", headers=headers)
    assert first.json()["appointments"][0]["id"] == str(original.id) and first.headers["cache-control"] == "no-store"
    assert client.patch(path, json={"action":"cancel"}, headers=headers | {"Origin":"https://other.invalid"}).status_code == 403
    assert client.patch(path, json={"action":"cancel"}, headers=headers | {"Idempotency-Key":"bad"}).status_code == 422
    assert client.patch(path, json={"action":"x"*700}, headers=headers).status_code == 413
    assert client.patch(path, content='{"action":"cancel","action":"reschedule"}',
                        headers=headers | {"Content-Type":"application/json"}).status_code == 422
    cancelled = client.patch(path, json={"action":"cancel"}, headers=headers)
    assert cancelled.json()["appointment"]["status"] == "cancelled" and cancelled.headers["cache-control"] == "no-store"
    assert client.patch(path, json={"action":"cancel"}, headers=headers).json() == cancelled.json()
    assert manage not in caplog.text and PHONE not in caplog.text


@pytest.mark.parametrize("winner", ["revocation", "withdrawal", "replacement"])
def test_bk04_authority_races(winner, lifecycle, booking, doctors, governed_fixtures):
    create, manage = lifecycle.tokens()
    original = booking.reserve(create)
    revised = None
    if winner == "replacement":
        revised = doctors.candidate(doctor_id=booking.doctor.doctor_id, registration=booking.doctor.registration,
                                    revision=2, schedule=booking.doctor.schedule)
        doctors.stage(revised)
        for role in ("credential", "schedule"):
            doctors.approve(revised, role)
    called = False

    def change(conn, cursor, statement, parameters, context, executemany):
        nonlocal called
        needle = ("SELECT * FROM roghyar.appointment_identity()" if winner == "revocation"
                  else "pg_advisory_xact_lock_shared(79350919)")
        if called or needle not in statement:
            return
        called = True
        if winner == "revocation":
            booking.otp.service.revoke(manage, "appointments")
        elif winner == "withdrawal":
            governed_fixtures.services["operator"].withdraw(original.hospital_id)
        else:
            doctors.publish(revised)

    event.listen(lifecycle.service.engine, "before_cursor_execute", change)
    try:
        code = {"revocation":"unauthenticated", "withdrawal":"slot_unavailable",
                "replacement":"schedule_authority_unavailable"}[winner]
        with pytest.raises(LifecycleDenied, match=f"^{code}$"):
            lifecycle.service.mutate(manage, original.id, lifecycle.target(), new_key())
    finally:
        event.remove(lifecycle.service.engine, "before_cursor_execute", change)
    assert called and lifecycle.count() == 0 and booking.counts() == (1, 1, 1)


def test_bk04_revocation_waits_for_locked_authority(lifecycle, booking):
    create, manage = lifecycle.tokens()
    original = booking.reserve(create)
    identity = booking.otp.service.authorize(manage, "appointments")
    blocked = False

    def observe(conn, cursor, statement, parameters, context, executemany):
        nonlocal blocked
        if blocked or "INSERT INTO roghyar.appointment_lifecycle_events" not in statement:
            return
        blocked = True
        with pytest.raises(DBAPIError) as denied, booking.otp.writer.begin() as otp_conn:
            otp_conn.execute(text("SET LOCAL lock_timeout='150ms'"))
            otp_conn.execute(text("UPDATE roghyar.otp_sessions SET revoked_at=now() WHERE id=:id"),
                             {"id": identity.session})
        assert denied.value.orig.sqlstate == "55P03"

    event.listen(lifecycle.service.engine, "after_cursor_execute", observe)
    try:
        assert lifecycle.service.mutate(manage, original.id, CANCEL, new_key()).appointment.status == "cancelled"
    finally:
        event.remove(lifecycle.service.engine, "after_cursor_execute", observe)
    assert blocked
    booking.otp.service.revoke(manage, "appointments")
    with pytest.raises(LifecycleDenied, match="^unauthenticated$"):
        lifecycle.service.mine(manage, HistoryQuery())


def test_bk04_nonempty_downgrade_guard(lifecycle, booking):
    import importlib
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    create, manage = lifecycle.tokens()
    original = booking.reserve(create)
    lifecycle.service.mutate(manage, original.id, CANCEL, new_key())
    migration = importlib.import_module("migrations.versions.0014_appointment_lifecycle")
    with pytest.raises(DBAPIError, match="Appointment lifecycle history requires approved archival"), engine.begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            migration.downgrade()
    assert lifecycle.count() == 1 and lifecycle.service.mine(manage, HistoryQuery()).appointments[0].status == "cancelled"


def seed_history(appointment, count):
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO roghyar.appointments(id,booking_code,doctor_id,hospital_id,patient_id,
                slot_start,slot_end,status,consent_at)
            SELECT gen_random_uuid(),'SYNTHETIC-HISTORY-' || n, a.doctor_id,a.hospital_id,a.patient_id,
                a.slot_start-(n / 2 + 1)*interval '1 day', a.slot_end-(n / 2 + 1)*interval '1 day','completed',a.consent_at
            FROM roghyar.appointments a CROSS JOIN generate_series(1,:count) n WHERE a.id=:id
        """), {"id": appointment, "count": count})


def test_bk04_large_history_and_live_performance(lifecycle, booking):
    from statistics import median
    from time import perf_counter

    create, manage = lifecycle.tokens()
    original = booking.reserve(create)
    timings = {}

    def measure(name, callback, count):
        durations = []
        for _ in range(count):
            start = perf_counter()
            callback()
            durations.append((perf_counter()-start)*1000)
        timings[name] = (count, median(durations), max(durations))

    measure("history", lambda: lifecycle.service.mine(manage, HistoryQuery(limit=5)), 10)
    seed_history(original.id, 120)
    page = HistoryQuery(limit=10)
    first = lifecycle.service.mine(manage, page)
    second = HistoryQuery(limit=10, before_start=first.before_start, before_id=first.before_id)
    measure("page", lambda: lifecycle.service.mine(manage, second), 10)
    seen = []
    while True:
        batch = lifecycle.service.mine(manage, page)
        assert len(batch.appointments) <= 10
        seen.extend((row.slot_start, row.id) for row in batch.appointments)
        if batch.before_id is None:
            break
        page = HistoryQuery(limit=10, before_start=batch.before_start, before_id=batch.before_id)
    assert len(seen) == len(set(seen)) == 121 and seen == sorted(seen, reverse=True)
    with engine.begin() as conn:
        conn.execute(text("SET LOCAL enable_seqscan=off"))
        lines = conn.execute(text("""
            EXPLAIN SELECT id FROM roghyar.appointments WHERE patient_id=(
              SELECT patient_id FROM roghyar.appointments WHERE id=:id)
            ORDER BY slot_start DESC,id DESC LIMIT 10
        """), {"id": original.id}).scalars()
        assert any("ix_appointments_lifecycle_page" in line for line in lines)

    cancel_key = new_key()
    measure("cancel", lambda: lifecycle.service.mutate(manage, original.id, CANCEL, cancel_key), 1)
    measure("cancel_replay", lambda: lifecycle.service.mutate(manage, original.id, CANCEL, cancel_key), 10)
    left = booking.reserve(create)
    right = booking.reserve(create, i=2)
    barrier = Barrier(2, timeout=5)
    keys = {id: new_key() for id in (left.id, right.id)}

    def compete(appointment):
        worker = lifecycle.worker()
        barrier.wait()
        start = perf_counter()
        try:
            result = worker.mutate(manage, appointment, lifecycle.target(), keys[appointment])
        except LifecycleDenied as exc:
            assert str(exc) == "slot_unavailable"
            result = None
        return result, (perf_counter()-start)*1000

    with ThreadPoolExecutor(2) as pool:
        attempts = list(pool.map(compete, keys))
    won, = [(result, duration) for result, duration in attempts if result is not None]
    lost, = [duration for result, duration in attempts if result is None]
    timings["reschedule"] = (1, won[1], won[1])
    timings["conflict"] = (1, lost, lost)
    timings["parallel"] = (2, median(duration for _, duration in attempts), max(duration for _, duration in attempts))
    measure("reschedule_replay", lambda: lifecycle.service.mutate(
        manage, won[0].appointment.id, lifecycle.target(), keys[won[0].appointment.id]), 10)
    print("\nBK04 measurements " + " ".join(f"{name}={n},{p50:.3f},{p95:.3f}"
          for name, (n,p50,p95) in timings.items()))


def test_bk04_cutoff_and_key_isolation(lifecycle, booking):
    create, manage = lifecycle.tokens()
    theirs, other = lifecycle.tokens(OTHER)
    own = booking.reserve(create)
    victim = booking.reserve(theirs, i=2, phone=OTHER)
    clock = lambda: own.slot_start - timedelta(minutes=30, microseconds=1)
    worker = lifecycle.worker(clock=clock)
    key = new_key()
    assert worker.mutate(manage, own.id, CANCEL, key).appointment.id == own.id
    with pytest.raises(LifecycleDenied, match="^not_found$"):
        worker.mutate(other, own.id, CANCEL, key)
    assert worker.mutate(other, victim.id, CANCEL, key).appointment.id == victim.id
    assert lifecycle.count() == 2
