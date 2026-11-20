"""SYNTHETIC—DEVELOPMENT ONLY. Disposable database publication security probes."""

from dataclasses import replace
from uuid import uuid4

import pytest
from psycopg import sql
from sqlalchemy import bindparam, create_engine, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import DBAPIError, IntegrityError, ProgrammingError

from roghyar import main
from roghyar.red_flags import PostgresRuleRepository, RedFlagRuleEngine, Status
from roghyar.scoped_session import scoped_session
from roghyar.triage import TriageService, TriageState
from test_database_security import VERIFIER, database, token
from test_red_flags import facts, sign_rule

INSERT = text("""
    INSERT INTO roghyar.red_flag_rules
      (id,rule_code,version,category,level,pattern,review_status,reviewed_by,reviewed_at,active)
    VALUES (:id,:code,:version,:category,:level,:pattern,'approved',:reviewer,:reviewed_at,:active)
""").bindparams(bindparam("pattern", type_=JSONB))


def insert(connection, identifier, record, *, active):
    connection.execute(INSERT, {
        "id": identifier, "code": record.rule_code, "version": record.version,
        "category": record.category, "level": record.level, "pattern": record.pattern,
        "reviewer": record.reviewed_by, "reviewed_at": record.reviewed_at, "active": active,
    })


@pytest.fixture
def publisher(database):
    # Real non-superuser connection. Its only group membership is the existing
    # publication role. This temporary service identity exists only in CI.
    name = "synthetic_publisher_" + uuid4().hex
    password = "SYNTHETIC—DEVELOPMENT ONLY"
    with main.engine.begin() as owner:
        connection = owner.connection.driver_connection
        connection.execute(sql.SQL("CREATE ROLE {} LOGIN NOINHERIT PASSWORD {}").format(
            sql.Identifier(name), sql.Literal(password),
        ))
        connection.execute(sql.SQL("GRANT roghyar_publisher TO {}").format(sql.Identifier(name)))
    engine = create_engine(main.engine.url.set(username=name, password=password), hide_parameters=True)
    try:
        yield engine, name
    finally:
        engine.dispose()
        with main.engine.begin() as owner:
            owner.connection.driver_connection.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(name)))


def test_live_runtime_cannot_publish_and_reads_only_attested_versions(database, publisher, sign_rule):
    engine, login = publisher
    sign, keys = sign_rule
    first, second, corrupt = uuid4(), uuid4(), uuid4()
    rule = sign()
    v2 = sign(version=2)
    evidence = facts(synthetic_first=True)
    repository = PostgresRuleRepository(database)
    interpreter = RedFlagRuleEngine(repository, keys)
    activate = text("UPDATE roghyar.red_flag_rules SET active=:active WHERE id=:id")
    try:
        # Approved-by fields without an envelope cannot create an active version.
        with pytest.raises(IntegrityError) as error, engine.begin() as connection:
            connection.execute(text("SET LOCAL ROLE roghyar_publisher"))
            insert(connection, corrupt, replace(rule, pattern={}), active=True)
        assert error.value.orig.diag.constraint_name == "ck_red_flag_active_attestation"

        with engine.begin() as connection:
            connection.execute(text("SET LOCAL ROLE roghyar_publisher"))
            assert connection.execute(text("""
                SELECT session_user, current_user, rolsuper, rolbypassrls
                FROM pg_roles WHERE rolname=session_user
            """)).one() == (login, "roghyar_publisher", False, False)
            insert(connection, first, rule, active=False)
            insert(connection, second, v2, active=False)
        assert repository() == []

        for kind in ("anonymous", "patient", "hospital_admin"):
            with pytest.raises(DBAPIError) as denied, scoped_session(
                database, VERIFIER, token(kind, uuid4()),
            ) as session:
                session.execute(activate, {"active": True, "id": first})
            assert denied.value.orig.sqlstate in ("42501", "25006")

        # A raw read-write connection as the same runtime also lacks write grants.
        with pytest.raises(ProgrammingError) as denied, database.begin() as connection:
            connection.execute(activate, {"active": True, "id": first})
        assert denied.value.orig.sqlstate == "42501"

        with engine.begin() as connection:
            connection.execute(text("SET LOCAL ROLE roghyar_publisher"))
            connection.execute(activate, {"active": True, "id": first})
        assert [item.version for item in interpreter.evaluate(evidence).matches] == [1]
        triage = TriageService(database, keys).evaluate(evidence)
        assert triage.state == TriageState.REVIEWED_EMERGENCY
        assert triage.matches == interpreter.evaluate(evidence).matches
        assert RedFlagRuleEngine(repository).evaluate(evidence).status is Status.INVALID_CONFIGURATION

        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(text("SET LOCAL ROLE roghyar_publisher"))
            connection.execute(activate, {"active": True, "id": second})
        with pytest.raises(DBAPIError) as immutable, engine.begin() as connection:
            connection.execute(text("SET LOCAL ROLE roghyar_publisher"))
            connection.execute(text("UPDATE roghyar.red_flag_rules SET pattern='{}' WHERE id=:id"),
                               {"id": first})
        assert immutable.value.orig.sqlstate == "P0001"

        for current, previous in ((second, first), (first, second)):
            with engine.begin() as connection:
                connection.execute(text("SET LOCAL ROLE roghyar_publisher"))
                connection.execute(activate, {"active": False, "id": previous})
                connection.execute(activate, {"active": True, "id": current})
            matching = interpreter.evaluate(evidence)
            assert matching.status is Status.MATCH
            assert matching.matches[0].version == (2 if current == second else 1)

        with main.engine.begin() as owner:
            history = owner.execute(text("""
                SELECT action, actor_db_role FROM roghyar.audit_log WHERE entity_id=:id
                ORDER BY at
            """), {"id": first}).all()
            assert history == [(action, login) for action in
                               ("approved", "published", "deactivated", "published")]
    finally:
        with main.engine.begin() as owner:
            params = {"ids": [first, second, corrupt]}
            owner.execute(text("DELETE FROM roghyar.audit_log WHERE entity_id=ANY(:ids)"), params)
            owner.execute(text("DELETE FROM roghyar.red_flag_rules WHERE id=ANY(:ids)"), params)
