"""Case-level integration evidence. Every case reruns a signed positive control."""

import pytest
from test_guidance import run_signed_chain, sign_rule, signed, home_fixture, sign_formulary

FIELDS = ("age", "pregnancy", "allergies", "conditions", "current_medicines")
PROVENANCE = ("unknown", "not_asked", "incomplete", "reported_complete", "verified",
              "contradictory", "unsupported", "missing_event", "foreign_event", "superseded",
              "corrected", "stale_revision", "forged_verification", "stale_digest")
AGES = ['0.adult', '1.adult', '17.adult', '18.adult', '65.adult', '66.adult', '120.adult', '4.child', '5.child', '10.child', '11.child']
AUTHORITIES = ['lower.revoked', 'lower.expired', 'lower.scope', 'lower.untrusted', 'lower.inactive', 'policy.revoked', 'policy.expired', 'policy.scope', 'policy.inactive', 'formulary.revoked', 'formulary.expired', 'formulary.scope', 'reply.revoked', 'reply.removed', 'reply.tampered', 'home.revoked', 'home.tampered']
ISSUERS = ['valid', 'owner', 'conversation', 'event', 'revision', 'field', 'value', 'vocabulary', 'expired', 'signature', 'revoked', 'scope', 'unknown_key']
CHANGED_CASES = (
    "changed:age:denied", "changed:age:allowed", "changed:pregnancy:denied", "changed:allergies:denied",
    "changed:conditions:denied", "changed:current_medicines:denied", "changed:current_medicines:allowed",
    "changed:current_medicines:unknown", "race:age",
)
CASES = list(CHANGED_CASES) + ['transplant:age:event', 'transplant:age:revision', 'transplant:age:value', 'transplant:age:new_event', 'transplant:age:second', 'transplant:age:return', 'transplant:age:field', 'transplant:age:owner', 'transplant:age:conversation', 'transplant:age:revoked', 'transplant:age:expired', 'transplant:age:scope'] + (
    [f"custody:{field}" for field in FIELDS] +
    [f"issuer:{case}" for case in ISSUERS] +
    [f"authority:{case}" for case in AUTHORITIES] +
    [f"age:{age}" for age in AGES] +
    [f"provenance:{field}.{kind}" for field in FIELDS for kind in PROVENANCE]
    + [f"lower:{kind}" for kind in ("expiry", "signature", "version", "snapshot", "evidence", "reply_version", "reply_digest")]
    + [f"medication:{kind}" for kind in ("version", "condition", "product", "formulary", "ingredients", "vocabulary")]
)


@pytest.mark.parametrize("case", CASES, ids=CASES)
def test_gd05_signed_case(case, monkeypatch, sign_rule, signed, home_fixture, sign_formulary, request):
    run_signed_chain("structured", "authorized_lower", monkeypatch, sign_rule,
                     signed, home_fixture, sign_formulary, request, case=case)


from gd05_screening_cases import CASES as SCREENING_CASES


@pytest.mark.parametrize("case", tuple(SCREENING_CASES))
def test_gd05_screening_case(case, monkeypatch, sign_rule, signed, home_fixture, sign_formulary, request):
    run_signed_chain("structured", "authorized_lower", monkeypatch, sign_rule,
                     signed, home_fixture, sign_formulary, request, case=f"screening:{case}")


CLOSEOUT_CHAIN = ("structured_version", "structured_id", "structured_digest", "condition", "applicability",
                  "vocabulary", "reviewer", "key", "conversation", "reply_id", "cross_binding", "locale")
CLOSEOUT_AGE = ("30:66", "66:30", "30:5", "5:30", "7:9", "7:8", "8:9", "4:5", "65:66",
                "9:12", "5:4", "18:17")


@pytest.mark.parametrize("case", CLOSEOUT_CHAIN)
def test_gd05_chain_closeout(case, monkeypatch, sign_rule, signed, home_fixture, sign_formulary, request):
    run_signed_chain("structured", "authorized_lower", monkeypatch, sign_rule,
                     signed, home_fixture, sign_formulary, request, case=f"chain:{case}")


@pytest.mark.parametrize("case", CLOSEOUT_AGE)
def test_gd05_age_rebinding(case, monkeypatch, sign_rule, signed, home_fixture, sign_formulary, request):
    run_signed_chain("structured", "authorized_lower", monkeypatch, sign_rule,
                     signed, home_fixture, sign_formulary, request, case=f"age_change:{case}")


CLOSEOUT_TRANSLATIONS = (
    "missing", "signature", "expired_approval", "expired_reviewer", "revoked_reviewer", "revoked_ur",
    "revoked_ps", "reviewer", "key", "locale", "translation_version", "binding", "digest", "reference",
    "scope", "trust_ref", "old_approval", "reused_variant", "text_after_signing", "metadata_after_signing",
)


@pytest.mark.parametrize("case", CLOSEOUT_TRANSLATIONS)
def test_gd05_postgres_translation(case, monkeypatch, sign_rule, signed, home_fixture, sign_formulary, request):
    run_signed_chain("structured_postgres", "authorized_lower", monkeypatch, sign_rule, signed, home_fixture,
                     sign_formulary, request, case=f"translation_db:{case}")


PEDIATRIC = {
    "contiguous": (((4, 6), (7, 10)), 7, "covered"),
    "noncontiguous": (((5, 7), (9, 11)), 9, "covered"),
    "overlap": (((4, 7), (7, 10)), 7, "invalid"),
    "reversed": (((7, 5),), 6, "invalid"),
    "duplicate": (((5, 7), (5, 7)), 5, "invalid"),
    "unsorted": (((9, 11), (5, 7)), 5, "invalid"),
    "outside_range": (((3, 7),), 4, "invalid"),
    "zero_band": (((0, 5),), 0, "invalid"),
    "missing_adult": (((5, 7),), 5, "invalid"),
    "missing_required_bands": ((), 5, "invalid"),
}


@pytest.mark.parametrize("case", tuple(PEDIATRIC))
def test_gd05_pediatric_intervals(case, sign_formulary):
    from roghyar.formulary_safety import Dose, FormularyRecord, SafetyContext, screen_verified_formulary
    from test_formulary_safety import VOCABULARY
    bands, age, expected = PEDIATRIC[case]
    make_row, _ = sign_formulary
    if case in ("missing_adult", "missing_required_bands"):
        row = make_row(children=True, bands=bands)
        row.update(adult_dose=None, frequency=None, max_daily=None)
        row["reviewed_payload"]["adult"] = None
        if case == "missing_required_bands":
            row["max_age"] = 17
        with pytest.raises(ValueError):
            FormularyRecord.model_validate(row)
    elif expected == "invalid":
        with pytest.raises(ValueError):
            make_row(children=True, bands=bands)
    else:
        row = FormularyRecord.model_validate(make_row(children=True, bands=bands))
        result = screen_verified_formulary(row, SafetyContext(age=age, pregnancy="not_pregnant", allergies=[], conditions=[], current_medicines=[]), VOCABULARY)
        assert isinstance(result, Dose)
