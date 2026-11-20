"""SYNTHETIC—DEVELOPMENT ONLY. Live disposable formulary publication probes."""

import pytest
from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import DBAPIError, IntegrityError, OperationalError

from roghyar import main
from roghyar.formulary_safety import FormularyService, SelectionReason
from roghyar.settings import Settings
from roghyar.triage import TriageService
from test_database_security import database
from test_formulary_safety import CONDITION, DOSE, VOCABULARY, safety, sign_formulary
from test_red_flag_database import publisher

INSERT = text("""
  INSERT INTO roghyar.formulary (
    id,condition_code,medicine,version,review_status,reviewed_by,reviewed_at,active,
    min_age,max_age,adult_dose,frequency,max_daily,child_dose_table,contraindications,
    home_steps,see_doctor_if,reviewed_payload
  ) VALUES (
    :id,:condition_code,:medicine,:version,:review_status,:reviewed_by,:reviewed_at,:active,
    :min_age,:max_age,:adult_dose,:frequency,:max_daily,:child_dose_table,:contraindications,
    :home_steps,:see_doctor_if,:reviewed_payload
  )
""").bindparams(bindparam("child_dose_table", type_=JSONB(none_as_null=True)),
                bindparam("reviewed_payload", type_=JSONB(none_as_null=True)))
ACTIVATE = text("UPDATE roghyar.formulary SET active=:active WHERE id=:id")


def test_live_signed_formulary_publication(database, publisher, sign_formulary, monkeypatch, caplog):
    engine, login = publisher
    sign, keys = sign_formulary
    v1, v2 = sign(), sign(version=2)
    settings = Settings(_env_file=None, environment="test", synthetic_fixtures=True)
    service = FormularyService(database, VOCABULARY, keys, TriageService(database))
    try:
        with pytest.raises(IntegrityError) as missing, engine.begin() as connection:
            connection.exec_driver_sql("SET LOCAL ROLE roghyar_publisher")
            connection.execute(INSERT, {**v1, "reviewed_payload": None})
        assert missing.value.orig.diag.constraint_name == "ck_formulary_active_attestation"
        with engine.begin() as connection:
            connection.exec_driver_sql("SET LOCAL ROLE roghyar_publisher")
            assert connection.execute(text("""
                SELECT session_user, current_user, rolsuper, rolbypassrls
                FROM pg_roles WHERE rolname=session_user
            """)).one() == (login, "roghyar_publisher", False, False)
            connection.execute(INSERT, {**v1, "active": False})
            connection.execute(INSERT, {**v2, "active": False})
        with database.connect() as connection:
            assert connection.scalar(text("SELECT current_user")) == "roghyar_runtime"
            assert connection.scalar(text("SELECT count(*) FROM roghyar.formulary")) == 0
        with pytest.raises(DBAPIError) as denied, database.begin() as connection:
            connection.execute(ACTIVATE, {"id": v1["id"], "active": True})
        assert denied.value.orig.sqlstate == "42501"
        assert service.probe_synthetic(CONDITION, safety(), settings).reason == SelectionReason.NO_APPROVED_CONTENT
        with engine.begin() as connection:
            connection.exec_driver_sql("SET LOCAL ROLE roghyar_publisher")
            connection.execute(ACTIVATE, {"id": v1["id"], "active": True})
        result = service.probe_synthetic(CONDITION, safety(), settings)
        assert result.version == 1 and result.dose.instructions == DOSE["instructions"]
        assert service.evaluate({"observations": []}).reason == SelectionReason.TRIAGE_NOT_AUTHORIZED
        invalid = FormularyService(database, VOCABULARY, {}, TriageService(database))
        assert invalid.probe_synthetic(CONDITION, safety(), settings).reason == SelectionReason.INVALID_ATTESTATION
        with pytest.raises(IntegrityError) as duplicate, engine.begin() as connection:
            connection.exec_driver_sql("SET LOCAL ROLE roghyar_publisher")
            connection.execute(ACTIVATE, {"id": v2["id"], "active": True})
        assert duplicate.value.orig.sqlstate == "23505"
        with pytest.raises(DBAPIError) as immutable, engine.begin() as connection:
            connection.exec_driver_sql("SET LOCAL ROLE roghyar_publisher")
            connection.execute(text("UPDATE roghyar.formulary SET adult_dose='changed' WHERE id=:id"),
                               {"id": v1["id"]})
        assert immutable.value.orig.sqlstate == "P0001"
        for prior, next_version in ((v1, v2), (v2, v1)):
            with engine.begin() as connection:
                connection.exec_driver_sql("SET LOCAL ROLE roghyar_publisher")
                connection.execute(ACTIVATE, [{"id": prior["id"], "active": False},
                                              {"id": next_version["id"], "active": True}])
            assert service.probe_synthetic(CONDITION, safety(), settings).version == next_version["version"]
        with main.engine.begin() as connection:
            audit = connection.execute(text("""
                SELECT action, actor_db_role FROM roghyar.audit_log WHERE entity_id=:id ORDER BY at
            """), {"id": v1["id"]}).all()
        assert audit == [(action, login) for action in ("approved", "published", "deactivated", "published")]

        marker = "SYNTHETIC private diagnostic"

        def failure(*args):
            raise OperationalError("SELECT 1", {}, RuntimeError(marker))

        monkeypatch.setattr(service._repository, "load", failure)
        assert service.probe_synthetic(CONDITION, safety(), settings).reason == SelectionReason.EVALUATION_FAILURE
        assert marker not in caplog.text
    finally:
        with main.engine.begin() as connection:
            params = {"ids": [v1["id"], v2["id"]]}
            connection.execute(text("DELETE FROM roghyar.audit_log WHERE entity_id=ANY(:ids)"), params)
            connection.execute(text("DELETE FROM roghyar.formulary WHERE id=ANY(:ids)"), params)
