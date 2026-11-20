"""SYNTHETIC—DEVELOPMENT ONLY. No clinical facts, rules or warnings."""

from dataclasses import replace
from datetime import datetime, timezone
from statistics import median, quantiles
from time import perf_counter
from uuid import uuid4

import pytest
from sqlalchemy.exc import OperationalError

from roghyar.red_flags import Evaluation, MAX_RULES, RedFlagRuleEngine, Status
from roghyar.triage import GuidanceLevel, TriageDecision, TriageService, TriageState, WarningArtifact
from test_red_flags import FIRST, facts, sign_rule

UNKNOWN = {**FIRST, "fact": "synthetic_unresolved"}


@pytest.fixture
def engine_service(monkeypatch, sign_rule):
    signed, keys = sign_rule

    def service(rows, *, reviewer_keys=keys, warnings=None):
        # Patch only the row source. All schema and signature verification stays live.
        monkeypatch.setattr("roghyar.red_flags.PostgresRuleRepository.__call__", lambda _: rows)
        return TriageService(None, reviewer_keys, warnings)

    return signed, service, keys


@pytest.mark.parametrize("priorities,unresolved,state,floor", [
    (["urgent", "emergency"], False, TriageState.REVIEWED_EMERGENCY, GuidanceLevel.EMERGENCY),
    (["emergency", "emergency", "urgent"], True, TriageState.REVIEWED_EMERGENCY, GuidanceLevel.EMERGENCY),
    (["urgent"], False, TriageState.REVIEWED_URGENT, GuidanceLevel.URGENT),
    (["urgent"], True, TriageState.URGENT_REVIEW_REQUIRED, GuidanceLevel.URGENT),
    ([], True, TriageState.FURTHER_ASSESSMENT_REQUIRED, None),
    ([], False, TriageState.NO_REVIEWED_MATCH, None),
])
def test_precedence(engine_service, priorities, unresolved, state, floor):
    signed, service, _ = engine_service
    rows = [signed(code=f"synthetic_rule_{idx}", priority=priority, version=idx + 1)
            for idx, priority in enumerate(priorities)]
    if unresolved or not rows:
        rows.append(signed(code="synthetic_extra", include=UNKNOWN))
    evidence = facts(synthetic_first=True, **({} if unresolved else {"synthetic_unresolved": False}))
    result = service(rows).evaluate(evidence)
    repeated = service(list(reversed(rows))).evaluate(evidence)
    assert result.state == state
    assert result.urgency_floor == floor
    assert result.fingerprint == repeated.fingerprint
    assert result.matches == repeated.matches
    assert result.unresolved_evidence == (("synthetic_unresolved",) if unresolved else ())
    expected = [(priority, f"synthetic_rule_{idx}", idx + 1) for idx, priority in enumerate(priorities)]
    assert [(match.priority, match.rule_code, match.version) for match in result.matches] == sorted(expected)
    assert all(match.warning_ref == "synthetic_warning" and match.warning_sha256 == "a" * 64
               for match in result.matches)


def test_conflicting_observations_preserve_urgent_floor(engine_service):
    signed, service, _ = engine_service
    rules = [signed(priority="urgent"), signed(code="synthetic_other", include=UNKNOWN)]
    evidence = facts(synthetic_first=True, synthetic_unresolved=False)
    evidence["observations"] += [{"fact": "synthetic_unresolved", "value": True, "provenance": "verified"}]
    result = service(rules).evaluate(evidence)
    assert result.state == TriageState.URGENT_REVIEW_REQUIRED
    assert result.urgency_floor == GuidanceLevel.URGENT
    assert result.unresolved_evidence == ("synthetic_unresolved",)


def test_unavailable_and_invalid_signatures_never_produce_matches(engine_service):
    signed, service, keys = engine_service
    good = signed()
    broken = signed(code="synthetic_broken")
    broken.pattern["attestation"]["signature"] = "f" * 128
    broken_schema = signed(code="synthetic_schema")
    broken_schema.pattern["schema_version"] = 2
    key = keys["synthetic_key"]
    evidence = facts(synthetic_first=True)
    for rows, authority, reason in (
        ([], keys, Status.NO_APPROVED_RULES),
        ([good, broken], keys, Status.INVALID_CONFIGURATION),
        ([broken_schema], keys, Status.INVALID_CONFIGURATION),
        ([good, signed(version=2)], keys, Status.INVALID_CONFIGURATION),
        ([good], {}, Status.INVALID_CONFIGURATION),
        ([good], {"synthetic_key": replace(key, valid_until=datetime.now(timezone.utc))},
         Status.INVALID_CONFIGURATION),
        ([good], {"synthetic_key": replace(key, reviewer=uuid4())}, Status.INVALID_CONFIGURATION),
    ):
        outcome = service(rows, reviewer_keys=authority).evaluate(evidence)
        assert outcome.state == TriageState.RULES_UNAVAILABLE and outcome.reason == reason
        assert outcome.fingerprint is None and outcome.matches == () and outcome.urgency_floor is None


def test_bad_payload_cannot_supply_match_or_override_by_severity(engine_service, caplog):
    signed, service, _ = engine_service
    proof = service([signed()]).evaluate(facts(synthetic_first=True))
    assert proof.state == TriageState.REVIEWED_EMERGENCY
    assert proof.state == service([signed()]).evaluate(facts(synthetic_first=True)).state
    marker = "SYNTHETIC—DEVELOPMENT ONLY private text"
    for fake in (
        {"matches": proof.matches, "status": "match", "text": marker},
        Evaluation(Status.MATCH, proof.evaluated_at, proof.fingerprint, proof.matches),
        {**facts(synthetic_first=False), "severity": "severe"},
        {**facts(synthetic_first=True), "severity": "mild"},
    ):
        result = service([signed()]).evaluate(fake)
        assert result.state == TriageState.FURTHER_ASSESSMENT_REQUIRED
        assert result.urgency_floor is None and result.matches == ()
    assert marker not in caplog.text


def test_repository_and_unexpected_interpreter_failures_are_redacted(monkeypatch, caplog):
    marker = "SYNTHETIC—DEVELOPMENT ONLY exception"

    def database_failure(_):
        raise OperationalError("SELECT 1", {}, RuntimeError(marker))

    monkeypatch.setattr("roghyar.red_flags.PostgresRuleRepository.__call__", database_failure)
    service = TriageService(None)
    assert service.evaluate(facts()).state == TriageState.EVALUATION_FAILURE

    def interpreter_failure(*args):
        raise RuntimeError(marker)

    monkeypatch.setattr(RedFlagRuleEngine, "evaluate", interpreter_failure)
    result = service.evaluate(facts())
    assert result.state == TriageState.EVALUATION_FAILURE and result.matches == ()
    assert result.fingerprint is None and marker not in caplog.text


def test_result_invariants_reject_conflicts(engine_service):
    signed, service, _ = engine_service
    resolved = service([signed()]).evaluate(facts(synthetic_first=True))
    with pytest.raises(ValueError, match="verified rule snapshot"):
        replace(resolved, fingerprint=None)
    with pytest.raises(ValueError, match="Conflicting matched rule versions"):
        replace(resolved, matches=resolved.matches * 2)
    with pytest.raises(ValueError, match="status and matches disagree"):
        replace(resolved, reason=Status.NO_MATCH)
    with pytest.raises(ValueError, match="cannot carry decision evidence"):
        TriageDecision(Status.EVALUATION_FAILURE, resolved.evaluated_at, resolved.fingerprint)


def test_warning_resolution_requires_exact_current_bundle_and_language(engine_service):
    signed, service, _ = engine_service
    translations = {
        "en": "SYNTHETIC—DEVELOPMENT ONLY", "ur": "SYNTHETIC—DEVELOPMENT ONLY Urdu fixture",
        "ps": "SYNTHETIC—DEVELOPMENT ONLY Pashto fixture",
    }
    warning = WarningArtifact(reference="synthetic_warning", translations=translations)
    digest = warning.sha256()
    row = signed(warning_sha256=digest)
    evidence = facts(synthetic_first=True)
    current = service([row]).evaluate(evidence)
    selector = {"fingerprint": current.fingerprint, "rule_code": row.rule_code,
                "version": row.version, "language": "ps"}

    def repository(reference, requested_digest):
        assert (reference, requested_digest) == (warning.reference, digest)
        return warning

    live = service([row], warnings=repository)
    for language in translations:
        assert live.resolve_warning(evidence, {**selector, "language": language}) == translations[language]
    for change in ({"version": 2}, {"fingerprint": "0" * 64}, {"language": "es"},
                   {"rule_code": "synthetic_unmatched"}):
        assert live.resolve_warning(evidence, {**selector, **change}) is None
    assert live.resolve_warning(facts(synthetic_first=False), selector) is None
    assert service([signed(version=2, warning_sha256=digest)], warnings=repository).resolve_warning(
        evidence, selector,
    ) is None
    for raw in (
        None, {"reference": warning.reference, "translations": {"en": translations["en"]}},
        {"reference": "synthetic_wrong", "translations": translations},
        {"reference": warning.reference, "translations": {**translations, "ps": "changed"}},
    ):
        missing = service([row], warnings=lambda *_: raw)
        assert missing.resolve_warning(evidence, selector) is None
        assert missing.evaluate(evidence).state == TriageState.REVIEWED_EMERGENCY

    english_only = WarningArtifact(reference=warning.reference, translations={"en": translations["en"]})
    english_row = signed(warning_sha256=english_only.sha256())
    single = service([english_row], warnings=lambda *_: english_only)
    selector["fingerprint"] = single.evaluate(evidence).fingerprint
    assert single.resolve_warning(evidence, selector) is None


def test_emergency_does_not_resolve_a_lower_priority_warning(engine_service):
    signed, service, _ = engine_service
    urgent = signed(code="synthetic_urgent", priority="urgent")
    evidence = facts(synthetic_first=True)

    def never_called(*args):
        pytest.fail("A lower-priority warning lookup must not run")

    triage = service([urgent, signed()], warnings=never_called)
    selector = {"fingerprint": triage.evaluate(evidence).fingerprint,
                "rule_code": urgent.rule_code, "version": 1, "language": "en"}
    assert triage.resolve_warning(evidence, selector) is None


def test_synthetic_maximum_rule_benchmark(engine_service):
    signed, service, _ = engine_service
    conditions = [{**FIRST, "fact": f"synthetic_fact_{idx}"} for idx in range(27)]
    include = {"op": "all", "conditions": [
        {"op": "all", "conditions": conditions[start:start + 8]} for start in (0, 8, 16, 24)
    ]}
    rows = [signed(code=f"synthetic_bench_{idx:02d}", include=include) for idx in range(MAX_RULES)]
    evidence = facts(**{item["fact"]: True for item in conditions})
    triage = service(rows)
    assert len(triage.evaluate(evidence).matches) == MAX_RULES
    samples = []
    for _ in range(25):
        start = perf_counter()
        result = triage.evaluate(evidence)
        samples.append((perf_counter() - start) * 1000)
        assert result.state == TriageState.REVIEWED_EMERGENCY
    p95 = quantiles(samples, n=20, method="inclusive")[18]
    print(f"BE-04 synthetic benchmark (64 rules × 32 nodes, 25 samples, no DB): "
          f"median={median(samples):.2f} ms, p95={p95:.2f} ms")
