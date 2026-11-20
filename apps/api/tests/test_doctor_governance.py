"""Synthetic-only, live LOC-05 doctor and scheduling acceptance."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from hashlib import sha256
from threading import Barrier
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient
from psycopg import sql
from pydantic import ValidationError
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from directory_fixtures import directory_actors, governed_fixtures
from roghyar.directory_governance import DirectoryDenied, Source, fingerprint
from roghyar.doctor_governance import DoctorDirectory, DoctorRevision, DoctorReview, DoctorReviewer
from roghyar.doctor_schedules import LOCAL, Schedule, ScheduleException, SessionHours, WeeklySession, provisional_slots
from roghyar.doctor_search import DISCOVERY, FREE, SCHEDULE, DoctorSearch
from roghyar.doctor_specialties import Specialty, SpecialtyVocabulary
from roghyar.main import app, engine
from roghyar.scoped_session import CredentialVerifier, scoped_session
from test_database_security import database

ROLES = ("importer", "credential", "schedule", "operator")
ORG = "synthetic_source"
EVIDENCE = {name: f"SYNTHETIC DEVELOPMENT ONLY {name}".encode()
            for name in ("credential-document", "affiliation-document", "schedule-document")}


@pytest.fixture(scope="module")
def doctor_actors(database):
    logins, conns = {}, {}
    for role in ROLES:
        login = f"synthetic_doc_{role}_{uuid4().hex[:10]}"
        logins[role] = login
        password = "synthetic-doctor-local"
        with engine.begin() as connection:
            raw = connection.connection.driver_connection
            raw.execute(sql.SQL("CREATE ROLE {} LOGIN NOINHERIT PASSWORD {}").format(sql.Identifier(login),
                                                                                   sql.Literal(password)))
            raw.execute(sql.SQL("GRANT {} TO {}").format(sql.Identifier(f"roghyar_doc_{role}"), sql.Identifier(login)))
        conns[role] = create_engine(engine.url.set(username=login, password=password), hide_parameters=True)
    try:
        yield logins, conns
    finally:
        for conn in conns.values():
            conn.dispose()
        with engine.begin() as owner:
            for login in logins.values():
                owner.connection.driver_connection.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(login)))


class DoctorFixture:
    def __init__(self, hospital, actors, runtime):
        self.hospital = hospital
        self.logins, self.connections = actors
        self.runtime = runtime
        self.now = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        self.day = self.now.astimezone(LOCAL).date()
        issued = datetime.now(UTC)
        self.taxonomy = SpecialtyVocabulary(version=1, source_ref="synthetic_taxonomy",
            reviewer_ref="synthetic_taxonomy_reviewer", expires_at=issued + timedelta(days=1), entries=(
                Specialty(key="synthetic_cardio", display="Synthetic specialty", aliases=("synthetic_heart",)),
                Specialty(key="synthetic_general", display="Synthetic second specialty"),
            ))
        self.private = {kind: Ed25519PrivateKey.generate() for kind in ("credential", "schedule")}
        self.authorities = {kind: DoctorReviewer(self.logins[kind], uuid4(), "synthetic_review_org", kind,
            frozenset({hospital.hospital_id}), frozenset({ORG}), frozenset({"synthetic_cardio", "synthetic_general"}),
            key.public_key(), issued - timedelta(hours=1), issued + timedelta(hours=12))
            for kind, key in self.private.items()}
        self.services = {kind: self.service(kind) for kind in ROLES}
        self.doctors = set()
        self.patients = set()
        self.revocations = set()

    def service(self, kind, **overrides):
        options = dict(logins=frozenset({self.logins[kind]}), reviewers=self.authorities,
                       sources=frozenset({ORG}), vocabulary=self.taxonomy)
        return DoctorDirectory(self.connections[kind], **(options | overrides))

    def candidate(self, **overrides):
        def source(reference, kind):
            return Source(organization=ORG, record="synthetic_record", version=1, kind=kind,
                          reference=reference, observed_at=datetime.now(UTC) - timedelta(seconds=2),
                          evidence_digest=sha256(EVIDENCE[reference]).hexdigest())
        school = Schedule(timezone="Asia/Karachi", effective_from=self.day, effective_until=self.day + timedelta(days=30),
            weekly=(WeeklySession(weekday=self.day.weekday(), start=time(9), end=time(10, 5), minutes=20),))
        values = dict(schema_version=1, doctor_id=uuid4(), revision=1, hospital_id=self.hospital.hospital_id,
            hospital_revision=self.hospital.revision, name="Synthetic Doctor", registry="pmdc",
            registration="SYN-" + uuid4().hex[:20].upper(), specialties=("synthetic_cardio",),
            taxonomy_version=self.taxonomy.version, taxonomy_digest=fingerprint(self.taxonomy.model_dump(mode="json")),
            professional=source("credential-document", "official_register"),
            affiliation=source("affiliation-document", "provider_attestation"),
            schedule_source=source("schedule-document", "provider_attestation"), schedule=school)
        result = DoctorRevision.model_validate(values | overrides)
        self.doctors.add(result.doctor_id)
        return result

    def proof(self, candidate, kind, **overrides):
        reviewer = self.authorities[kind]
        now = datetime.now(UTC) - timedelta(milliseconds=2)
        body = DoctorReview(**(dict(doctor_id=candidate.doctor_id, hospital_id=candidate.hospital_id,
            revision=candidate.revision, digest=candidate.digest(), kind=kind,
            evidence_digests=tuple(item.evidence_digest for item in candidate.evidence_for(kind)),
            decision="approved", reviewer_id=reviewer.reviewer_id, credential=kind, organization=reviewer.organization,
            reviewed_at=now, expires_at=now + timedelta(minutes=45), signature="0"*128) | overrides))
        return body.model_copy(update={"signature": self.private[kind].sign(body.signing_bytes()).hex()})

    def stage(self, candidate):
        return self.services["importer"].submit(candidate, EVIDENCE)

    def approve(self, candidate, kind):
        self.services[kind].review(self.proof(candidate, kind), EVIDENCE)

    def publish(self, candidate):
        self.services["operator"].publish(candidate.doctor_id, candidate.hospital_id, candidate.revision)

    def activate(self, candidate):
        self.stage(candidate)
        for kind in ("credential", "schedule"):
            self.approve(candidate, kind)
        self.publish(candidate)

    def read(self, **overrides):
        return DoctorSearch(self.runtime, **(dict(vocabulary=self.taxonomy, clock=lambda: self.now) | overrides))

    def slot_query(self, doctor, **overrides):
        return {"doctor_id": doctor.doctor_id, "hospital_id": doctor.hospital_id, "day": self.day} | overrides

    def revoke(self, kind, subject):
        self.revocations.add((kind, subject))
        self.services["operator"].revoke(self.hospital.hospital_id, kind, subject)

    def appointment(self, doctor, start, end, state):
        patient, appointment = uuid4(), uuid4()
        self.patients.add(patient)
        with engine.begin() as owner:
            owner.execute(text("""
                INSERT INTO roghyar.patients(id,auth_subject,full_name_ciphertext,phone_ciphertext,phone_lookup,
                  phone_verified_at,age,consent_at)
                VALUES(:patient,:subject,decode('00','hex'),decode('00','hex'),:lookup,now(),30,now())
            """), {"patient": patient, "subject": uuid4(), "lookup": sha256(patient.bytes).digest()})
            owner.execute(text("""
                INSERT INTO roghyar.appointments(id,booking_code,doctor_id,hospital_id,patient_id,slot_start,
                                                  slot_end,status,consent_at)
                VALUES(:id,:code,:doctor,:hospital,:patient,:start,:end,:state,now())
            """), dict(id=appointment, code=appointment.hex, doctor=doctor.doctor_id, hospital=doctor.hospital_id,
                       patient=patient, start=start, end=end, state=state))
        return appointment


@pytest.fixture
def doctors(database, governed_fixtures, doctor_actors):
    hospital = governed_fixtures.candidate()
    governed_fixtures.publish(hospital)
    fixture = DoctorFixture(hospital, doctor_actors, database)
    with engine.begin() as owner:
        for kind, login in fixture.logins.items():
            owner.execute(text("INSERT INTO roghyar.doctor_operator_scope VALUES(:login,:hospital,:kind)"),
                          {"login": login, "hospital": hospital.hospital_id, "kind": kind})
    try:
        yield fixture
    finally:
        with engine.begin() as owner:
            owner.execute(text("DELETE FROM roghyar.appointments WHERE patient_id=ANY(:ids)"), {"ids": list(fixture.patients)})
            owner.execute(text("DELETE FROM roghyar.patients WHERE id=ANY(:ids)"), {"ids": list(fixture.patients)})
            for table in ("doctor_current", "doctor_events", "doctor_reviews", "doctor_revisions"):
                owner.execute(text(f"DELETE FROM roghyar.{table} WHERE doctor_id=ANY(:ids)"), {"ids": list(fixture.doctors)})
            owner.execute(text("DELETE FROM roghyar.doctors WHERE id=ANY(:ids)"), {"ids": list(fixture.doctors)})
            for table in ("doctor_revocations", "doctor_operator_scope"):
                owner.execute(text(f"DELETE FROM roghyar.{table} WHERE hospital_id=:hospital"),
                              {"hospital": hospital.hospital_id})


@pytest.mark.parametrize("case", ["weekday", "reversed", "overnight", "slot_size", "overlap",
                                  "timezone", "range", "overlapping_exceptions", "replacement_without_sessions"])
def test_loc05_schedule_validation(case):
    day = date(2030, 1, 7)
    valid = dict(timezone="Asia/Karachi", effective_from=day, effective_until=day + timedelta(days=4),
                 weekly=(WeeklySession(weekday=day.weekday(), start=time(9), end=time(10), minutes=15),))
    assert Schedule(**valid).sessions_for(day)
    session = valid["weekly"][0]
    if case in ("weekday", "reversed", "overnight", "slot_size"):
        update = {"weekday": {"weekday": 7}, "reversed": {"end": time(8)},
                  "overnight": {"start": time(23), "end": time(1)}, "slot_size": {"minutes": 0}}
        with pytest.raises(ValidationError):
            WeeklySession.model_validate(session.model_dump() | update[case])
        return
    if case == "replacement_without_sessions":
        with pytest.raises(ValidationError):
            ScheduleException(start_date=day, end_date=day, action="replacement")
        return
    if case == "overlap":
        valid["weekly"] = (session, session)
    if case == "timezone":
        valid["timezone"] = "UTC"
    if case == "range":
        valid["effective_until"] = day - timedelta(days=1)
    if case == "overlapping_exceptions":
        valid["exceptions"] = tuple(ScheduleException(start_date=day, end_date=day, action=action)
                                    for action in ("leave", "holiday"))
    with pytest.raises(ValidationError):
        Schedule(**valid)


def test_loc05_controlled_clock_and_timezone():
    day = date(2030, 1, 7)
    schedule = Schedule(timezone="Asia/Karachi", effective_from=day, effective_until=day + timedelta(days=31),
        weekly=(WeeklySession(weekday=0, start=time(0), end=time(1, 10), minutes=30),
                WeeklySession(weekday=2, start=time(9), end=time(10), minutes=20)))
    now = datetime(2030, 1, 6, 18, 30, tzinfo=UTC)
    slots = provisional_slots(schedule, day, now, timedelta(minutes=15))
    assert [slot.start_utc.hour for slot in slots] == [19, 19]
    assert slots[0].start_local.hour == 0 and slots[-1].end_local.hour == 1
    next_midnight = datetime(2030, 1, 6, 19, tzinfo=UTC)
    assert len(provisional_slots(schedule, day, next_midnight, timedelta(minutes=15))) == 1
    assert len(provisional_slots(schedule, day + timedelta(days=30), next_midnight, timedelta())) == 3
    for delta in (-1, 1, 31):
        assert not provisional_slots(schedule, day + timedelta(days=delta), next_midnight, timedelta())


@pytest.mark.parametrize("action", ["leave", "holiday", "closure", "cancelled", "suspended", "replacement"])
def test_loc05_exception_behavior(action, doctors):
    item = doctors.candidate()
    exception = ScheduleException(start_date=doctors.day,
        end_date=doctors.day + timedelta(days=1 if action == "leave" else 0), action=action,
        sessions=(SessionHours(start=time(11), end=time(12), minutes=30),) if action == "replacement" else ())
    schedule = item.schedule.model_copy(update={"exceptions": (exception,)})
    item = item.model_copy(update={"schedule": schedule})
    doctors.activate(item)
    result = doctors.read().slots(doctors.slot_query(item))
    if action == "replacement":
        assert [slot.start_local.hour for slot in result.slots] == [11, 11]
    else:
        assert result.status == "no_slots"


def test_loc05_identity_discovery_and_affiliation(doctors, governed_fixtures):
    doctor = doctors.candidate(specialties=("synthetic_cardio", "synthetic_general"))
    assert doctors.stage(doctor) == "submitted"
    assert doctors.stage(doctor) == "unchanged"
    reader = doctors.read()
    assert reader.details(doctor.doctor_id).status == "no_matches"
    doctors.approve(doctor, "credential")
    with pytest.raises(DirectoryDenied, match="reviews_required"):
        doctors.publish(doctor)
    doctors.approve(doctor, "schedule")
    doctors.publish(doctor)
    assert reader.details(doctor.doctor_id).doctors[0].specialties == doctor.specialties
    assert reader.search({"specialty": "synthetic_heart", "hospital_id": doctor.hospital_id}).status == "found"
    assert reader.search({"specialty": "synthetic_general", "latitude": 0, "longitude": 140}).status == "found"
    assert reader.search({"specialty": "synthetic_wrong"}).status == "unknown_specialty"
    assert reader.search({"specialty": "synthetic';select"}).status == "invalid_query"
    assert doctors.read(vocabulary=doctors.taxonomy.model_copy(update={"revoked": True})).search({}).status == "taxonomy_unavailable"
    assert doctors.read(vocabulary=doctors.taxonomy.model_copy(update={"version": 2})).search({}).status == "no_matches"
    governed_fixtures.services["operator"].withdraw(doctor.hospital_id)
    assert reader.details(doctor.doctor_id).status == "no_matches"
    assert reader.slots(doctors.slot_query(doctor)).status == "ineligible"


@pytest.mark.parametrize("case", ["signature", "wrong_doctor", "wrong_hospital", "wrong_revision", "scope",
                                  "self", "expired", "revoked", "organization", "evidence", "issuer"])
def test_loc05_independent_review_denials(case, doctors):
    item = doctors.candidate()
    doctors.stage(item)
    authority = doctors.authorities["credential"]
    updates = {}
    if case == "wrong_doctor":
        updates["doctor_id"] = uuid4()
    if case == "wrong_hospital":
        updates["hospital_id"] = uuid4()
    if case == "wrong_revision":
        updates["revision"] = 2
    if case == "organization":
        updates["organization"] = "wrong_org"
    if case == "issuer":
        updates["credential"] = "unknown_key"
    changes = {"scope": {"specialties": frozenset()}, "self": {"login": doctors.logins["importer"]},
               "expired": {"expires_at": datetime.now(UTC) - timedelta(seconds=1)}, "revoked": {"revoked": True}}
    reviewer = doctors.service("credential", reviewers=doctors.authorities |
                               {"credential": replace(authority, **changes.get(case, {}))})
    proof = doctors.proof(item, "credential", **updates)
    if case == "signature":
        proof = proof.model_copy(update={"signature": "0"*128})
    evidence = EVIDENCE if case != "evidence" else EVIDENCE | {"credential-document": b"altered"}
    with pytest.raises(DirectoryDenied):
        reviewer.review(proof, evidence)
    assert doctors.read().details(item.doctor_id).status == "no_matches"
    doctors.approve(item, "credential")
    doctors.approve(item, "schedule")
    doctors.publish(item)
    assert doctors.read().details(item.doctor_id).status == "found"


@pytest.mark.parametrize("state,expected", [("pending", 1), ("confirmed", 1), ("cancelled", 3),
                                             ("rescheduled", 3), ("completed", 3), ("no_show", 3)])
def test_loc05_occupancy_status_and_overlap(state, expected, doctors):
    item = doctors.candidate()
    doctors.activate(item)
    reader = doctors.read()
    query = doctors.slot_query(item)
    initial = reader.slots(query)
    assert initial.status == "provisional" and len(initial.slots) == 3
    assert initial.slots[0].start_utc.hour == 4
    assert initial.slots[-1].end_local.hour == 10 and initial.slots[-1].end_local.minute == 0
    a, b, c = initial.slots
    booking = doctors.appointment(item, a.start_utc + timedelta(minutes=10), c.start_utc, state)
    result = reader.slots(query)
    assert len(result.slots) == expected
    if expected == 1:
        assert result.slots == (c,)
        with engine.begin() as owner:
            owner.execute(text("UPDATE roghyar.appointments SET status='cancelled' WHERE id=:id"), {"id": booking})
        assert reader.slots(query).slots == initial.slots
    assert not {"patient_id", "appointment_id", "booking_code"} & result.model_dump().keys()


def test_loc05_replacement_revocation_and_stale_approval(doctors):
    item = doctors.candidate()
    doctors.activate(item)
    next_revision = item.model_copy(update={"revision": 2, "name": "Synthetic Corrected Doctor"})
    old = doctors.proof(item, "credential")
    doctors.stage(next_revision)
    with pytest.raises(DirectoryDenied):
        doctors.services["credential"].review(old.model_copy(update={"revision": 2}), EVIDENCE)
    for kind in ("credential", "schedule"):
        doctors.approve(next_revision, kind)
    doctors.publish(next_revision)
    assert doctors.read().details(item.doctor_id).doctors[0].name == next_revision.name
    doctors.revoke("key", "schedule")
    assert doctors.read().slots(doctors.slot_query(item)).status == "ineligible"
    with pytest.raises(DirectoryDenied, match="revoked"):
        doctors.publish(next_revision)


def test_loc05_publication_race_and_permissions(doctors):
    item = doctors.candidate()
    doctors.stage(item)
    for kind in ("credential", "schedule"):
        doctors.approve(item, kind)
    with pytest.raises(DirectoryDenied, match="unauthorized_operator"):
        doctors.services["credential"].publish(item.doctor_id, item.hospital_id, 1)
    barrier = Barrier(2, timeout=10)
    def attempt(_):
        barrier.wait()
        try:
            doctors.publish(item)
        except DirectoryDenied as exc:
            assert str(exc) == "transaction_conflict"
            return "denied"
        return "published"
    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(attempt, range(2))) == ["denied", "published"]
    for target, role, statement in (
        (doctors.runtime, None, "SELECT proof FROM roghyar.doctor_reviews"),
        (doctors.runtime, None, "SELECT patient_id FROM roghyar.appointments"),
        (doctors.connections["operator"], "roghyar_doc_operator", "UPDATE roghyar.doctor_reviews SET credential='x'"),
        (engine, "roghyar_maintenance", "UPDATE roghyar.doctors SET published=true"),
        (engine, "roghyar_maintenance", "DELETE FROM roghyar.doctor_schedules"),
    ):
        with pytest.raises(DBAPIError):
            with target.begin() as conn:
                if role:
                    conn.exec_driver_sql(f"SET LOCAL ROLE {role}")
                conn.execute(text(statement))
    doctors.revoke("revision", f"{item.doctor_id}:1")
    assert doctors.read().details(item.doctor_id).status == "no_matches"


def test_loc05_private_routes_and_outage():
    from psycopg import OperationalError
    def disconnect():
        raise OperationalError("synthetic outage")
    connection = create_engine("postgresql+psycopg://", creator=disconnect, hide_parameters=True)
    now = datetime.now(UTC)
    vocabulary = SpecialtyVocabulary(version=1, source_ref="synthetic", reviewer_ref="synthetic",
        expires_at=now + timedelta(hours=1), entries=(Specialty(key="synthetic", display="Synthetic"),))
    try:
        assert DoctorSearch(connection, vocabulary=vocabulary).search({}).status == "database_unavailable"
    finally:
        connection.dispose()
    client = TestClient(app)
    for path in ("/api/v1/doctors", f"/api/v1/doctors/{uuid4()}/slots"):
        assert client.get(path).status_code == 404
    assert not any("/doctors" in path for path in app.openapi()["paths"])


@pytest.mark.parametrize("case", ["missing_registration", "invalid_registration", "unknown_registry",
                                  "untrusted_source", "expired_evidence", "duplicate_registration"])
def test_loc05_credential_import_validation(case, doctors):
    candidate = doctors.candidate()
    if case in ("missing_registration", "invalid_registration", "unknown_registry"):
        body = candidate.model_dump()
        if case == "missing_registration":
            body.pop("registration")
        elif case == "invalid_registration":
            body["registration"] = " "
        else:
            body["registry"] = "unknown"
        with pytest.raises(ValidationError):
            DoctorRevision.model_validate(body)
    elif case == "duplicate_registration":
        doctors.stage(candidate)
        duplicate = doctors.candidate(registration=candidate.registration, name="Synthetic Duplicate")
        with pytest.raises(DirectoryDenied, match="identity_conflict"):
            doctors.stage(duplicate)
    else:
        professional = candidate.professional.model_copy(update=(
            {"organization": "synthetic_unknown"} if case == "untrusted_source" else
            {"observed_at": datetime.now(UTC) - timedelta(days=31)}))
        with pytest.raises(DirectoryDenied, match="evidence_denied"):
            doctors.stage(candidate.model_copy(update={"professional": professional}))
    doctors.activate(candidate)
    assert doctors.read().details(candidate.doctor_id).status == "found"


def test_loc05_stable_names_ambiguous_taxonomy_and_proof_transplant(doctors):
    one, two = doctors.candidate(), doctors.candidate()
    with pytest.raises(ValidationError):
        SpecialtyVocabulary.model_validate(doctors.taxonomy.model_dump() | {
            "entries": (Specialty(key="a", display="Synthetic first", aliases=("shared",)),
                        Specialty(key="b", display="Synthetic second", aliases=("shared",)))})
    doctors.stage(one)
    doctors.stage(two)
    proof = doctors.proof(one, "credential")
    with pytest.raises(DirectoryDenied, match="review_denied"):
        doctors.services["credential"].review(proof.model_copy(update={"doctor_id": two.doctor_id}), EVIDENCE)
    doctors.activate(one)
    doctors.activate(two)
    assert [row.doctor_id for row in doctors.read().search({}).doctors] == sorted((one.doctor_id, two.doctor_id))


def test_loc05_hospital_revision_requires_new_affiliation(doctors, governed_fixtures):
    doctor = doctors.candidate()
    doctors.activate(doctor)
    facility = doctors.hospital
    revision = facility.model_copy(update={"revision": 2, "address": "Synthetic reviewed change",
        "source": facility.source.model_copy(update={"version": 2})})
    governed_fixtures.publish(revision)
    assert doctors.read().slots(doctors.slot_query(doctor)).status == "ineligible"
    amended = doctor.model_copy(update={"revision": 2, "hospital_revision": 2})
    doctors.activate(amended)
    assert doctors.read().slots(doctors.slot_query(doctor)).revision == 2


def test_loc05_scope_and_governed_expiry(doctors, governed_fixtures):
    candidate = doctors.candidate()
    doctors.activate(candidate)
    foreign = governed_fixtures.candidate()
    governed_fixtures.publish(foreign)
    with pytest.raises(DirectoryDenied, match="unauthorized_facility"):
        doctors.services["operator"].publish(candidate.doctor_id, foreign.hospital_id, 1)
    with engine.begin() as owner:
        owner.execute(text("UPDATE roghyar.doctor_current SET expires_at=now()-interval '1 second' WHERE doctor_id=:id"),
                      {"id": candidate.doctor_id})
    assert doctors.read().slots(doctors.slot_query(candidate)).status == "ineligible"
    assert doctors.read().details(candidate.doctor_id).status == "no_matches"


def test_loc05_booking_snapshot_and_duplicate_active_start(doctors):
    candidate = doctors.candidate()
    doctors.activate(candidate)
    reader = doctors.read()
    query = doctors.slot_query(candidate)
    first = reader.slots(query).slots[0]
    params = {"doctor_id": candidate.doctor_id, "hospital_id": candidate.hospital_id,
              "starts": [first.start_utc], "ends": [first.end_utc]}
    with scoped_session(doctors.runtime.execution_options(isolation_level="REPEATABLE READ"),
                        CredentialVerifier(None), None) as snapshot:
        assert snapshot.scalars(FREE, params).all() == [1]
        doctors.appointment(candidate, first.start_utc, first.end_utc, "confirmed")
        with pytest.raises(DBAPIError):
            doctors.appointment(candidate, first.start_utc, first.end_utc, "pending")
        assert snapshot.scalars(FREE, params).all() == [1]
    assert first not in reader.slots(query).slots


def test_loc05_schedule_replacement_and_source_withdrawal_snapshot(doctors):
    candidate = doctors.candidate()
    doctors.activate(candidate)
    revised = candidate.model_copy(update={"revision": 2, "schedule": candidate.schedule.model_copy(update={
        "exceptions": (ScheduleException(start_date=doctors.day, end_date=doctors.day, action="closure"),)})})
    reader = doctors.read()
    params = doctors.slot_query(candidate) | reader.taxonomy(doctors.now)
    with scoped_session(doctors.runtime.execution_options(isolation_level="REPEATABLE READ"),
                        CredentialVerifier(None), None) as snapshot:
        assert snapshot.execute(SCHEDULE, params).mappings().one()["revision"] == 1
        doctors.activate(revised)
        assert snapshot.execute(SCHEDULE, params).mappings().one()["revision"] == 1
    assert reader.slots(doctors.slot_query(candidate)).status == "no_slots"
    doctors.revoke("source", "schedule-document")
    assert reader.slots(doctors.slot_query(candidate)).status == "ineligible"


def test_loc05_live_query_plans_and_timeout(doctors):
    candidate = doctors.candidate()
    doctors.activate(candidate)
    for index in range(12):
        doctors.activate(doctors.candidate(name=f"Synthetic Doctor Plan {index}"))
    reader = doctors.read()
    args = dict(doctor_id=None, hospital_id=None, specialty=None, city=None, latitude=None, longitude=None, limit=10)
    args |= reader.taxonomy(doctors.now)
    slots = reader.slots(doctors.slot_query(candidate)).slots
    with scoped_session(doctors.runtime, CredentialVerifier(None), None) as session:
        session.execute(text("SET LOCAL statement_timeout = '1500ms'"))
        plans = {}
        for label, query, params in (
            ("detail", DISCOVERY, args | {"doctor_id": candidate.doctor_id}),
            ("specialty", DISCOVERY, args | {"specialty": "synthetic_cardio"}),
            ("facility", DISCOVERY, args | {"hospital_id": candidate.hospital_id}),
            ("schedule", SCHEDULE, args | doctors.slot_query(candidate)),
            ("occupancy", FREE, doctors.slot_query(candidate) |
             {"starts": [slot.start_utc for slot in slots], "ends": [slot.end_utc for slot in slots]}),
        ):
            raw = session.execute(text("EXPLAIN (ANALYZE, FORMAT JSON) " + query.text), params).scalar_one()[0]
            plans[label] = ("Index" in str(raw["Plan"]), raw["Execution Time"])
    print("\nLOC05 plans " + " ".join(f"{label}={int(index)},{ms:.3f}" for label, (index, ms) in plans.items()))
    with engine.begin() as owner:
        owner.execute(text("LOCK TABLE roghyar.doctor_current IN ACCESS EXCLUSIVE MODE"))
        assert reader.details(candidate.doctor_id).status == "query_timeout"
    assert reader.details(candidate.doctor_id).status == "found"
