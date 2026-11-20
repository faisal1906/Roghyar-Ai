"""SYNTHETIC—DEVELOPMENT ONLY. Fictitious content and temporary software keys."""

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from roghyar.formulary_safety import (
    DOMAIN, FormularyRecord, FormularyReviewer, FormularyService, SelectionReason,
    SyntheticSelection, Vocabulary, _screen_snapshot, signature_message,
)
from roghyar.red_flags import DOMAIN as RED_FLAG_DOMAIN
from roghyar.triage import TriageService, TriageState
from test_red_flags import facts, sign_rule

CONDITION = "synthetic_condition"
MEDICINE = "synthetic_compound"
OTHER = "synthetic_alternative"
COMPATIBLE = "synthetic_compatible"
BLOCKED = "synthetic_blocked"
UNKNOWN = "synthetic_unreviewed"
ALLERGY = "synthetic_allergen"
CONTRA = "synthetic_exclusion"
VOCABULARY = Vocabulary(frozenset({MEDICINE, OTHER, COMPATIBLE, BLOCKED, UNKNOWN}),
                        frozenset({CONDITION, CONTRA}), frozenset({ALLERGY}))
DOSE = {"form": "tablet", "unit": "tablet", "instructions": "SYNTHETIC—DEVELOPMENT ONLY instructions",
        "frequency": "SYNTHETIC frequency", "maximum_per_day": "SYNTHETIC limit"}


def safety(**changes):
    return dict(age=30, pregnancy="not_pregnant", allergies=[], conditions=[],
                current_medicines=[]) | changes


@pytest.fixture
def sign_formulary():
    private = Ed25519PrivateKey.generate()
    now = datetime.now(timezone.utc)
    reviewer = FormularyReviewer(uuid4(), private.public_key(), frozenset({MEDICINE, OTHER}),
                                frozenset({CONDITION}), now - timedelta(days=1), now + timedelta(days=1))

    def signed(*, domain=DOMAIN, medicine=MEDICINE, version=1, coverage=True, children=False):
        payload = {
            "schema_version": 1, "key_id": "synthetic_key", "signature": "0" * 128, "adult": deepcopy(DOSE),
            "screening": {"pregnancy": "not_pregnant_only", "allergy_ids": [ALLERGY],
                          "blocked_conditions": [CONTRA], "interactions_reviewed": coverage,
                          "compatible_medicines": [COMPATIBLE], "blocked_medicines": [BLOCKED]},
        }
        row = dict(
            id=uuid4(), condition_code=CONDITION, medicine=medicine, version=version,
            review_status="approved", reviewed_by=reviewer.identity, reviewed_at=now - timedelta(minutes=1),
            active=True, min_age=4 if children else 18, max_age=65,
            adult_dose=DOSE["instructions"], frequency=DOSE["frequency"], max_daily=DOSE["maximum_per_day"],
            child_dose_table=[{"first_age": 5, "last_age": 10, "dose": {
                **DOSE, "instructions": "SYNTHETIC exact child instruction",
            }}] if children else None,
            contraindications=["SYNTHETIC label"], home_steps=["SYNTHETIC home text"], see_doctor_if=[],
            reviewed_payload=payload,
        )
        signed_content = signature_message(FormularyRecord.model_validate(row))
        payload["signature"] = private.sign(domain + signed_content[len(DOMAIN):]).hex()
        return row

    return signed, {"synthetic_key": reviewer}


@pytest.mark.parametrize(("changes", "expected"), [
    ({}, SelectionReason.SYNTHETIC_ELIGIBLE), ({"age": 18}, SelectionReason.SYNTHETIC_ELIGIBLE),
    ({"age": 65}, SelectionReason.SYNTHETIC_ELIGIBLE), ({"age": 66}, SelectionReason.AGE_OR_PREGNANCY_MISMATCH),
    ({"age": None}, SelectionReason.INSUFFICIENT_INFORMATION), ({"age": True}, SelectionReason.INSUFFICIENT_INFORMATION),
    ({"age": 121}, SelectionReason.INSUFFICIENT_INFORMATION),
    ({"pregnancy": None}, SelectionReason.INSUFFICIENT_INFORMATION),
    ({"pregnancy": "pregnant"}, SelectionReason.AGE_OR_PREGNANCY_MISMATCH),
    ({"allergies": [ALLERGY]}, SelectionReason.CONTRAINDICATED),
    ({"allergies": None}, SelectionReason.INSUFFICIENT_INFORMATION),
    ({"conditions": [CONTRA]}, SelectionReason.CONTRAINDICATED),
    ({"conditions": ["synthetic_unknown"]}, SelectionReason.INSUFFICIENT_INFORMATION),
    ({"current_medicines": [BLOCKED]}, SelectionReason.CONTRAINDICATED),
    ({"current_medicines": [MEDICINE]}, SelectionReason.CONTRAINDICATED),
    ({"current_medicines": [COMPATIBLE]}, SelectionReason.SYNTHETIC_ELIGIBLE),
    ({"current_medicines": [UNKNOWN]}, SelectionReason.INTERACTIONS_UNVERIFIED),
    ({"current_medicines": None}, SelectionReason.INTERACTIONS_UNVERIFIED),
])
def test_screening(sign_formulary, changes, expected):
    sign, keys = sign_formulary
    result = _screen_snapshot([sign()], safety(**changes), keys, VOCABULARY)
    assert result.reason == expected
    if isinstance(result, SyntheticSelection):
        assert result.dose.model_dump() == DOSE
    else:
        assert "dose" not in result.model_dump()


def test_signed_content_and_domains(sign_formulary):
    sign, keys = sign_formulary
    assert _screen_snapshot([sign(domain=RED_FLAG_DOMAIN)], safety(), keys, VOCABULARY).reason \
        == SelectionReason.INVALID_ATTESTATION
    key = keys["synthetic_key"]
    for authority in ({}, {"synthetic_key": replace(key, authorized_medicines=frozenset())},
                      {"synthetic_key": replace(key, valid_until=datetime.now(timezone.utc))}):
        assert _screen_snapshot([sign()], safety(), authority, VOCABULARY).reason \
            == SelectionReason.INVALID_ATTESTATION
    row = sign()
    del row["reviewed_payload"]["signature"]
    assert _screen_snapshot([row], safety(), keys, VOCABULARY).reason == SelectionReason.INVALID_ATTESTATION
    for field, value in (("id", uuid4()), ("version", 2), ("home_steps", ["tampered"])):
        row = sign()
        row[field] = value
        assert _screen_snapshot([row], safety(), keys, VOCABULARY).reason == SelectionReason.INVALID_ATTESTATION
    row = sign()
    row["adult_dose"] = row["reviewed_payload"]["adult"]["instructions"] = "SYNTHETIC changed"
    assert _screen_snapshot([sign(medicine=OTHER), row], safety(), keys, VOCABULARY).reason \
        == SelectionReason.INVALID_ATTESTATION


def test_schema_invalid_pediatric_and_selection_policy(sign_formulary):
    sign, keys = sign_formulary
    for key, value in (("schema_version", 2), ("schema_version", True), ("unknown", 1)):
        row = sign()
        row["reviewed_payload"][key] = value
        assert _screen_snapshot([row], safety(), keys, VOCABULARY).reason == SelectionReason.INVALID_CONFIGURATION
    for key in ("unit", "maximum_per_day"):
        row = sign()
        del row["reviewed_payload"]["adult"][key]
        assert _screen_snapshot([row], safety(), keys, VOCABULARY).reason == SelectionReason.INVALID_CONFIGURATION
    row = sign(children=True)
    for age in (5, 10):
        result = _screen_snapshot([row], safety(age=age), keys, VOCABULARY)
        assert result.dose.instructions == "SYNTHETIC exact child instruction"
    assert _screen_snapshot([row], safety(age=4), keys, VOCABULARY).reason \
        == SelectionReason.UNSUPPORTED_PEDIATRIC_DOSE
    row["child_dose_table"] *= 2
    assert _screen_snapshot([row], safety(age=5), keys, VOCABULARY).reason == SelectionReason.INVALID_CONFIGURATION
    assert _screen_snapshot([sign(coverage=False)], safety(), keys, VOCABULARY).reason \
        == SelectionReason.INTERACTIONS_UNVERIFIED
    for rows in ([], [{**sign(), "active": False}], [{**sign(), "review_status": "draft"}]):
        assert _screen_snapshot(rows, safety(), keys, VOCABULARY).reason == SelectionReason.NO_APPROVED_CONTENT
    assert _screen_snapshot([sign(), sign(version=2)], safety(), keys, VOCABULARY).reason \
        == SelectionReason.INVALID_CONFIGURATION
    assert _screen_snapshot([sign(), sign(medicine=OTHER)], safety(), keys, VOCABULARY).reason \
        == SelectionReason.NO_SELECTION_POLICY


def test_triage_never_opens_medicine_selection(monkeypatch, sign_rule, sign_formulary):
    signed_rule, rule_keys = sign_rule
    _, keys = sign_formulary
    rows = []
    monkeypatch.setattr("roghyar.red_flags.PostgresRuleRepository.__call__", lambda _: rows)
    service = FormularyService(None, VOCABULARY, keys, TriageService(None, rule_keys))
    for cases, evidence, state in (
        ([], facts(), TriageState.RULES_UNAVAILABLE),
        ([signed_rule()], facts(), TriageState.FURTHER_ASSESSMENT_REQUIRED),
        ([signed_rule()], facts(synthetic_first=False), TriageState.NO_REVIEWED_MATCH),
        ([signed_rule()], facts(synthetic_first=True), TriageState.REVIEWED_EMERGENCY),
        ([signed_rule(priority="urgent")], facts(synthetic_first=True), TriageState.REVIEWED_URGENT),
        ([signed_rule(priority="urgent"), signed_rule(code="synthetic_pending", include={
            "op": "eq", "fact": "synthetic_missing", "value": True,
        })], facts(synthetic_first=True), TriageState.URGENT_REVIEW_REQUIRED),
    ):
        rows[:] = cases
        result = service.evaluate(evidence)
        assert result.reason == SelectionReason.TRIAGE_NOT_AUTHORIZED
        assert result.triage_state == state
