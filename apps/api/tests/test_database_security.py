"""Exercise the schema as a real, separate runtime login on disposable PostGIS."""

import base64
from datetime import datetime, timezone
from uuid import uuid4

import jwt
import pytest
from fastapi.testclient import TestClient
from psycopg import sql
from pydantic import SecretStr
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError, ProgrammingError

from roghyar import main
from roghyar.field_encryption import FieldEncryption
from roghyar.scoped_session import (
    AUDIENCE, ISSUER, AuthorizationDenied, CredentialVerifier, scoped_session,
)
from test_postgis import require_disposable_postgis

KEY = "SYNTHETIC-DEVELOPMENT-ONLY-signing-key"
VERIFIER = CredentialVerifier(SecretStr(KEY))
ENCRYPTION = FieldEncryption.from_secrets(
    SecretStr(base64.b64encode(b"a" * 32).decode()),
    SecretStr(base64.b64encode(b"b" * 32).decode()),
)
SYNTHETIC_NAME = "SYNTHETIC—DEVELOPMENT ONLY"


def token(kind, subject):
    now = int(datetime.now(timezone.utc).timestamp())
    return jwt.encode({"sub": str(subject), "kind": kind, "iat": now, "exp": now + 60,
                       "aud": AUDIENCE, "iss": ISSUER}, KEY, algorithm="HS256")


def ids(session, table):
    return set(session.scalars(text(f"SELECT id FROM roghyar.{table}")))


@pytest.fixture(scope="module")
def database():
    require_disposable_postgis()
    password = "SYNTHETIC-DEVELOPMENT-ONLY-runtime"
    with main.engine.begin() as owner:
        owner.connection.driver_connection.execute(
            sql.SQL("ALTER ROLE roghyar_runtime PASSWORD {}").format(sql.Literal(password))
        )
    runtime = create_engine(
        main.engine.url.set(username="roghyar_runtime", password=password),
        pool_size=1, max_overflow=0, hide_parameters=True,
    )
    try:
        yield runtime
    finally:
        runtime.dispose()


@pytest.fixture
def rows(database):
    hospitals, doctors, patients, admins, bookings, subjects, assessments = (
        [uuid4(), uuid4()] for _ in range(7)
    )
    session_id, anonymous_assessment, rule = uuid4(), uuid4(), uuid4()
    with main.engine.begin() as owner:
        for index, (hospital, doctor, patient, admin, booking, subject, assessment) in enumerate(
            zip(hospitals, doctors, patients, admins, bookings, subjects, assessments, strict=True)
        ):
            params = dict(hospital=hospital, doctor=doctor, patient=patient, admin=admin,
                          booking=booking, subject=subject, assessment=assessment,
                          tag=f"SYNTHETIC-DEVELOPMENT-ONLY-{uuid4()}", verified=index == 0,
                          pmdc=f"SYNTHETIC-DEVELOPMENT-ONLY-{doctor.hex[:16]}",
                          booking_code=f"SYNTHETIC-DEVELOPMENT-ONLY-{booking.hex[:5]}")
            phone = f"+1202555010{index}"
            params.update(
                name=ENCRYPTION.encrypt(SYNTHETIC_NAME, record_id=patient, field_name="full_name"),
                phone=ENCRYPTION.encrypt(phone, record_id=patient, field_name="phone"),
                lookup=ENCRYPTION.phone_lookup(phone),
            )
            owner.execute(text("""
                INSERT INTO roghyar.hospitals (id,name,address,city,district,province,location,
                    verified,published,verified_on,verified_by,source)
                VALUES (:hospital,:tag,:tag,:tag,:tag,:tag,ST_MakePoint(0,0)::geography,
                    :verified,:verified,now(),:admin,:tag)
            """), params)
            owner.execute(text("""
                INSERT INTO roghyar.doctors (id,hospital_id,full_name,specialty,pmdc_reg_no,
                    verified,published,verified_on,verified_by,source)
                VALUES (:doctor,:hospital,:tag,:tag,:pmdc,:verified,:verified,now(),:admin,:tag)
            """), params)
            owner.execute(text("""
                INSERT INTO roghyar.patients (id,auth_subject,full_name_ciphertext,phone_ciphertext,
                    phone_lookup,phone_verified_at,age,consent_at)
                VALUES (:patient,:subject,:name,:phone,:lookup,now(),30,now())
            """), params)
            owner.execute(text("""
                INSERT INTO roghyar.admin_users (hospital_id,auth_subject,email,role,active)
                VALUES (:hospital,:admin,:tag,'hospital_admin',true)
            """), params)
            owner.execute(text("""
                INSERT INTO roghyar.assessments (id,patient_id,text_ciphertext,severity,duration_hours,age)
                VALUES (:assessment,:patient,decode('00','hex'),'mild',120,30)
            """), params)
            owner.execute(text("""
                INSERT INTO roghyar.appointments (id,booking_code,doctor_id,hospital_id,patient_id,
                    assessment_id,slot_start,slot_end,status,consent_at)
                VALUES (:booking,:booking_code,:doctor,:hospital,:patient,
                    :assessment,'2030-01-01 00:00:00+00','2030-01-01 00:30:00+00','confirmed',now())
            """), params)
        owner.execute(text("""
            INSERT INTO roghyar.assessments (id,session_id,text_ciphertext,severity,more_than_one_year,age)
            VALUES (:id,:session,decode('00','hex'),'severe',true,0)
        """), {"id": anonymous_assessment, "session": session_id})
        owner.execute(text("""
            INSERT INTO roghyar.red_flag_rules (id,rule_code,category,pattern,level,version)
            VALUES (:id,'SYNTHETIC-DEVELOPMENT-ONLY','SYNTHETIC-DEVELOPMENT-ONLY','{}','synthetic',1)
        """), {"id": rule})
    try:
        yield hospitals, doctors, patients, admins, bookings, subjects, assessments, session_id, anonymous_assessment, rule
    finally:
        with main.engine.begin() as owner:
            for table, keys in (
                ("appointments", bookings), ("assessments", [*assessments, anonymous_assessment]),
                ("patients", patients), ("doctors", doctors), ("hospitals", hospitals),
                ("red_flag_rules", [rule]),
            ):
                if table == "hospitals":
                    owner.execute(text("DELETE FROM roghyar.admin_users WHERE auth_subject = ANY(:ids)"),
                                  {"ids": admins})
                owner.execute(text(f"DELETE FROM roghyar.{table} WHERE id = ANY(:ids)"), {"ids": keys})


def test_actual_runtime_isolation_and_pool_reuse(database, rows, monkeypatch):
    hospitals, doctors, patients, admins, bookings, subjects, assessments, session_id, anon, _ = rows
    with scoped_session(database, VERIFIER, token("patient", subjects[0]), required="patient") as session:
        pid = session.scalar(text("SELECT pg_backend_pid()"))
        assert session.scalar(text("SELECT current_user")) == "roghyar_runtime"
        assert ids(session, "patients") == {patients[0]}
        ciphertext = session.scalar(text("SELECT full_name_ciphertext FROM roghyar.patients"))
        assert ENCRYPTION.decrypt(ciphertext, record_id=patients[0], field_name="full_name") == SYNTHETIC_NAME
        assert ids(session, "appointments") == {bookings[0]}
        assert ids(session, "assessments") == {assessments[0]}
        assert session.scalar(text("SELECT id FROM roghyar.patients WHERE id=:id"),
                              {"id": patients[1]}) is None

    for index, admin in enumerate(admins):
        with scoped_session(database, VERIFIER, token("hospital_admin", admin)) as session:
            assert session.scalar(text("SELECT pg_backend_pid()")) == pid
            assert ids(session, "appointments") == {bookings[index]}
            assert ids(session, "patients") == ids(session, "assessments") == set()
            assert ids(session, "doctors") == ({doctors[0]} if index == 0 else set(doctors))
            assert session.scalar(text("SELECT count(*) FROM roghyar.admin_users")) == 1

    with pytest.raises(RuntimeError, match="force rollback"):
        with scoped_session(database, VERIFIER, token("patient", subjects[1])) as session:
            assert ids(session, "patients") == {patients[1]}
            raise RuntimeError("force rollback")
    with database.connect() as raw:
        assert raw.scalar(text("SELECT pg_backend_pid()")) == pid
        for name in ("kind", "subject"):
            assert raw.scalar(text("SELECT nullif(current_setting(:key, true), '')"),
                              {"key": f"roghyar.{name}"}) is None
    with scoped_session(database, VERIFIER, None) as session:
        assert ids(session, "hospitals") == {hospitals[0]}
        assert ids(session, "doctors") == {doctors[0]}
        for table in ("patients", "appointments", "assessments", "red_flag_rules", "formulary"):
            assert ids(session, table) == set()
    with scoped_session(database, VERIFIER, token("anonymous", session_id)) as session:
        assert ids(session, "assessments") == {anon}
    with scoped_session(database, VERIFIER, token("patient", uuid4())) as session:
        assert ids(session, "patients") == ids(session, "appointments") == set()

    monkeypatch.setattr(main, "engine", database)
    assert TestClient(main.app).get("/api/v1/health/ready").status_code == 200


def test_runtime_cannot_access_otp_audit_or_write_privileges(database, rows):
    _, _, _, admins, _, _, _, _, _, _ = rows
    with database.connect() as connection:
        assert not connection.scalar(text("SELECT pg_has_role(current_user,'roghyar_publisher','MEMBER')"))
        assert not connection.scalar(text("SELECT pg_has_role(current_user,'roghyar_maintenance','MEMBER')"))
        owned = connection.scalar(text("""
            SELECT count(*) FROM pg_tables WHERE schemaname='roghyar' AND tableowner=current_user
        """))
        assert owned == 0
        for table in ("admin_users", "patients", "formulary", "red_flag_rules"):
            assert not connection.scalar(text("SELECT has_table_privilege(:name, 'UPDATE')"),
                                         {"name": f"roghyar.{table}"})
        for table in ("otp_codes", "audit_log"):
            assert not connection.scalar(text("SELECT has_table_privilege(:name, 'SELECT')"),
                                         {"name": f"roghyar.{table}"})
    with pytest.raises(ProgrammingError):
        with scoped_session(database, VERIFIER, token("hospital_admin", admins[0])) as session:
            session.execute(text("SELECT source FROM roghyar.hospitals"))
    with pytest.raises(AuthorizationDenied, match="Untrusted database connection"):
        with scoped_session(main.engine, VERIFIER, None):
            pytest.fail("Owner connection escaped the runtime check")


def test_constraints_and_cancelled_slot_reuse(database, rows):
    hospitals, doctors, patients, _, bookings, _, _, _, _, rule = rows
    params = dict(hospital=hospitals[0], foreign=hospitals[1], doctor=doctors[0],
                  patient=patients[0], booking=bookings[0], rule=rule)
    appointment = """
        INSERT INTO roghyar.appointments (booking_code,doctor_id,hospital_id,patient_id,
            slot_start,slot_end,status,consent_at)
        VALUES ('SYNTHETIC-DEVELOPMENT-ONLY',:doctor,:hospital,:patient,
            '2030-01-01 00:00:00+00','2030-01-01 00:30:00+00','pending',now())
    """
    free_slot = appointment.replace("2030-01-01", "2030-01-02")
    invalid = (
        (appointment, "23505"),
        (free_slot.replace(":hospital", ":foreign"), "23503"),
        (free_slot.replace("'pending'", "'unknown'"), "23514"),
        ("UPDATE roghyar.patients SET age=121 WHERE id=:patient", "23514"),
        ("UPDATE roghyar.assessments SET duration_hours=0 WHERE patient_id=:patient", "23514"),
        ("UPDATE roghyar.assessments SET duration_hours=NULL WHERE patient_id=:patient", "23514"),
        ("UPDATE roghyar.red_flag_rules SET active=true WHERE id=:rule", "23514"),
        ("""INSERT INTO roghyar.doctor_schedules (doctor_id,weekday,start_time,end_time,slot_minutes)
            VALUES (:doctor,7,'09:00','10:00',15)""", "23514"),
        ("""INSERT INTO roghyar.doctor_schedules (doctor_id,weekday,start_time,end_time,slot_minutes)
            VALUES (:doctor,1,'09:00','10:00',61)""", "23514"),
    )
    with main.engine.connect() as owner, owner.begin() as transaction:
        for statement, sqlstate in invalid:
            with pytest.raises(IntegrityError) as exc, owner.begin_nested():
                owner.execute(text(statement), params)
            assert exc.value.orig.sqlstate == sqlstate
        assert owner.execute(text("""
            SELECT review_status, active FROM roghyar.red_flag_rules WHERE id=:rule
        """), params).one() == ("draft", False)
        assert owner.execute(text("""
            INSERT INTO roghyar.formulary (condition_code,medicine,version)
            VALUES ('SYNTHETIC-DEVELOPMENT-ONLY','SYNTHETIC-DEVELOPMENT-ONLY',1)
            RETURNING review_status, active
        """)).one() == ("draft", False)
        owner.execute(text("UPDATE roghyar.appointments SET status='cancelled' WHERE id=:booking"), params)
        owner.execute(text(appointment), params)
        transaction.rollback()

