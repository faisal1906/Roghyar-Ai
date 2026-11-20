"""SYNTHETIC—DEVELOPMENT ONLY. No clinical rules or clinician signatures."""

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy.exc import OperationalError

from roghyar.red_flags import (
    MAX_RULES, Category, RedFlagRuleEngine, ReviewerKey, RuleRecord, Status,
    attestation_payload, validate_pattern,
)

FIRST = {"op": "eq", "fact": "synthetic_first", "value": True}
SECOND = {"op": "gte", "fact": "synthetic_second", "value": 7}


def facts(**items):
    return {"observations": [
        {"fact": key, "value": value, "provenance": "unknown" if value is None else "reported"}
        for key, value in items.items()
    ]}


@pytest.fixture
def sign_rule():
    # Ephemeral software-test key. The application has no corresponding trust anchor.
    private = Ed25519PrivateKey.generate()
    reviewer = uuid4()
    now = datetime.now(timezone.utc)
    key = ReviewerKey(reviewer, private.public_key(), frozenset(Category),
                      now - timedelta(days=1), now + timedelta(days=1))

    def signed(*, code="synthetic_rule", version=1, category=Category.HEART, priority="emergency",
               include=FIRST, exclude=None):
        pattern = {
            "schema_version": 1, "include": deepcopy(include), "exclude": deepcopy(exclude),
            "warning_ref": "synthetic_warning", "warning_sha256": "a" * 64,
            "attestation": {"key_id": "synthetic_key", "signature": "0" * 128},
        }
        record = RuleRecord(code, version, str(category), priority, pattern, "approved",
                            reviewer, now - timedelta(minutes=1), True)
        signature = private.sign(attestation_payload(record, validate_pattern(pattern)))
        pattern["attestation"]["signature"] = signature.hex()
        return record

    return signed, {"synthetic_key": key}


@pytest.mark.parametrize(("op", "values", "expected"), [
    ("all", {"synthetic_first": True, "synthetic_second": 7}, Status.MATCH),
    ("all", {"synthetic_first": True}, Status.INCOMPLETE_EVIDENCE),
    ("all", {"synthetic_first": False}, Status.NO_MATCH),
    ("any", {"synthetic_first": True}, Status.MATCH),
    ("any", {"synthetic_first": False}, Status.INCOMPLETE_EVIDENCE),
    ("any", {"synthetic_first": False, "synthetic_second": 6}, Status.NO_MATCH),
])
def test_three_valued_groups(sign_rule, op, values, expected):
    sign, keys = sign_rule
    rule = sign(include={"op": op, "conditions": [FIRST, SECOND]})
    result = RedFlagRuleEngine(lambda: [rule], keys).evaluate(facts(**values))
    assert result.status is expected
    assert result.evaluated_at.utcoffset().total_seconds() == 0
    assert bool(result.matches) is (expected is Status.MATCH)
    if result.matches:
        assert result.matches[0].evidence == (
            ("synthetic_first", "synthetic_second") if op == "all" else ("synthetic_first",)
        )


@pytest.mark.parametrize("source", ["untrusted", "unknown", "unsupported"])
def test_unknown_exclusion_is_never_assumed_absent(sign_rule, source):
    sign, keys = sign_rule
    rule = sign(exclude=SECOND)
    engine = RedFlagRuleEngine(lambda: [rule], keys)
    observations = facts(synthetic_first=True)["observations"]
    observations.append({"fact": "synthetic_second", "provenance": source})
    result = engine.evaluate({"observations": observations})
    assert result.status is Status.INCOMPLETE_EVIDENCE
    assert result.unresolved_evidence == ("synthetic_second",)


def test_exclusion_conflicts_type_mismatch_and_no_safe_conclusion(sign_rule):
    sign, keys = sign_rule
    engine = RedFlagRuleEngine(lambda: [sign(exclude=SECOND)], keys)
    confirmed = facts(synthetic_first=True, synthetic_second=7)
    assert engine.evaluate(confirmed).status is Status.NO_MATCH
    assert engine.evaluate(facts(synthetic_first=True, synthetic_second=6)).status is Status.MATCH
    confirmed["observations"].append({"fact": "synthetic_second", "value": 6, "provenance": "verified"})
    assert engine.evaluate(confirmed).status is Status.INCOMPLETE_EVIDENCE
    for invalid in (facts(synthetic_first=1), facts(synthetic_first="true"), {"text": "private synthetic text"}):
        assert engine.evaluate(invalid).status is Status.INCOMPLETE_EVIDENCE


def test_all_categories_and_stable_versioned_fingerprint(sign_rule):
    sign, keys = sign_rule
    rows = [sign(code=f"synthetic_{category}", category=category, version=2) for category in Category]
    evidence = facts(synthetic_first=True)
    first = RedFlagRuleEngine(lambda: rows, keys).evaluate(evidence)
    second = RedFlagRuleEngine(lambda: list(reversed(rows)), keys).evaluate(evidence)
    assert first.matches == second.matches and first.fingerprint == second.fingerprint
    assert {match.category for match in first.matches} == set(Category)
    assert {match.rule_code for match in first.matches} == {row.rule_code for row in rows}
    assert all(match.version == 2 for match in first.matches)
    assert all(match.warning_ref == "synthetic_warning" and match.priority == "emergency"
               for match in first.matches)
    changed = sign(code=rows[0].rule_code, category=Category.HEART, version=3)
    assert RedFlagRuleEngine(lambda: [changed, *rows[1:]], keys).evaluate(evidence).fingerprint != first.fingerprint


@pytest.mark.parametrize(("op", "matching", "nonmatching"), [
    ("eq", 7, 6), ("ne", 6, 7), ("lt", 6, 7), ("lte", 7, 8), ("gt", 8, 7), ("gte", 7, 6),
])
def test_typed_operator_and_reviewed_priority(sign_rule, op, matching, nonmatching):
    sign, keys = sign_rule
    row = sign(priority="urgent", include={**SECOND, "op": op})
    engine = RedFlagRuleEngine(lambda: [row], keys)
    assert engine.evaluate(facts(synthetic_second=matching)).matches[0].priority == "urgent"
    assert engine.evaluate(facts(synthetic_second=nonmatching)).status is Status.NO_MATCH


@pytest.mark.parametrize("key,value", [
    ("schema_version", 2), ("schema_version", True), ("warning_sha256", "guess"),
    ("include", {"op": "regexp", "value": ".*"}),
    ("include", {"op": "eq", "fact": "synthetic_first", "value": "true"}),
    ("include", {"op": "gt", "fact": "synthetic_first", "value": True}),
    ("include", {"op": "all", "conditions": []}),
    ("include", {"op": "any", "conditions": [FIRST] * 9}),
    ("include", {"op": "eq", "fact": "synthetic_first", "value": True, "sql": "SELECT 1"}),
    ("include", {"op": "all", "conditions": [{"op": "any", "conditions": [FIRST] * 8}] * 8}),
    ("include", {"op": "all", "conditions": [{"op": "all", "conditions": [
        {"op": "all", "conditions": [{"op": "all", "conditions": [FIRST]}]}
    ]}]}),
])
def test_schema_rejects_unsafe_or_unbounded_patterns(sign_rule, key, value):
    sign, keys = sign_rule
    row = sign()
    row.pattern[key] = value
    assert RedFlagRuleEngine(lambda: [row], keys).evaluate(facts()).status is Status.INVALID_CONFIGURATION


def test_no_approval_invalid_signatures_duplicates_and_no_partial_matches(sign_rule):
    sign, keys = sign_rule
    good = sign()
    for rows in ([], [replace(good, active=False)], [replace(good, active=False, review_status="draft")]):
        assert RedFlagRuleEngine(lambda: rows, keys).evaluate(facts()).status is Status.NO_APPROVED_RULES
    evidence = facts(synthetic_first=True)
    assert RedFlagRuleEngine(lambda: [good]).evaluate(evidence).status is Status.INVALID_CONFIGURATION
    key = keys["synthetic_key"]
    for modified in (replace(key, reviewer=uuid4()), replace(key, categories=frozenset()),
                     replace(key, valid_until=datetime.now(timezone.utc))):
        assert RedFlagRuleEngine(lambda: [good], {"synthetic_key": modified}).evaluate(evidence).status \
            is Status.INVALID_CONFIGURATION
    wrong = sign(code="synthetic_wrong")
    wrong.pattern["include"]["value"] = False
    wrong_warning = sign(code="synthetic_warning_tamper")
    wrong_warning.pattern["warning_sha256"] = "b" * 64
    for bad in ([good, wrong], [good, wrong_warning], [good, good],
                [good, sign(version=2)], [good] * (MAX_RULES + 1),
                [replace(good, review_status="draft")], [replace(good, pattern="{}")]):
        result = RedFlagRuleEngine(lambda: bad, keys).evaluate(evidence)
        assert result.status is Status.INVALID_CONFIGURATION and result.matches == ()


def test_repository_failure_is_redacted(caplog):
    marker = "SYNTHETIC private details"

    def failure():
        raise OperationalError("SELECT 1", {"synthetic": marker}, ConnectionError(marker))

    assert RedFlagRuleEngine(failure).evaluate({"text": marker}).status is Status.EVALUATION_FAILURE
    assert marker not in caplog.text


def test_unexpected_evaluation_failure_exposes_no_partial_result(sign_rule, monkeypatch, caplog):
    sign, keys = sign_rule

    def failure(*args):
        raise RuntimeError("SYNTHETIC private marker")

    monkeypatch.setattr("roghyar.red_flags.decide", failure)
    result = RedFlagRuleEngine(lambda: [sign()], keys).evaluate(facts(synthetic_first=True))
    assert result.status is Status.EVALUATION_FAILURE and result.matches == ()
    assert "SYNTHETIC private marker" not in caplog.text
