"""SYNTHETIC—DEVELOPMENT ONLY. Ephemeral keys and non-clinical copy."""

import hashlib
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from statistics import median
from time import perf_counter_ns

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import ValidationError

from roghyar.classification import Classified, Uncertain
from roghyar.red_flags import Category, Match, Status
from roghyar.reply_writer import ArtifactBody, ReplyWriterService, ReviewerAuthority, SignedArtifact, Translation
from roghyar.structured_triage import Outcome
from roghyar.triage import TriageDecision, TriageService, WarningArtifact

NOW = datetime(2026, 1, 1, tzinfo=UTC)
COPY = {"en": "Synthetic sample", "ur": "آزمائشی عبارت", "ps": "ازمایښتي متن"}


def classified(label="greeting", languages=("en",)):
    return Classified(relevance_category=label, languages=languages, relevance_confidence=0.9)


@pytest.fixture
def signed():
    key = Ed25519PrivateKey.generate()
    authority = ReviewerAuthority("synthetic", key.public_key(), frozenset({"product"}),
                                  NOW - timedelta(days=2), NOW + timedelta(days=2))

    def artifact(intent="greeting", **changes):
        body = ArtifactBody(**(dict(schema_version=1, content_id="sample", version=1, intent=intent,
                                    reviewer="synthetic", key_id="sample_key", reviewed_at=NOW,
                                    review_status="approved", active=True,
                                    translations=tuple(Translation(language=k, text=v, review_ref="synthetic")
                                                       for k, v in COPY.items())) | changes))
        payload = body.signing_bytes()
        return SignedArtifact(body=body, digest=hashlib.sha256(payload).hexdigest(),
                              signature=key.sign(payload).hex())
    return key, authority, artifact


@pytest.mark.parametrize("locale", COPY)
@pytest.mark.parametrize("intent", ["greeting", "off_topic", "clarification"])
def test_exact_catalog_copy(signed, locale, intent):
    _, authority, artifact = signed
    writer = ReplyWriterService(catalog=(artifact(intent),), authorities={"sample_key": authority})
    label = Uncertain(languages=(), reason_code="language_unknown") if intent == "clarification" else classified(intent)
    reply = writer.render(classification=label, locale=locale, now=NOW)
    assert (reply.status, reply.text, reply.language, reply.intent) == ("rendered", COPY[locale], locale, intent)


@pytest.mark.parametrize("failure,reason", [
    ("tamper", "digest_mismatch"), ("domain", "invalid_signature"), ("revoked", "invalid_authority"),
    ("expired", "invalid_authority"), ("scope", "invalid_authority"), ("locale", "missing_locale"),
    ("duplicate", "missing_content"), ("inactive", "missing_content"),
])
def test_fail_closed_catalog(signed, failure, reason):
    key, authority, artifact = signed
    item = artifact()
    if failure == "tamper":
        item = item.model_copy(update={"body": item.body.model_copy(update={"translations": (
            Translation(language="en", text="Changed synthetic copy", review_ref="synthetic"),)})})
    if failure == "domain":
        item = item.model_copy(update={"signature": key.sign(b"wrong-domain" + item.body.signing_bytes()).hex()})
    if failure == "revoked":
        authority = replace(authority, revoked=True)
    if failure == "expired":
        authority = replace(authority, valid_until=NOW)
    if failure == "scope":
        authority = replace(authority, scopes=frozenset({"clinical"}))
    if failure == "locale":
        item = artifact(translations=(item.body.translations[0],))
    if failure == "inactive":
        item = artifact(active=False)
    writer = ReplyWriterService(catalog=(item, item) if failure == "duplicate" else (item,),
                                authorities={"sample_key": authority})
    result = writer.render(classification=classified(), locale="ps", now=NOW)
    assert result.reason == reason and result.text is None


@pytest.mark.parametrize("priority", ["emergency", "urgent"])
@pytest.mark.parametrize("failure", [None, "snapshot", "version", "digest", "locale", "missing"])
def test_warning_uses_be04_resolver(priority, failure):
    artifact = WarningArtifact(reference="synthetic", translations={"ur": COPY["ur"]})
    fingerprint = "a" * 64
    sealed = Match("synthetic", 1, next(iter(Category)), priority, (), "synthetic", artifact.sha256())
    original = TriageDecision(Status.MATCH, NOW, fingerprint, (sealed,))
    current = original
    if failure == "snapshot":
        current = replace(original, fingerprint="b" * 64)
    if failure == "version":
        current = replace(original, matches=(replace(sealed, version=2),))
    if failure == "digest":
        current = replace(original, matches=(replace(sealed, warning_sha256="b" * 64),))

    class SyntheticTriage(TriageService):
        def __init__(self):
            self._warning_repository = lambda ref, digest: None if failure == "missing" else artifact

        def evaluate(self, evidence):
            assert evidence == {"observations": []}
            return current

    writer = ReplyWriterService(triage=SyntheticTriage())
    outcome = Outcome("reviewed_emergency" if priority == "emergency" else "reviewed_urgent", original)
    result = writer.render(classification=classified("off_topic"), outcome=outcome, now=NOW,
                           locale="ps" if failure == "locale" else "ur")
    assert result.intent == priority
    assert result.text == (None if failure else COPY["ur"])
    assert result.reason == ("warning_unavailable" if failure else "verified")


def test_empty_restricted_mixed_and_no_candidate_text():
    writer = ReplyWriterService()
    for label, outcome, expected in (
        (classified(), None, "missing_content"),
        (classified("unsafe_request"), None, "restricted_request"),
        (classified(languages=("ur", "en")), None, "invalid_locale"),
        (classified(), Outcome("lower_urgency_unauthorized"), "missing_content"),
    ):
        result = writer.render(classification=label, outcome=outcome, now=NOW)
        assert result.text is None and result.reason == expected


@pytest.mark.parametrize("text", ["{patient}", "<script>", "\x00data", "\ud800", "\u202edata"])
def test_rejects_substitution_and_unsupported_text(text):
    with pytest.raises(ValidationError):
        Translation(language="en", text=text, review_ref="synthetic")


def test_synthetic_benchmark(signed):
    _, authority, artifact = signed
    samples = {"catalog validation": [], "signed render": [], "missing locale": []}
    item = artifact()
    for _ in range(200):
        start = perf_counter_ns()
        writer = ReplyWriterService(catalog=(item,), authorities={"sample_key": authority})
        samples["catalog validation"].append((perf_counter_ns() - start) / 1e6)
        start = perf_counter_ns()
        assert writer.render(classification=classified(), now=NOW).status == "rendered"
        samples["signed render"].append((perf_counter_ns() - start) / 1e6)
        start = perf_counter_ns()
        assert writer.render(classification=classified(languages=("ur", "en")), now=NOW).status == "unavailable"
        samples["missing locale"].append((perf_counter_ns() - start) / 1e6)
    for name, values in samples.items():
        print(f"AI-04 synthetic benchmark {name} n=200 p50={median(values):.3f} ms p95={sorted(values)[189]:.3f} ms")

def test_pending_question_binds_current_owner_and_options(signed):
    import asyncio
    from uuid import uuid4

    from roghyar.conversation import ConversationMemoryService, Question
    from roghyar.scoped_session import Principal

    _, authority, artifact = signed
    question = Question(key="sample_question", options=("one", "two"))
    owner = Principal("patient", uuid4())
    memory = ConversationMemoryService(reviewed_questions=(question,))
    state = memory.start(owner, NOW)
    pending = asyncio.run(memory.apply(state, {
        "action": "ask", "conversation_id": state.conversation_id, "event_id": uuid4(),
        "revision": 0, "question": question.key,
    }, owner, NOW)).state
    variant = Translation(language="en", text="Synthetic question?", review_ref="synthetic", options=question.options)
    item = artifact("follow_up", question=question.key, translations=(variant,))
    writer = ReplyWriterService(catalog=(item,), questions=(question,), authorities={
        "sample_key": replace(authority, scopes=frozenset({"clinical"})),
    })
    fields = dict(state=pending, owner=owner, now=NOW, revision=1, classification=classified())
    assert writer.render(**fields).options == question.options
    for changes in ({"owner": Principal("patient", uuid4())}, {"now": pending.expires_at}, {"revision": 0}):
        assert writer.render(**(fields | changes)).reason == "invalid_conversation"
    changed = Question(key=question.key, options=("one", "three"))
    writer = ReplyWriterService(catalog=(item,), questions=(changed,), authorities={
        "sample_key": replace(authority, scopes=frozenset({"clinical"})),
    })
    assert writer.render(**fields).reason == "missing_question"


def test_unsupported_catalog_schema(signed):
    _, _, artifact = signed
    with pytest.raises(ValidationError):
        artifact(schema_version=2)
