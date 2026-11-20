"""Bind Phase 4 evidence to actual required test outcomes."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

temp = Path(os.environ["RUNNER_TEMP"])
manifest = Path(__file__).with_name("gd05_coverage.json").read_bytes()
def unique(pairs):
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError("Duplicate manifest key")
    return result

inventory = json.loads(manifest.decode("utf-8-sig"), object_pairs_hook=unique)
requirements = inventory["requirements"]
if inventory["version"] != 3:
    raise SystemExit("Unsupported Phase 4 manifest")

mandatory = {"GD01", "GD02", "GD03-AGE", "GD03-PREG", "GD03-HISTORY", "GD04-TRANSLATION",
             "GD04-ATOMIC", "GD04-WRITER", "GD05-LANDSCAPE", "GD05-OFFLINE", "GD05-CHAIN",
             "GD05-PROVENANCE", "GD05-PREG-APPLICABILITY", "GD05-INGREDIENT", "GD05-CHAIN-POSTGRES"}
mandatory |= {"GD05-AGE-INPUT-" + suffix for suffix in (
    "NULL", "BOOLEAN", "STRING", "FRACTIONAL", "NEGATIVE", "ABOVE-MAXIMUM", "MISSING", "0", "1", "17", "18", "120",
)}
expected_cases = {'GD05-CASE-provenance:age.unknown': 'test_gd05_signed_case[provenance:age.unknown]', 'GD05-CASE-provenance:age.not_asked': 'test_gd05_signed_case[provenance:age.not_asked]', 'GD05-CASE-provenance:age.incomplete': 'test_gd05_signed_case[provenance:age.incomplete]', 'GD05-CASE-provenance:age.reported_complete': 'test_gd05_signed_case[provenance:age.reported_complete]', 'GD05-CASE-provenance:age.verified': 'test_gd05_signed_case[provenance:age.verified]', 'GD05-CASE-provenance:age.contradictory': 'test_gd05_signed_case[provenance:age.contradictory]', 'GD05-CASE-provenance:age.unsupported': 'test_gd05_signed_case[provenance:age.unsupported]', 'GD05-CASE-provenance:age.missing_event': 'test_gd05_signed_case[provenance:age.missing_event]', 'GD05-CASE-provenance:age.foreign_event': 'test_gd05_signed_case[provenance:age.foreign_event]', 'GD05-CASE-provenance:age.superseded': 'test_gd05_signed_case[provenance:age.superseded]', 'GD05-CASE-provenance:age.corrected': 'test_gd05_signed_case[provenance:age.corrected]', 'GD05-CASE-provenance:age.stale_revision': 'test_gd05_signed_case[provenance:age.stale_revision]', 'GD05-CASE-provenance:age.forged_verification': 'test_gd05_signed_case[provenance:age.forged_verification]', 'GD05-CASE-provenance:age.stale_digest': 'test_gd05_signed_case[provenance:age.stale_digest]', 'GD05-CASE-provenance:pregnancy.unknown': 'test_gd05_signed_case[provenance:pregnancy.unknown]', 'GD05-CASE-provenance:pregnancy.not_asked': 'test_gd05_signed_case[provenance:pregnancy.not_asked]', 'GD05-CASE-provenance:pregnancy.incomplete': 'test_gd05_signed_case[provenance:pregnancy.incomplete]', 'GD05-CASE-provenance:pregnancy.reported_complete': 'test_gd05_signed_case[provenance:pregnancy.reported_complete]', 'GD05-CASE-provenance:pregnancy.verified': 'test_gd05_signed_case[provenance:pregnancy.verified]', 'GD05-CASE-provenance:pregnancy.contradictory': 'test_gd05_signed_case[provenance:pregnancy.contradictory]', 'GD05-CASE-provenance:pregnancy.unsupported': 'test_gd05_signed_case[provenance:pregnancy.unsupported]', 'GD05-CASE-provenance:pregnancy.missing_event': 'test_gd05_signed_case[provenance:pregnancy.missing_event]', 'GD05-CASE-provenance:pregnancy.foreign_event': 'test_gd05_signed_case[provenance:pregnancy.foreign_event]', 'GD05-CASE-provenance:pregnancy.superseded': 'test_gd05_signed_case[provenance:pregnancy.superseded]', 'GD05-CASE-provenance:pregnancy.corrected': 'test_gd05_signed_case[provenance:pregnancy.corrected]', 'GD05-CASE-provenance:pregnancy.stale_revision': 'test_gd05_signed_case[provenance:pregnancy.stale_revision]', 'GD05-CASE-provenance:pregnancy.forged_verification': 'test_gd05_signed_case[provenance:pregnancy.forged_verification]', 'GD05-CASE-provenance:pregnancy.stale_digest': 'test_gd05_signed_case[provenance:pregnancy.stale_digest]', 'GD05-CASE-provenance:allergies.unknown': 'test_gd05_signed_case[provenance:allergies.unknown]', 'GD05-CASE-provenance:allergies.not_asked': 'test_gd05_signed_case[provenance:allergies.not_asked]', 'GD05-CASE-provenance:allergies.incomplete': 'test_gd05_signed_case[provenance:allergies.incomplete]', 'GD05-CASE-provenance:allergies.reported_complete': 'test_gd05_signed_case[provenance:allergies.reported_complete]', 'GD05-CASE-provenance:allergies.verified': 'test_gd05_signed_case[provenance:allergies.verified]', 'GD05-CASE-provenance:allergies.contradictory': 'test_gd05_signed_case[provenance:allergies.contradictory]', 'GD05-CASE-provenance:allergies.unsupported': 'test_gd05_signed_case[provenance:allergies.unsupported]', 'GD05-CASE-provenance:allergies.missing_event': 'test_gd05_signed_case[provenance:allergies.missing_event]', 'GD05-CASE-provenance:allergies.foreign_event': 'test_gd05_signed_case[provenance:allergies.foreign_event]', 'GD05-CASE-provenance:allergies.superseded': 'test_gd05_signed_case[provenance:allergies.superseded]', 'GD05-CASE-provenance:allergies.corrected': 'test_gd05_signed_case[provenance:allergies.corrected]', 'GD05-CASE-provenance:allergies.stale_revision': 'test_gd05_signed_case[provenance:allergies.stale_revision]', 'GD05-CASE-provenance:allergies.forged_verification': 'test_gd05_signed_case[provenance:allergies.forged_verification]', 'GD05-CASE-provenance:allergies.stale_digest': 'test_gd05_signed_case[provenance:allergies.stale_digest]', 'GD05-CASE-provenance:conditions.unknown': 'test_gd05_signed_case[provenance:conditions.unknown]', 'GD05-CASE-provenance:conditions.not_asked': 'test_gd05_signed_case[provenance:conditions.not_asked]', 'GD05-CASE-provenance:conditions.incomplete': 'test_gd05_signed_case[provenance:conditions.incomplete]', 'GD05-CASE-provenance:conditions.reported_complete': 'test_gd05_signed_case[provenance:conditions.reported_complete]', 'GD05-CASE-provenance:conditions.verified': 'test_gd05_signed_case[provenance:conditions.verified]', 'GD05-CASE-provenance:conditions.contradictory': 'test_gd05_signed_case[provenance:conditions.contradictory]', 'GD05-CASE-provenance:conditions.unsupported': 'test_gd05_signed_case[provenance:conditions.unsupported]', 'GD05-CASE-provenance:conditions.missing_event': 'test_gd05_signed_case[provenance:conditions.missing_event]', 'GD05-CASE-provenance:conditions.foreign_event': 'test_gd05_signed_case[provenance:conditions.foreign_event]', 'GD05-CASE-provenance:conditions.superseded': 'test_gd05_signed_case[provenance:conditions.superseded]', 'GD05-CASE-provenance:conditions.corrected': 'test_gd05_signed_case[provenance:conditions.corrected]', 'GD05-CASE-provenance:conditions.stale_revision': 'test_gd05_signed_case[provenance:conditions.stale_revision]', 'GD05-CASE-provenance:conditions.forged_verification': 'test_gd05_signed_case[provenance:conditions.forged_verification]', 'GD05-CASE-provenance:conditions.stale_digest': 'test_gd05_signed_case[provenance:conditions.stale_digest]', 'GD05-CASE-provenance:current_medicines.unknown': 'test_gd05_signed_case[provenance:current_medicines.unknown]', 'GD05-CASE-provenance:current_medicines.not_asked': 'test_gd05_signed_case[provenance:current_medicines.not_asked]', 'GD05-CASE-provenance:current_medicines.incomplete': 'test_gd05_signed_case[provenance:current_medicines.incomplete]', 'GD05-CASE-provenance:current_medicines.reported_complete': 'test_gd05_signed_case[provenance:current_medicines.reported_complete]', 'GD05-CASE-provenance:current_medicines.verified': 'test_gd05_signed_case[provenance:current_medicines.verified]', 'GD05-CASE-provenance:current_medicines.contradictory': 'test_gd05_signed_case[provenance:current_medicines.contradictory]', 'GD05-CASE-provenance:current_medicines.unsupported': 'test_gd05_signed_case[provenance:current_medicines.unsupported]', 'GD05-CASE-provenance:current_medicines.missing_event': 'test_gd05_signed_case[provenance:current_medicines.missing_event]', 'GD05-CASE-provenance:current_medicines.foreign_event': 'test_gd05_signed_case[provenance:current_medicines.foreign_event]', 'GD05-CASE-provenance:current_medicines.superseded': 'test_gd05_signed_case[provenance:current_medicines.superseded]', 'GD05-CASE-provenance:current_medicines.corrected': 'test_gd05_signed_case[provenance:current_medicines.corrected]', 'GD05-CASE-provenance:current_medicines.stale_revision': 'test_gd05_signed_case[provenance:current_medicines.stale_revision]', 'GD05-CASE-provenance:current_medicines.forged_verification': 'test_gd05_signed_case[provenance:current_medicines.forged_verification]', 'GD05-CASE-provenance:current_medicines.stale_digest': 'test_gd05_signed_case[provenance:current_medicines.stale_digest]', 'GD05-CASE-lower:expiry': 'test_gd05_signed_case[lower:expiry]', 'GD05-CASE-lower:signature': 'test_gd05_signed_case[lower:signature]', 'GD05-CASE-lower:version': 'test_gd05_signed_case[lower:version]', 'GD05-CASE-lower:snapshot': 'test_gd05_signed_case[lower:snapshot]', 'GD05-CASE-lower:evidence': 'test_gd05_signed_case[lower:evidence]', 'GD05-CASE-lower:reply_version': 'test_gd05_signed_case[lower:reply_version]', 'GD05-CASE-lower:reply_digest': 'test_gd05_signed_case[lower:reply_digest]', 'GD05-CASE-medication:version': 'test_gd05_signed_case[medication:version]', 'GD05-CASE-medication:condition': 'test_gd05_signed_case[medication:condition]', 'GD05-CASE-medication:product': 'test_gd05_signed_case[medication:product]', 'GD05-CASE-medication:formulary': 'test_gd05_signed_case[medication:formulary]', 'GD05-CASE-medication:ingredients': 'test_gd05_signed_case[medication:ingredients]', 'GD05-CASE-medication:vocabulary': 'test_gd05_signed_case[medication:vocabulary]'}
expected_cases.update({
    f"GD05-CASE-age:{age}": f"test_gd05_signed_case[age:{age}]"
    for age in ['0.adult', '1.adult', '17.adult', '18.adult', '65.adult', '66.adult', '120.adult', '4.child', '5.child', '10.child', '11.child']
})
expected_cases.update({
    f"GD05-CASE-authority:{case}": f"test_gd05_signed_case[authority:{case}]"
    for case in ['lower.revoked', 'lower.expired', 'lower.scope', 'lower.untrusted', 'lower.inactive', 'policy.revoked', 'policy.expired', 'policy.scope', 'policy.inactive', 'formulary.revoked', 'formulary.expired', 'formulary.scope', 'reply.revoked', 'reply.removed', 'reply.tampered', 'home.revoked', 'home.tampered']
})
expected_cases.update({
    f"GD05-CASE-issuer:{case}": f"test_gd05_signed_case[issuer:{case}]"
    for case in ['valid', 'owner', 'conversation', 'event', 'revision', 'field', 'value', 'vocabulary', 'expired', 'signature', 'revoked', 'scope', 'unknown_key']
})
expected_cases.update({
    f"GD05-CASE-custody:{field}": f"test_gd05_signed_case[custody:{field}]"
    for field in ("age", "pregnancy", "allergies", "conditions", "current_medicines")
})
expected_cases.update({f"GD05-CASE-{c}": f"test_gd05_signed_case[{c}]" for c in ('changed:age:denied', 'changed:age:allowed', 'changed:pregnancy:denied', 'changed:allergies:denied', 'changed:conditions:denied', 'changed:current_medicines:denied', 'changed:current_medicines:allowed', 'changed:current_medicines:unknown', 'race:age')})
expected_cases.update({f"GD05-CASE-{c}": f"test_gd05_signed_case[{c}]" for c in ['transplant:age:event', 'transplant:age:revision', 'transplant:age:value', 'transplant:age:new_event', 'transplant:age:second', 'transplant:age:return', 'transplant:age:field', 'transplant:age:owner', 'transplant:age:conversation', 'transplant:age:revoked', 'transplant:age:expired', 'transplant:age:scope']})
screening_cases = {
    "canonical": "synthetic_eligible",
    "alias": "synthetic_eligible",
    "unknown_alias": "interaction_coverage_missing",
    "ambiguous_alias": "interaction_coverage_missing",
    "shared_formulation_alias": "interaction_coverage_missing",
    "formulary_binding": "binding_mismatch",
    "empty_registry": "interaction_coverage_missing",
    "identity_revoked": "interaction_coverage_missing",
    "identity_expired": "interaction_coverage_missing",
    "identity_signature": "interaction_coverage_missing",
    "identity_scope": "interaction_coverage_missing",
    "identity_vocabulary": "interaction_coverage_missing",
    "alias_changed": "interaction_coverage_missing",
    "inactive_alias": "interaction_coverage_missing",
    "duplicate_aliases": "duplicate_ingredient_risk",
    "single_ingredient": "synthetic_eligible",
    "combination": "synthetic_eligible",
    "partial": "interaction_coverage_missing",
    "duplicate_ingredient": "interaction_coverage_missing",
    "unknown_ingredient": "interaction_coverage_missing",
    "ingredient_changed": "interaction_coverage_missing",
    "stale_product_version": "interaction_coverage_missing",
    "reused_mapping": "interaction_coverage_missing",
    "ingredient_revoked": "interaction_coverage_missing",
    "ingredient_expired": "interaction_coverage_missing",
    "ingredient_signature": "interaction_coverage_missing",
    "policy_mapping_mismatch": "interaction_coverage_missing",
    "ingredient_overlap": "duplicate_ingredient_risk",
    "missing_mapping": "interaction_coverage_missing",
    "no_current": "synthetic_eligible",
    "one_current": "synthetic_eligible",
    "two_current": "synthetic_eligible",
    "pairwise_only": "interaction_coverage_missing",
    "blocked_set": "known_contraindication",
    "unknown_product": "interaction_coverage_missing",
    "extra_member": "interaction_coverage_missing",
    "removed_member": "interaction_coverage_missing",
    "reordered": "synthetic_eligible",
    "new_mapping_version": "interaction_coverage_missing",
    "interaction_revoked": "interaction_coverage_missing",
    "interaction_expired": "interaction_coverage_missing",
    "interaction_signature": "interaction_coverage_missing",
    "wrong_owner": "invalid_context",
    "wrong_policy": "binding_mismatch",
    "pregnant_restricted": "age_not_covered",
    "pregnant_applicable": "synthetic_eligible",
    "not_pregnant": "synthetic_eligible",
    "pregnancy_unknown": "unknown_pregnancy_status",
    "pregnancy_contradiction": "unknown_pregnancy_status",
    "pregnancy_unsupported": "unknown_pregnancy_status",
    "pregnancy_verified_applicability": "synthetic_eligible",
    "pregnancy_stale_policy": "binding_mismatch",
    "pregnancy_fresh_policy": "age_not_covered",
    "be05_coverage_required": "interaction_coverage_missing",
    "authority_not_independent": "interaction_coverage_missing",
    "be05_blocks_reviewed_set": "known_contraindication",
    "missing_custody": "binding_mismatch",
}
screening_mapping = {f"GD05-SCREENING-{case}": ["backend", f"test_gd05_screening_case[{case}]", reason]
                     for case, reason in screening_cases.items()}
if any(requirements.get(key) != mapping for key, mapping in screening_mapping.items()):
    raise SystemExit("Mandatory governed screening identity or expected result mismatch")
mandatory |= screening_mapping.keys()
closeout = {
    "GD05-CLOSEOUT-CHAIN-structured_version": ["backend", "test_gd05_chain_closeout[structured_version]", "missing_content"],
    "GD05-CLOSEOUT-CHAIN-structured_id": ["backend", "test_gd05_chain_closeout[structured_id]", "missing_content"],
    "GD05-CLOSEOUT-CHAIN-structured_digest": ["backend", "test_gd05_chain_closeout[structured_digest]", "missing_content"],
    "GD05-CLOSEOUT-CHAIN-condition": ["backend", "test_gd05_chain_closeout[condition]", "missing_content"],
    "GD05-CLOSEOUT-CHAIN-applicability": ["backend", "test_gd05_chain_closeout[applicability]", "missing_content"],
    "GD05-CLOSEOUT-CHAIN-vocabulary": ["backend", "test_gd05_chain_closeout[vocabulary]", "policy_mismatch"],
    "GD05-CLOSEOUT-CHAIN-reviewer": ["backend", "test_gd05_chain_closeout[reviewer]", "invalid_policy"],
    "GD05-CLOSEOUT-CHAIN-key": ["backend", "test_gd05_chain_closeout[key]", "invalid_policy"],
    "GD05-CLOSEOUT-CHAIN-conversation": ["backend", "test_gd05_chain_closeout[conversation]", "policy_mismatch"],
    "GD05-CLOSEOUT-CHAIN-reply_id": ["backend", "test_gd05_chain_closeout[reply_id]", "missing_content"],
    "GD05-CLOSEOUT-CHAIN-cross_binding": ["backend", "test_gd05_chain_closeout[cross_binding]", "missing_content"],
    "GD05-CLOSEOUT-CHAIN-locale": ["backend", "test_gd05_chain_closeout[locale]", "missing_content"],
    "GD05-CLOSEOUT-TRANSLATION-missing": ["backend", "test_gd05_postgres_translation[missing]", "publication translation_coverage"],
    "GD05-CLOSEOUT-TRANSLATION-signature": ["backend", "test_gd05_postgres_translation[signature]", "publication translation_signature"],
    "GD05-CLOSEOUT-TRANSLATION-expired_approval": ["backend", "test_gd05_postgres_translation[expired_approval]", "publication translation_authority"],
    "GD05-CLOSEOUT-TRANSLATION-expired_reviewer": ["backend", "test_gd05_postgres_translation[expired_reviewer]", "restricted reader invalid_artifact"],
    "GD05-CLOSEOUT-TRANSLATION-revoked_reviewer": ["backend", "test_gd05_postgres_translation[revoked_reviewer]", "restricted reader invalid_artifact"],
    "GD05-CLOSEOUT-TRANSLATION-revoked_ur": ["backend", "test_gd05_postgres_translation[revoked_ur]", "restricted reader invalid_artifact"],
    "GD05-CLOSEOUT-TRANSLATION-revoked_ps": ["backend", "test_gd05_postgres_translation[revoked_ps]", "restricted reader invalid_artifact"],
    "GD05-CLOSEOUT-TRANSLATION-reviewer": ["backend", "test_gd05_postgres_translation[reviewer]", "publication translation_authority"],
    "GD05-CLOSEOUT-TRANSLATION-key": ["backend", "test_gd05_postgres_translation[key]", "publication translation_authority"],
    "GD05-CLOSEOUT-TRANSLATION-locale": ["backend", "test_gd05_postgres_translation[locale]", "publication translation_coverage"],
    "GD05-CLOSEOUT-TRANSLATION-translation_version": ["backend", "test_gd05_postgres_translation[translation_version]", "publication translation_signature"],
    "GD05-CLOSEOUT-TRANSLATION-binding": ["backend", "test_gd05_postgres_translation[binding]", "publication translation_authority"],
    "GD05-CLOSEOUT-TRANSLATION-digest": ["backend", "test_gd05_postgres_translation[digest]", "publication translation_authority"],
    "GD05-CLOSEOUT-TRANSLATION-reference": ["backend", "test_gd05_postgres_translation[reference]", "publication translation_authority"],
    "GD05-CLOSEOUT-TRANSLATION-scope": ["backend", "test_gd05_postgres_translation[scope]", "restricted reader invalid_artifact"],
    "GD05-CLOSEOUT-TRANSLATION-trust_ref": ["backend", "test_gd05_postgres_translation[trust_ref]", "restricted reader invalid_artifact"],
    "GD05-CLOSEOUT-TRANSLATION-old_approval": ["backend", "test_gd05_postgres_translation[old_approval]", "publication translation_authority"],
    "GD05-CLOSEOUT-TRANSLATION-reused_variant": ["backend", "test_gd05_postgres_translation[reused_variant]", "publication translation_signature"],
    "GD05-CLOSEOUT-TRANSLATION-text_after_signing": ["backend", "test_gd05_postgres_translation[text_after_signing]", "publication translation_signature"],
    "GD05-CLOSEOUT-TRANSLATION-metadata_after_signing": ["backend", "test_gd05_postgres_translation[metadata_after_signing]", "publication translation_signature"],
    "GD05-CLOSEOUT-AGE-REBIND-30:66": ["backend", "test_gd05_age_rebinding[30:66]", "age_not_covered"],
    "GD05-CLOSEOUT-AGE-REBIND-66:30": ["backend", "test_gd05_age_rebinding[66:30]", "synthetic_eligible"],
    "GD05-CLOSEOUT-AGE-REBIND-30:5": ["backend", "test_gd05_age_rebinding[30:5]", "synthetic_eligible"],
    "GD05-CLOSEOUT-AGE-REBIND-5:30": ["backend", "test_gd05_age_rebinding[5:30]", "synthetic_eligible"],
    "GD05-CLOSEOUT-AGE-REBIND-7:9": ["backend", "test_gd05_age_rebinding[7:9]", "synthetic_eligible"],
    "GD05-CLOSEOUT-AGE-REBIND-7:8": ["backend", "test_gd05_age_rebinding[7:8]", "pediatric_dose_unsupported"],
    "GD05-CLOSEOUT-AGE-REBIND-8:9": ["backend", "test_gd05_age_rebinding[8:9]", "synthetic_eligible"],
    "GD05-CLOSEOUT-AGE-REBIND-4:5": ["backend", "test_gd05_age_rebinding[4:5]", "synthetic_eligible"],
    "GD05-CLOSEOUT-AGE-REBIND-65:66": ["backend", "test_gd05_age_rebinding[65:66]", "age_not_covered"],
    "GD05-CLOSEOUT-AGE-REBIND-9:12": ["backend", "test_gd05_age_rebinding[9:12]", "pediatric_dose_unsupported"],
    "GD05-CLOSEOUT-AGE-REBIND-5:4": ["backend", "test_gd05_age_rebinding[5:4]", "pediatric_dose_unsupported"],
    "GD05-CLOSEOUT-AGE-REBIND-18:17": ["backend", "test_gd05_age_rebinding[18:17]", "pediatric_dose_unsupported"],
    "GD05-CLOSEOUT-PEDIATRIC-contiguous": ["backend", "test_gd05_pediatric_intervals[contiguous]", "covered"],
    "GD05-CLOSEOUT-PEDIATRIC-noncontiguous": ["backend", "test_gd05_pediatric_intervals[noncontiguous]", "covered"],
    "GD05-CLOSEOUT-PEDIATRIC-overlap": ["backend", "test_gd05_pediatric_intervals[overlap]", "invalid"],
    "GD05-CLOSEOUT-PEDIATRIC-reversed": ["backend", "test_gd05_pediatric_intervals[reversed]", "invalid"],
    "GD05-CLOSEOUT-PEDIATRIC-duplicate": ["backend", "test_gd05_pediatric_intervals[duplicate]", "invalid"],
    "GD05-CLOSEOUT-PEDIATRIC-unsorted": ["backend", "test_gd05_pediatric_intervals[unsorted]", "invalid"],
    "GD05-CLOSEOUT-PEDIATRIC-outside_range": ["backend", "test_gd05_pediatric_intervals[outside_range]", "invalid"],
    "GD05-CLOSEOUT-PEDIATRIC-zero_band": ["backend", "test_gd05_pediatric_intervals[zero_band]", "invalid"],
    "GD05-CLOSEOUT-PEDIATRIC-missing_adult": ["backend", "test_gd05_pediatric_intervals[missing_adult]", "invalid"],
    "GD05-CLOSEOUT-PEDIATRIC-missing_required_bands": ["backend", "test_gd05_pediatric_intervals[missing_required_bands]", "invalid"],
}
if any(requirements.get(key) != requirement for key, requirement in closeout.items()):
    raise SystemExit("Mandatory chain or age mapping differs from the acceptance catalog")
mandatory |= closeout.keys()
mandatory |= expected_cases.keys()
if any(requirements.get(key) != ["backend", name, "Signed positive control and exact boundary assertion"]
       for key, name in expected_cases.items()):
    raise SystemExit("Mandatory signed case identity mismatch")
if requirements.keys() != mandatory or len({(row[0], row[1]) for row in requirements.values()}) != len(requirements):
    raise SystemExit("Invalid Phase 4 manifest")
suites = {name: list(ET.parse(temp / f"phase3-{name}.xml").iter("testcase"))
          for name in ("backend", "browser")}
results = {}
for key, (suite, test, expected) in requirements.items():
    matches = [node for node in suites[suite] if node.get("name") == test]
    status = "NOT_TESTED"
    if len(matches) == 1 and matches[0].find("skipped") is None:
        status = "FAIL" if any(matches[0].find(tag) is not None for tag in ("failure", "error")) else "PASS"
    results[key] = dict(suite=suite, test=test, expected=expected, result=status)
log = (temp / "phase2-backend.log").read_text(encoding="utf-8")
benchmarks = {name: float(value) for name, value in re.findall(
    r"GD-05 publication (\w+) n=1 elapsed_ms=([\d.]+)", log)}
sha = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
engineering_ok = sha == os.environ["VERIFIED_HEAD"] and os.environ["REGRESSION_STATUS"] == "success"
publication_ok = all(results[key]["result"] == "PASS" for key in mandatory if key.startswith("GD04"))
publication_ok &= benchmarks.keys() == {"stage", "review", "ready", "activate", "replace", "rollback", "revoke", "post_revoke"}
uncovered = {}
provenance_keys = {key for key in mandatory if key.startswith(tuple(
    f"GD05-CASE-{kind}:" for kind in ("provenance", "issuer", "custody", "changed", "race", "transplant")
))} | {"GD05-PROVENANCE"}
regression_cases = ("changed_missing-1", "transplant_missing-1", "transplant_skipped-1",
                    "transplant_failed-1", "transplant_duplicate-1", "transplant_renamed-1",
                    "missing_id-1", "edited_gaps-0")
regression_results = {}
for case in regression_cases:
    name = f"test_reporter_evidence_requirements[{case}]"
    matching = [node for node in suites["backend"] if node.get("name") == name]
    regression_results[name] = ("PASS" if len(matching) == 1 and not any(
        matching[0].find(tag) is not None for tag in ("failure", "error", "skipped")) else "UNVERIFIED")
provenance_ok = (engineering_ok and all(results[key]["result"] == "PASS" for key in provenance_keys)
                 and all(status == "PASS" for status in regression_results.values()))
if not provenance_ok:
    uncovered["GD05-PROVENANCE-INVENTORY"] = "Required provenance cases, reporter regressions or exact-head CI unverified"
screening_regressions = {}
for case in ("screening_missing", "screening_skipped", "screening_failed", "screening_duplicate",
             "screening_renamed", "screening_wrong_expected", "ingredient_missing",
             "exact_set_missing", "screening_metadata_tampered", "regression_failure"):
    name = f"test_reporter_evidence_requirements[{case}-1]"
    matching = [node for node in suites["backend"] if node.get("name") == name]
    screening_regressions[name] = ("PASS" if len(matching) == 1 and not any(
        matching[0].find(tag) is not None for tag in ("failure", "error", "skipped")) else "UNVERIFIED")
screening_ok = (engineering_ok and all(results[key]["result"] == "PASS" for key in screening_mapping)
                and all(status == "PASS" for status in screening_regressions.values()))
if not screening_ok:
    uncovered["GD05-SCREENING-INVENTORY"] = "Required governed screening cases or exact-head CI unverified"
chain_keys = {key for key in closeout if key.startswith(("GD05-CLOSEOUT-CHAIN-", "GD05-CLOSEOUT-TRANSLATION-"))}
chain_keys |= {"GD05-CHAIN", "GD05-CHAIN-POSTGRES"} | {
    key for key in expected_cases if key.startswith(("GD05-CASE-lower:", "GD05-CASE-authority:", "GD05-CASE-medication:"))}
age_keys = {key for key in mandatory if key.startswith(("GD05-AGE-INPUT-", "GD05-CASE-age:",
    "GD05-CLOSEOUT-AGE-REBIND-", "GD05-CLOSEOUT-PEDIATRIC-"))} | {"GD03-AGE"}
chain_ok = engineering_ok and all(results[key]["result"] == "PASS" for key in chain_keys)
age_ok = engineering_ok and all(results[key]["result"] == "PASS" for key in age_keys)
if not chain_ok:
    uncovered["GD05-CHAIN-MUTATIONS"] = "Missing exact-head signed-chain or restricted PostgreSQL evidence"
if not age_ok:
    uncovered["GD05-AGE-INVENTORY"] = "Missing exact-head signed age or pediatric interval evidence"
for key, result in results.items():
    if result["result"] != "PASS":
        uncovered[key] = result["result"]
if not engineering_ok:
    uncovered["GD05-EXACT-HEAD"] = "Regression pipeline or SHA verification failed"
if not publication_ok:
    uncovered["GD04-PUBLICATION"] = "Publication tests or timing evidence incomplete"
def engineering_gates(missing, pipeline_ok, publishing_ok):
    return {
        "publication": "PASS" if pipeline_ok and publishing_ok else "OPEN",
        "gd05": "PASS" if pipeline_ok and not missing else "OPEN",
        "phase4": "PASS" if pipeline_ok and publishing_ok and not missing else "OPEN",
        "clinical": "EXTERNAL_VALIDATION_REQUIRED",
        "physical_device": "EXTERNAL_VALIDATION_REQUIRED",
        "real_model": "NOT_EVALUATED",
        "production": "CLOSED",
    }

frontend = re.findall(r"^# pass (\d+)$", (temp / "phase2-unit.log").read_text(encoding="utf-8"), re.M)
if len(frontend) != 1 or int(frontend[0]) == 0:
    raise SystemExit("Missing frontend verification")
report = dict(chain_mutations="PASS" if chain_ok else "OPEN", age="PASS" if age_ok else "OPEN",
              screening="PASS" if screening_ok else "OPEN", screening_reporter_regressions=screening_regressions,
              provenance="PASS" if provenance_ok else "OPEN",
              provenance_reporter_regressions=regression_results, external=inventory["external"], uncovered=uncovered,
              frontend_passed=int(frontend[0]), commit=sha, manifest_sha256=hashlib.sha256(manifest).hexdigest(),
              coverage=results, totals={key: len(rows) for key, rows in suites.items()},
              publication_single_sample_ms=benchmarks,
              **engineering_gates(uncovered, engineering_ok, publication_ok))
path = temp / "phase4-acceptance.json"
path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
sys.exit(0 if engineering_ok and publication_ok and not uncovered else 1)
