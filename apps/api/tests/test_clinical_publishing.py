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

    def artifact(version):
        item = create(content_id=name, condition=name, version=version, reviewed_at=now)
        reviews = []
        for variant in item.body.variants:
            review = TranslationApproval(schema_version=1, binding=selection(item).binding,
                locale=variant.locale, translation_version=1, reviewer=language.reviewer, key_id="language_key",
                review_ref=variant.review_ref, reviewed_at=now, expires_at=now + timedelta(hours=1),
                status="approved", signature="0"*128)
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


def test_live_atomic_publication(database, publisher, author_login, reviewed):
    publish_engine, operator = publisher
    author_engine, author = author_login
    trust, artifact = reviewed
    author_service = HomeCarePublisher(author_engine, lambda: trust, operators=frozenset({author}))
    current_trust = trust
    service = HomeCarePublisher(publish_engine, lambda: current_trust, operators=frozenset({operator}))
    reader = PostgresHomeCareRepository(database, lambda: current_trust)
    first, second = artifact(1), artifact(2)
    scope = first[0].body.condition
    audit = text("SELECT previous_state,state,actor,transaction_id FROM roghyar.home_care_events WHERE content_id=:scope")
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
            service.transition(binding(pair), "reviewed", generation=1)
            service.transition(binding(pair), "ready", generation=2)
        assert lookup(first).status == "empty"

        def activate(pair):
            try:
                service.transition(binding(pair), "active", generation=3)
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
        service.transition(binding(loser), "active", generation=3,
                           expected_active=(scope, winner[0].body.version, 4))
        assert lookup(winner).status == "unknown_version"
        current_trust = replace(trust, revoked_translations=frozenset({(loser[0].digest, 1, "ur", "language_key")}))
        assert lookup(loser).variant is None
        current_trust = trust
        service.transition(binding(winner), "active", generation=5,
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
        service.transition(binding(winner), "revoked", generation=6)
        assert lookup(winner).variant is None
        with pytest.raises(DBAPIError):
            service.transition(binding(winner), "active", generation=7)
    finally:
        # Fixture cleanup runs only on the disposable database after restricted-role assertions.
        with engine.begin() as owner:
            owner.execute(text("DELETE FROM roghyar.home_care_events WHERE content_id=:scope"), {"scope": scope})
            owner.execute(text("DELETE FROM roghyar.home_care_revisions WHERE content_id=:scope"), {"scope": scope})
