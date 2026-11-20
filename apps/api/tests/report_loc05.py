"""Exact-head LOC-05 acceptance from mandatory unit and live PostgreSQL identities."""
from collections import Counter
import json
import os
from pathlib import Path
import re
import subprocess
import xml.etree.ElementTree as ET

CLASS = "apps.api.tests.test_doctor_governance"
REQUIRED = {
    "identity": tuple(f"test_loc05_credential_import_validation[{case}]" for case in (
        "missing_registration", "invalid_registration", "unknown_registry", "untrusted_source",
        "expired_evidence", "duplicate_registration")),
    "independent_review": tuple(f"test_loc05_independent_review_denials[{case}]" for case in (
        "signature", "wrong_doctor", "wrong_hospital", "wrong_revision", "scope", "self", "expired", "revoked",
        "organization", "evidence", "issuer")),
    "affiliation_and_specialty": (
        "test_loc05_identity_discovery_and_affiliation", "test_loc05_stable_names_ambiguous_taxonomy_and_proof_transplant",
        "test_loc05_hospital_revision_requires_new_affiliation", "test_loc05_scope_and_governed_expiry",
    ),
    "schedules": tuple(f"test_loc05_schedule_validation[{case}]" for case in (
        "weekday", "reversed", "overnight", "slot_size", "overlap", "timezone", "range",
        "overlapping_exceptions", "replacement_without_sessions")),
    "exceptions_and_withdrawal": (
        *[f"test_loc05_exception_behavior[{case}]" for case in (
            "leave", "holiday", "closure", "cancelled", "suspended", "replacement")],
        "test_loc05_replacement_revocation_and_stale_approval",
        "test_loc05_schedule_replacement_and_source_withdrawal_snapshot",
    ),
    "timezone_and_slots": ("test_loc05_controlled_clock_and_timezone",),
    "occupancy": (
        *[f"test_loc05_occupancy_status_and_overlap[{state}-{count}]" for state, count in (
            ("pending", 1), ("confirmed", 1), ("cancelled", 3), ("rescheduled", 3), ("completed", 3), ("no_show", 3))],
        "test_loc05_booking_snapshot_and_duplicate_active_start",
    ),
    "security_and_performance": ("test_loc05_publication_race_and_permissions", "test_loc05_live_query_plans_and_timeout",
                                 "test_loc05_private_routes_and_outage"),
}
STEPS = ("LOC05 previous revision restored", "Upgrade verified: revision 0010_doctor_governance")
PLAN_NAMES = ("detail", "specialty", "facility", "schedule", "occupancy")
PLAN = r"^LOC05 plans " + " ".join(fr"{key}=([01]),(\d+\.\d+)" for key in PLAN_NAMES) + "$"


def acceptance(xml, log, prior, commit, head, pipeline):
    nodes = list(ET.fromstring(xml).iter("testcase"))
    counts = Counter((node.get("classname"), node.get("name")) for node in nodes)
    passing = {(node.get("classname"), node.get("name")) for node in nodes if not any(
        node.find(tag) is not None for tag in ("failure", "error", "skipped"))}
    tests, gates = {}, {}
    for gate, names in REQUIRED.items():
        for name in names:
            identity = CLASS, name
            tests[name] = {"boundary": gate, "outcome": "PASS" if counts[identity] == 1 and identity in passing
                           else "OPEN"}
        gates[gate] = "PASS" if all(tests[name]["outcome"] == "PASS" for name in names) else "OPEN"
    migration_ok = all(marker in log for marker in STEPS)
    plans = re.findall(PLAN, log, re.M)
    if len(plans) != 1:
        gates["security_and_performance"] = "OPEN"
    head_ok = bool(re.fullmatch(r"[0-9a-f]{40}", commit)) and commit == head
    prior_ok = set(prior) == {"loc01", "loc02", "loc03", "loc04"} and all(
        row.get("commit") == commit and row.get("exact_head") is True and row.get("engineering") == "PASS"
        for row in prior.values())
    total = re.findall(r"^=+ (\d+) passed(?:, \d+ warnings?)? in .* =+$", log, re.M)
    complete = (head_ok and pipeline == "success" and prior_ok and migration_ok
                and total == [str(len(nodes))] and len(passing) == len(nodes)
                and all(value == "PASS" for value in gates.values()))
    return {
        "commit": commit, "exact_head": head_ok, "engineering": "PASS" if complete else "OPEN",
        "gates": gates, "coverage": tests, "backend_total": len(nodes), "migration_round_trip": migration_ok,
        "query_plans": {name: {"index_node": plans[0][i * 2] == "1", "execution_ms": float(plans[0][i * 2 + 1])}
                        for i, name in enumerate(PLAN_NAMES)} if len(plans) == 1 else {},
        "prior_location_acceptance": prior_ok, "real_credentials": "OPEN", "real_affiliations": "OPEN",
        "genuine_schedules": "OPEN", "public_doctor_discovery": "CLOSED", "real_booking": "CLOSED",
        "representative_performance": "UNVERIFIED", "native_qa04": "OPEN", "clinical_qa01": "OPEN",
        "real_model": "NOT_EVALUATED", "production_clinical_activation": "CLOSED",
    }


def main():
    temp = Path(os.environ["RUNNER_TEMP"])
    prior = {name: json.loads((temp / f"{name}-acceptance.json").read_text(encoding="utf-8"))
             for name in ("loc01", "loc02", "loc03", "loc04")}
    log = (temp / "phase2-backend.log").read_text(encoding="utf-8")
    log += "\n" + (temp / "loc05-migrations.log").read_text(encoding="utf-8")
    result = acceptance((temp / "phase3-backend.xml").read_bytes(), log, prior,
                        subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
                        os.environ["VERIFIED_HEAD"], os.environ["REGRESSION_STATUS"])
    (temp / "loc05-acceptance.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return int(result["engineering"] != "PASS")


if __name__ == "__main__":
    raise SystemExit(main())
