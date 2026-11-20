import base64
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from psycopg import sql
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import ProgrammingError

from roghyar import main
from roghyar.assessment import (
    AssessmentInput, AssessmentService, AssessmentValidationError, DraftUnavailable,
    RetryConflict, validate_assessment,
)
from roghyar.assessment_retention import RetentionReport, anonymize_assessments
from roghyar.field_encryption import PrivacyConfigurationError
from roghyar.scoped_session import AuthorizationDenied, assessment_write_session
from roghyar.settings import Settings
from test_database_security import KEY, database, rows, token  # Shared disposable fixtures


def data(**changes):
    return {
        "text": "  مجھے درد ہے۔ زما سر درد کوي  ", "language": "ur", "severity": "moderate",
        "duration_hours": 120, "age": 0, "conditions": ["SYNTHETIC condition"],
        "allergies": ["SYNTHETIC allergy"], "current_medicines": ["SYNTHETIC medicine"],
        "answers": {"has_symptom": False, "selected": ["first", "second"]}, **changes,
    }


@pytest.mark.parametrize("changes", [
    {"text": " "}, {"text": "x" * 4001}, {"text": "invalid\x00text"}, {"severity": "urgent"},
    {"age": -1}, {"age": 121}, {"age": 2.5}, {"age": "0"}, {"duration_hours": 0},
    {"duration_hours": -1}, {"duration_hours": 1.5}, {"duration_hours": 72.0},
    {"duration_hours": 8761}, {"duration_hours": 119}, {"duration_hours": None},
    {"more_than_one_year": True}, {"more_than_one_year": "true"}, {"language": "xx"},
    {"sex": "unknown"}, {"conditions": ["a"] * 17}, {"allergies": ["a" * 121]},
    {"answers": {"question": {"nested": "invalid"}}}, {"answers": {"question": ["a"] * 9}},
    {"answers": {f"question_{i}": False for i in range(6)}}, {"answers": {"question": 1}},
    {"session_id": str(uuid4())}, {"patient_id": str(uuid4())}, {"photo": "data:image/png"},
    {"level": "example"}, {"verified": True}, {"diagnosis": "example"},
])
def test_strict_validation(changes):
    with pytest.raises(AssessmentValidationError) as error:
        validate_assessment(data(**changes))
    assert set(error.value.fields) <= {"assessment", *AssessmentInput.model_fields}
    assert str(error.value) == "Assessment input is invalid"


def test_normalized_duration_age_and_unicode():
    for hours in (1, 2, 12, 24, 48, 120, 360, 8760):
        draft = AssessmentInput.model_validate(data(duration_hours=hours, age=120))
        assert draft.duration_hours == hours
        assert draft.text == "مجھے درد ہے۔ زما سر درد کوي"
    draft = AssessmentInput.model_validate(data(duration_hours=None, more_than_one_year=True))
    assert draft.age == 0 and draft.more_than_one_year and draft.duration_hours is None


def test_closed_http_does_not_read_body():
    def body():
        pytest.fail("The closed endpoint must not consume the request")
        yield b""

    client = TestClient(main.app)
    first = client.post("/api/v1/assessments", content=body())
    second = client.get(f"/api/v1/assessments/{uuid4()}")
    for response in (first, second):
        assert response.status_code == 503
        assert response.headers["cache-control"] == "no-store"
        assert response.json()["error"]["request_id"]
    assert first.json()["error"]["request_id"] != second.json()["error"]["request_id"]


@pytest.fixture
def service(database):
    password = "SYNTHETIC-DEVELOPMENT-ONLY-writer"
    with main.engine.begin() as db:
        db.connection.driver_connection.execute(
            sql.SQL("ALTER ROLE roghyar_assessment_writer PASSWORD {}").format(sql.Literal(password))
        )
    writer = create_engine(main.engine.url.set(username="roghyar_assessment_writer", password=password),
                           pool_size=1, max_overflow=0, hide_parameters=True)
    settings = Settings(_env_file=None, environment="test", synthetic_fixtures=True,
                        session_signing_key=KEY,
                        field_encryption_key=base64.b64encode(b"a" * 32).decode(),
                        phone_lookup_key=base64.b64encode(b"b" * 32).decode())
    try:
        yield AssessmentService(database, writer, settings), settings
    finally:
        writer.dispose()


def test_persist_retry_isolation_and_tampering(service, rows, caplog):
    api, settings = service
    signed_a, signed_b = [token("patient", subject) for subject in rows[5]]
    anon_a, anon_b = [token("anonymous", uuid4()) for _ in range(2)]
    retry = uuid4()
    draft = AssessmentInput.model_validate(data())
    created = []
    try:
        for owner in (signed_a, anon_a, anon_b):
            result = api.create(draft, owner, retry)
            created.append(result.id)
            assert result.created_at.utcoffset().total_seconds() == 0
            assert api.create(draft, owner, retry) == result
            assert api.retrieve(result.id, owner) == result
        assert len(set(created)) == 3
        assert api.retrieve(created[0], signed_b) is None
        assert api.retrieve(created[1], anon_b) is None
        with api.writer.connect() as conn:
            assert conn.execute(text("""
                SELECT nullif(current_setting('roghyar.kind',true),''),
                       nullif(current_setting('roghyar.subject',true),'')
            """)).one() == (None, None)
        with pytest.raises(RetryConflict):
            api.create(AssessmentInput.model_validate(data(age=120)), anon_a, retry)
        for credential in (None, "invalid", token("hospital_admin", uuid4()), token("patient", uuid4())):
            with pytest.raises(AuthorizationDenied):
                api.create(draft, credential, uuid4())
        with pytest.raises(PrivacyConfigurationError):
            AssessmentService(api.reader, api.writer, settings.model_copy(update={"field_encryption_key": None}))
        with pytest.raises(DraftUnavailable):
            AssessmentService(api.reader, api.writer, settings.model_copy(update={"environment": "production"}))
        with main.engine.begin() as db:
            row = db.execute(text("""
                SELECT text_ciphertext, answers_ciphertext, safety_details_ciphertext, age, severity,
                    duration_hours, language, level
                FROM roghyar.assessments WHERE id=:id
            """), {"id": created[0]}).one()
            assert all(draft.text.encode() not in blob for blob in row[:3])
            assert all(value is None for value in row[3:])
            db.execute(text("""
                UPDATE roghyar.assessments SET answers_ciphertext=text_ciphertext WHERE id=:id
            """), {"id": created[0]})
        with pytest.raises(DraftUnavailable, match="draft is unavailable"):
            api.retrieve(created[0], signed_a)
        assert api.retrieve(created[0], signed_b) is None
        assert draft.text not in caplog.text and draft.allergies[0] not in caplog.text
    finally:
        with main.engine.begin() as db:
            db.execute(text("DELETE FROM roghyar.assessments WHERE id=ANY(:ids)"), {"ids": created})


def test_actual_writer_rls_and_rollback(service, rows):
    api, _ = service
    subject, other = uuid4(), uuid4()
    credential = token("anonymous", subject)
    insert = """
        INSERT INTO roghyar.assessments
            (id,session_id,text_ciphertext,answers_ciphertext,safety_details_ciphertext)
        VALUES (:id,:owner,'synthetic','synthetic','synthetic')
    """
    record = uuid4()
    for statement, inputs in (
        (insert, {"id": record, "owner": other}),
        ("UPDATE roghyar.assessments SET session_id=NULL", {}),
        ("INSERT INTO roghyar.assessments (level) VALUES ('synthetic')", {}),
    ):
        with pytest.raises(ProgrammingError) as error:
            with assessment_write_session(api.writer, api.verifier, credential) as (session, _):
                assert session.scalar(text("SELECT session_user")) == "roghyar_assessment_writer"
                session.execute(text(statement), inputs)
        assert error.value.orig.sqlstate == "42501"
    patient_credential = token("patient", rows[5][0])
    with pytest.raises(ProgrammingError) as error:
        with assessment_write_session(api.writer, api.verifier, patient_credential) as (session, _):
            session.execute(text(insert.replace("id,session_id", "id,patient_id")),
                            {"id": record, "owner": rows[2][1]})
    assert error.value.orig.sqlstate == "42501"
    with pytest.raises(RuntimeError):
        with assessment_write_session(api.writer, api.verifier, credential) as (session, _):
            pid = session.scalar(text("SELECT pg_backend_pid()"))
            session.execute(text(insert), {"id": record, "owner": subject})
            raise RuntimeError("Synthetic rollback")
    with api.writer.connect() as conn:
        assert conn.scalar(text("SELECT pg_backend_pid()")) == pid
        assert conn.scalar(text("SELECT nullif(current_setting('roghyar.subject',true),'')")) is None
    assert api.retrieve(record, credential) is None


def test_anonymization_preserves_booked_and_young_drafts(service, rows):
    api, settings = service
    session_id = rows[7]
    expired = rows[8]
    booked = rows[6][0]
    young_token = token("anonymous", uuid4())
    young = api.create(AssessmentInput.model_validate(data()), young_token, uuid4())
    expired_encrypted = api.create(AssessmentInput.model_validate(data()), young_token, uuid4())
    chat = uuid4()
    with main.engine.begin() as db:
        db.execute(text("""
            UPDATE roghyar.assessments SET created_at=now()-interval '91 days' WHERE id=ANY(:ids)
        """), {"ids": [expired, booked]})
        db.execute(text("""
            UPDATE roghyar.assessments SET created_at=now()-interval '90 days' WHERE id=:id
        """), {"id": expired_encrypted.id})
        db.execute(text("""
            INSERT INTO roghyar.chat_messages (id,session_id,role,text_ciphertext)
            VALUES (:id,:session,'patient','synthetic')
        """), {"id": chat, "session": session_id})
    operator = create_engine(main.engine.url, hide_parameters=True)

    @event.listens_for(operator, "begin")
    def select_role(connection):
        connection.exec_driver_sql("SET LOCAL ROLE roghyar_maintenance")

    try:
        assert anonymize_assessments(operator, settings) == RetentionReport(2, 1, True)
        assert api.retrieve(expired_encrypted.id, young_token) is not None
        assert anonymize_assessments(operator, settings, dry_run=False) == RetentionReport(2, 1, False)
        assert anonymize_assessments(operator, settings, dry_run=False) == RetentionReport(0, 0, False)
        assert api.retrieve(expired, token("anonymous", session_id)) is None
        assert api.retrieve(expired_encrypted.id, young_token) is None
        assert api.retrieve(young.id, young_token) == young
        with main.engine.begin() as db:
            scrubbed = db.execute(text("""
                SELECT anonymized_at IS NOT NULL, session_id, patient_id, text_ciphertext,
                       answers_ciphertext, safety_details_ciphertext, age, severity
                FROM roghyar.assessments WHERE id=ANY(:ids)
            """), {"ids": [expired, expired_encrypted.id]}).all()
            assert scrubbed == [(True, None, None, None, None, None, None, None)] * 2
            assert db.scalar(text("SELECT anonymized_at FROM roghyar.assessments WHERE id=:id"),
                             {"id": booked}) is None
    finally:
        operator.dispose()
        with main.engine.begin() as db:
            db.execute(text("DELETE FROM roghyar.chat_messages WHERE id=:id"), {"id": chat})
            db.execute(text("DELETE FROM roghyar.assessments WHERE id=ANY(:ids)"),
                       {"ids": [young.id, expired_encrypted.id]})
