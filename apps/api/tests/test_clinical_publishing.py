"""SYNTHETIC—DEVELOPMENT ONLY: live publication and trust boundaries."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from time import perf_counter
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from roghyar.clinical_publishing import (
    HomeCarePublisher, LanguageAuthority, PostgresHomeCareRepository, PublicationDenied,
    TranslationApproval, TrustSnapshot, verify_translations,
)
from roghyar.main import engine
from test_database_security import database
from test_assessment import service
from test_home_care import home_fixture, selection
from test_red_flag_database import publisher

author_login = pytest.fixture(name="author_login")(publisher.__wrapped__)
from test_reply_writer import signed


@pytest.fixture
def reviewed(home_fixture):
    _, clinical, create = home_fixture
    key = Ed25519PrivateKey.generate()
    now = datetime.now(UTC)
    clinical = replace(clinical, valid_until=now + timedelta(days=3650))
    language = LanguageAuthority("synthetic_language", key.public_key(), frozenset({"en", "ur", "ps"}),
                                 now - timedelta(days=1), now + timedelta(days=1), "synthetic_anchor")
    trust = TrustSnapshot({"sample_key": clinical}, {"language_key": language})
    name = "synthetic_" + uuid4().hex

    def artifact(version, *, review_change=None, changed_variant=False):
        template = create()
        variants = tuple(v.model_copy(update={"summary": "Synthetic only"}) if v.locale == "en" else v
                         for v in template.body.variants)
        if changed_variant:
            variants = tuple(v.model_copy(update={"summary": "Synthetic changed"}) if v.locale == "ur" else v
                             for v in variants)
        item = create(content_id=name, condition=name, version=version, reviewed_at=now, variants=variants)
        reviews = []
        for variant in item.body.variants:
            review = TranslationApproval(schema_version=1, binding=selection(item).binding,
                locale=variant.locale, translation_version=1, reviewer=language.reviewer, key_id="language_key",
                review_ref=variant.review_ref, reviewed_at=now, expires_at=now + timedelta(hours=1),
                status="approved", signature="0"*128)
            if review_change and variant.locale == "ur":
                review = review_change(review)
            reviews.append(review.model_copy(update={"signature": key.sign(review.signing_bytes()).hex()}))
        return item, tuple(reviews)

    return trust, artifact


def test_translation_integrity(reviewed):
    trust, artifact = reviewed
    item, reviews = artifact(1)
    now = datetime.now(UTC)
    verify_translations(item, reviews, trust, now)
    for changed_item, changed_reviews, changed_trust in (
        (artifact(2)[0], reviews, trust),
        (item, reviews[:-1], trust),
        (item, (reviews[0].model_copy(update={"signature": "0"*128}), *reviews[1:]), trust),
        (item, reviews, replace(trust, language={})),
        (item, reviews, replace(trust, revoked_translations=frozenset({(item.digest, 1, "ur", "language_key")}))),
        (item, reviews, replace(trust, language={"language_key": replace(trust.language["language_key"], locales=frozenset({"en"}))})),
    ):
        with pytest.raises(PublicationDenied):
            verify_translations(changed_item, changed_reviews, changed_trust, now)


def test_live_atomic_publication(database, publisher, author_login, reviewed, monkeypatch):
    publish_engine, operator = publisher
    author_engine, author = author_login
    trust, artifact = reviewed
    author_service = HomeCarePublisher(author_engine, lambda: trust, operators=frozenset({author}))
    current_trust = trust
    workflow = HomeCarePublisher(publish_engine, lambda: current_trust, operators=frozenset({operator}))
    reader = PostgresHomeCareRepository(database, lambda: current_trust)
    first, second = artifact(1), artifact(2)
    scope = first[0].body.condition
    audit = text("SELECT previous_state,state,actor,transaction_id FROM roghyar.home_care_events WHERE content_id=:scope ORDER BY id")
    def binding(pair):
        return selection(pair[0]).binding
    def lookup(pair, locale="en"):
        return reader.resolve(selection(pair[0], locale=locale, now=datetime.now(UTC)))

    try:
        assert lookup(first).status == "empty"
        with pytest.raises(PublicationDenied, match="publisher_identity"):
            HomeCarePublisher(publish_engine, lambda: trust).stage(*first)
        for pair in (first, second):
            author_service.stage(*pair)
            author_service.transition(binding(pair), "submitted", generation=0)
            with pytest.raises(PublicationDenied, match="author_review"):
                author_service.transition(binding(pair), "reviewed", generation=1)
            workflow.transition(binding(pair), "reviewed", generation=1)
            workflow.transition(binding(pair), "ready", generation=2)
        assert lookup(first).status == "empty"

        def activate(pair):
            try:
                start = perf_counter()
                workflow.transition(binding(pair), "active", generation=3)
                print(f"GD-05 publication activate n=1 elapsed_ms={(perf_counter()-start)*1000:.3f}")
                return pair
            except PublicationDenied as error:
                assert str(error) == "stale_active"
                return None

        with ThreadPoolExecutor(max_workers=2) as pool:
            winners = [pair for pair in pool.map(activate, (first, second)) if pair is not None]
        assert len(winners) == 1
        winner = winners[0]
        loser = second if winner is first else first
        for locale in ("en", "ur", "ps"):
            assert lookup(winner, locale).variant == next(v for v in winner[0].body.variants if v.locale == locale)
        with pytest.raises(DBAPIError), database.begin() as connection:
            connection.execute(text("UPDATE roghyar.home_care_revisions SET state='revoked'"))
        with pytest.raises(DBAPIError), publish_engine.begin() as connection:
            connection.exec_driver_sql("SET LOCAL ROLE roghyar_publisher")
            connection.execute(text("UPDATE roghyar.home_care_revisions SET artifact='{}'::jsonb"))
        with pytest.raises(DBAPIError), publish_engine.begin() as connection:
            connection.exec_driver_sql("SET LOCAL ROLE roghyar_publisher")
            connection.execute(text("DELETE FROM roghyar.home_care_events"))
        snapshot_query = text("SELECT version,state,generation FROM roghyar.home_care_revisions "
                              "WHERE content_id=:scope ORDER BY version")
        def snapshot():
            with engine.connect() as connection:
                return (connection.execute(snapshot_query, {"scope": scope}).all(),
                        connection.execute(audit, {"scope": scope}).all())
        before = snapshot()
        # Inject a failure on the second write. The same transaction must already
        # see the first revision superseded and its audit event appended.
        from roghyar import clinical_publishing
        from sqlalchemy.engine import Connection
        execute = Connection.execute
        seen = []
        def fail_activation(connection, statement, parameters=None, **options):
            if statement is clinical_publishing.UPDATE and parameters["state"] == "active":
                rows = execute(connection, snapshot_query, {"scope": scope}).all()
                assert any(row.state == "superseded" and row.generation == 5 for row in rows)
                seen.append(True)
                raise RuntimeError("synthetic activation fault")
            return execute(connection, statement, parameters, **options)
        with monkeypatch.context() as patch:
            patch.setattr(Connection, "execute", fail_activation)
            with pytest.raises(RuntimeError, match="synthetic activation fault"):
                workflow.transition(binding(loser), "active", generation=3,
                                    expected_active=(scope, winner[0].body.version, 4))
        assert seen == [True]
        assert snapshot() == before
        assert lookup(winner).status == "verified"
        workflow.transition(binding(loser), "active", generation=3,
                           expected_active=(scope, winner[0].body.version, 4))
        assert lookup(winner).status == "unknown_version"
        current_trust = replace(trust, revoked_translations=frozenset({(loser[0].digest, 1, "ur", "language_key")}))
        assert lookup(loser).variant is None
        current_trust = trust
        workflow.transition(binding(winner), "active", generation=5,
                           expected_active=(scope, loser[0].body.version, 4))
        assert lookup(winner).status == "verified"
        with engine.connect() as owner:
            history = owner.execute(audit, {"scope": scope}).all()
        assert {row.actor for row in history} == {operator, author}
        assert any(row.previous_state == "active" and row.state == "superseded" for row in history)
        for label, operation in (("postgres_lookup", lambda: lookup(winner)),
                                 ("translation_verify", lambda: verify_translations(winner[0], winner[1], trust, datetime.now(UTC)))):
            samples = []
            for _ in range(25):
                start = perf_counter()
                operation()
                samples.append((perf_counter() - start) * 1000)
            samples.sort()
            print(f"GD-04 synthetic benchmark {label} n=25 p50={samples[12]:.3f} ms p95={samples[23]:.3f} ms")
        def benchmark(label, operation):
            start = perf_counter()
            result = operation()
            print(f"GD-05 publication {label} n=1 elapsed_ms={(perf_counter()-start)*1000:.3f}")
            return result

        third = artifact(3)
        benchmark("stage", lambda: author_service.stage(*third))
        author_service.transition(binding(third), "submitted", generation=0)
        benchmark("review", lambda: workflow.transition(binding(third), "reviewed", generation=1))
        benchmark("ready", lambda: workflow.transition(binding(third), "ready", generation=2))
        benchmark("replace", lambda: workflow.transition(binding(third), "active", generation=3,
                  expected_active=(scope, winner[0].body.version, 6)))
        benchmark("rollback", lambda: workflow.transition(binding(winner), "active", generation=7,
                  expected_active=(scope, 3, 4)))
        benchmark("revoke", lambda: workflow.transition(binding(winner), "revoked", generation=8))
        assert benchmark("post_revoke", lambda: lookup(winner)).variant is None
        assert lookup(winner).variant is None
        with pytest.raises(DBAPIError):
            workflow.transition(binding(winner), "active", generation=9)
    finally:
        # Fixture cleanup runs only on the disposable database after restricted-role assertions.
        with engine.begin() as owner:
            owner.execute(text("DELETE FROM roghyar.home_care_events WHERE content_id=:scope"), {"scope": scope})
            owner.execute(text("DELETE FROM roghyar.home_care_revisions WHERE content_id=:scope"), {"scope": scope})


def test_assessment_writer_cannot_publish(service):
    api, _ = service
    queries = (
        "INSERT INTO roghyar.home_care_revisions DEFAULT VALUES",
        "UPDATE roghyar.home_care_revisions SET state='active'",
        "UPDATE roghyar.home_care_revisions SET translations='[]'::jsonb",
        "DELETE FROM roghyar.home_care_events",
        "UPDATE roghyar.home_care_events SET state='active'",
        "SET LOCAL ROLE roghyar_publisher",
    )
    with api.writer.connect() as connection:
        assert connection.execute(text("""SELECT current_user, rolsuper, rolbypassrls
            FROM pg_roles WHERE rolname=current_user""")).one() == ("roghyar_assessment_writer", False, False)
    for statement in queries:
        with pytest.raises(DBAPIError) as denied, api.writer.begin() as connection:
            connection.exec_driver_sql(statement)
        assert denied.value.orig.sqlstate == "42501"


@pytest.fixture
def published_chain_content(database, publisher, author_login, reviewed):
    publish_engine, operator = publisher
    author_engine, author = author_login
    trust, artifact = reviewed
    item, approvals = artifact(1)
    author_service = HomeCarePublisher(author_engine, lambda: trust, operators=frozenset({author}))
    workflow = HomeCarePublisher(publish_engine, lambda: trust, operators=frozenset({operator}))
    with database.connect() as connection:
        assert connection.scalar(text("SELECT current_user")) == "roghyar_runtime"
    reader = PostgresHomeCareRepository(database, lambda: trust)

    def activate(revision, reviews, expected=None):
        target = selection(revision).binding
        author_service.stage(revision, reviews)
        author_service.transition(target, "submitted", generation=0)
        workflow.transition(target, "reviewed", generation=1)
        workflow.transition(target, "ready", generation=2)
        workflow.transition(target, "active", generation=3, expected_active=expected)

    def publish():
        activate(item, approvals)

    def replace_active():
        revision, reviews = artifact(2)
        activate(revision, reviews, (item.body.content_id, 1, 4))
        assert reader.resolve(selection(item, now=datetime.now(UTC))).status == "unknown_version"
        return revision

    def revoke(revision, check_chain):
        nonlocal trust
        prior = trust
        trust = replace(trust, revoked_translations=frozenset({(revision.digest, 1, "ur", "language_key")}))
        assert reader.resolve(selection(revision, now=datetime.now(UTC))).variant is None
        assert check_chain()
        trust = prior
        workflow.transition(selection(revision).binding, "revoked", generation=4)

    def mutate_translation(case, check_chain):
        nonlocal trust
        prior = trust
        if case in ("expired_reviewer", "revoked_reviewer", "revoked_ur", "revoked_ps", "scope", "trust_ref"):
            locale = "ps" if case == "revoked_ps" else "ur"
            authority = trust.language["language_key"]
            changes = {
                "expired_reviewer": {"valid_until": item.body.reviewed_at},
                "revoked_reviewer": {"revoked": True}, "scope": {"locales": frozenset({"en", "ps"})},
                "trust_ref": {"trust_ref": ""},
            }
            if case in changes:
                trust = replace(trust, language={"language_key": replace(authority, **changes[case])})
            else:
                trust = replace(trust, revoked_translations=frozenset({(item.digest, 1, locale, "language_key")}))
            try:
                assert reader.resolve(selection(item, locale=locale, now=datetime.now(UTC))).status == "invalid_artifact"
                # The artifact is atomic, including requests for the other reviewed variant.
                assert reader.resolve(selection(item, locale="en", now=datetime.now(UTC))).status == "invalid_artifact"
                check_chain(item)
            finally:
                trust = prior
            return

        def alter(review):
            updates = {
                "expired_approval": {"expires_at": review.reviewed_at},
                "reviewer": {"reviewer": "synthetic_other"},
                "key": {"key_id": "synthetic_unknown"},
                "locale": {"locale": "ps"},
                "binding": {"binding": review.binding.model_copy(update={"content_id": "synthetic_other"})},
                "digest": {"binding": review.binding.model_copy(update={"digest": "a" * 64})},
                "reference": {"review_ref": "synthetic_other"},
            }
            return review.model_copy(update=updates.get(case, {}))

        revision, reviews = artifact(2, review_change=alter, changed_variant=case == "text_after_signing")
        if case == "missing":
            reviews = tuple(r for r in reviews if r.locale != "ur")
        if case in ("signature", "translation_version", "metadata_after_signing"):
            changes = {"signature": "0" * 128} if case == "signature" else (
                {"translation_version": 2} if case == "translation_version" else
                {"reviewed_at": next(r for r in reviews if r.locale == "ur").reviewed_at + timedelta(microseconds=1)})
            reviews = tuple(r.model_copy(update=changes) if r.locale == "ur" else r for r in reviews)
        if case in ("old_approval", "text_after_signing"):
            if case == "old_approval":
                old_ur = next(r for r in approvals if r.locale == "ur")
            else:
                old_ur = next(r for r in approvals if r.locale == "ur").model_copy(update={
                    "binding": selection(revision).binding})
            reviews = tuple(old_ur if r.locale == "ur" else r for r in reviews)
        if case == "reused_variant":
            ur = next(r for r in reviews if r.locale == "ur")
            reviews = tuple(ur.model_copy(update={"locale": "ps"}) if r.locale == "ps" else r for r in reviews)

        target = selection(revision).binding
        author_service.stage(revision, reviews)
        author_service.transition(target, "submitted", generation=0)
        expected = ("translation_coverage" if case in ("missing", "locale") else
                    "translation_signature" if case in ("signature", "translation_version", "metadata_after_signing",
                                                       "text_after_signing", "reused_variant")
                    else "translation_authority")
        workflow.transition(target, "reviewed", generation=1)
        with pytest.raises(PublicationDenied, match=f"^{expected}$"):
            workflow.transition(target, "ready", generation=2)
        assert reader.resolve(selection(revision, now=datetime.now(UTC))).status == "unknown_version"
        check_chain(revision)

    try:
        yield item, reader, publish, replace_active, revoke, mutate_translation
    finally:
        with engine.begin() as owner:
            params = {"id": item.body.content_id}
            owner.execute(text("DELETE FROM roghyar.home_care_events WHERE content_id=:id"), params)
            owner.execute(text("DELETE FROM roghyar.home_care_revisions WHERE content_id=:id"), params)
