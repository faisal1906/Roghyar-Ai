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
# Track independently required inventories until each has a complete case-level mapping.
required_open = {"GD05-CHAIN-MUTATIONS", "GD05-AGE-INVENTORY",
                 "GD05-PROVENANCE-INVENTORY", "GD05-SCREENING-INVENTORY"}
# These gaps describe unsupported capabilities or missing independent cases.
# The input manifest cannot erase them by dropping an uncovered entry.
uncovered = {key: inventory["uncovered"].get(key, "Independent acceptance evidence pending")
             for key in required_open}
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
report = dict(external=inventory["external"], uncovered=uncovered,
              frontend_passed=int(frontend[0]), commit=sha, manifest_sha256=hashlib.sha256(manifest).hexdigest(),
              coverage=results, totals={key: len(rows) for key, rows in suites.items()},
              publication_single_sample_ms=benchmarks,
              **engineering_gates(uncovered, engineering_ok, publication_ok))
path = temp / "phase4-acceptance.json"
path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
sys.exit(0 if engineering_ok and publication_ok and all(r["result"] == "PASS" for r in results.values()) else 1)
