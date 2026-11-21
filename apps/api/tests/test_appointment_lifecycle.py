"""SYNTHETIC DEVELOPMENT ONLY. Lifecycle through restricted PostgreSQL and live Redis."""
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
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
        Mutation.model_validate_json(__import__("json").dumps(raw))


def test_bk04_history_ownership_cursor_and_withdrawal(lifecycle, booking, governed_fixtures):
    first_token, mine = lifecycle.tokens()
    other_token, other = lifecycle.tokens(OTHER)
    assert not lifecycle.service.mine(mine, HistoryQuery()).appointments
    own = booking.reserve(first_token)
    victim = booking.reserve(other_token, i=2, phone=OTHER)
    moved = lifecycle.service.mutate(mine, own.id, lifecycle.target(), new_key())
    governed_fixtures.services["operator"].withdraw(own.hospital_id)
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
    with pytest.raises(Exception, match="slot_unavailable"):
        booking.reserve(create)
    assert lifecycle.service.mutate(manage, original.id, lifecycle.target(), key).successor


@pytest.mark.parametrize("reason", ["wrong_purpose","revoked","expired","cutoff","closure","target_busy"])
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
