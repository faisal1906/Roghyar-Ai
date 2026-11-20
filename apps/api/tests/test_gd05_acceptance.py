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
CASES = (
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
