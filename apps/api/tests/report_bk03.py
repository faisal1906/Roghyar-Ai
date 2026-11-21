"""Exact-head BK-03 acceptance for the private synthetic reservation contract."""
from collections import Counter
import json
import os
from pathlib import Path
import re
import subprocess
import xml.etree.ElementTree as ET

CLASS = "apps.api.tests.test_booking_reservations"
MIGRATIONS = ("BK03 previous revision restored", "Upgrade verified: revision 0013_atomic_reservations")
SERIES = ("commit", "replay", "conflict", "overlap", "parallel")


def cases(name, labels):
    return tuple(f"test_bk03_{name}[{value}]" for value in labels.split())


REQUIRED = {
    "strict_consent_and_input": cases("strict_validation",
        "uuid naive precision name_empty name_long control boolean_age age_range minor_guardian phone "
        "consent_false consent_integer reason_long unicode assessment_uuid hospital_injection slot_end_injection "
        "verification_injection"),
    "identity_and_eligibility": cases("booking_denials",
        "wrong_purpose-unauthenticated expired-unauthenticated revoked-unauthenticated forged-unauthenticated "
        "mismatch-phone_mismatch assessment-assessment_link_unavailable minor-minor_policy_unavailable "
        "outside-slot_unavailable unknown_doctor-slot_unavailable offset-slot_unavailable "
        "missing_confirmation-schedule_authority_unavailable"),
    "authority_races": cases("authority_wins_before_reservation",
        "otp_revoked doctor_withdrawn hospital_withdrawn closure reviewer_revoked schedule_replaced")
        + ("test_bk03_revocation_waits_for_commit",),
    "overlap_and_concurrency": cases("concurrent_booking", "same_slot same_key adjacent different_subject_same_key")
        + ("test_bk03_overlap_constraint_and_status_reuse", "test_bk03_different_doctors_confirm_at_same_time"),
    "migration_legacy_safety": ("test_bk03_migration_blocks_legacy_overlap_and_nonempty_history",),
    "durable_replay_and_atomic_commit": cases("unknown_commit_recovery", "True False")
        + ("test_bk03_restart_replays_durable_encrypted_commit", "test_bk03_booking_code_collision_retries"),
    "rls_and_audit": ("test_bk03_restricted_sql_and_tampered_result",),
    "closed_http_and_privacy": ("test_bk03_private_http_and_closed_release",),
    "dependency_failure": ("test_bk03_database_dependency_failure",),
    "performance": ("test_bk03_live_measurements",),
}


def acceptance(xml, log, migration, phase5, bk01, bk02, sha, expected, status):
    nodes = list(ET.fromstring(xml).iter("testcase"))
    identities = [(node.get("classname"), node.get("name")) for node in nodes]
    counts = Counter(identities)
    passing = {identity for node, identity in zip(nodes, identities, strict=True)
               if all(node.find(tag) is None for tag in ("skipped", "failure", "error"))}
    coverage = {name: "PASS" if counts[(CLASS, name)] == 1 and (CLASS, name) in passing else "OPEN"
                for names in REQUIRED.values() for name in names}
    gates = {gate: "PASS" if all(coverage[name] == "PASS" for name in names) else "OPEN"
             for gate, names in REQUIRED.items()}
    exact = bool(re.fullmatch(r"[0-9a-f]{40}", sha)) and sha == expected
    migrated = all(marker in migration for marker in MIGRATIONS)
    totals = re.findall(r"^=+ (\d+) passed(?:, \d+ warnings?)? in .* =+$", log, re.M)
    marker = re.findall(r"^BK03 measurements (.+)$", log, re.M)
    measurements = {}
    if len(marker) == 1:
        match = re.fullmatch(" ".join(fr"{name}=(\d+),(\d+\.\d+),(\d+\.\d+)" for name in SERIES), marker[0])
        if match:
            for i, name in enumerate(SERIES):
                n, p50, p95 = match.groups()[i*3:i*3+3]
                if int(n) > 0 and 0 < float(p50) <= float(p95):
                    measurements[name] = {"samples": int(n), "p50_ms": float(p50), "p95_ms": float(p95)}
    prior = (all(result.get("commit") == sha and result.get("exact_head") is True
                 and result.get("backend_total") == len(nodes) for result in (phase5, bk01, bk02))
             and phase5.get("phase4_preserved") is True and phase5.get("phase5_synthetic_engineering") == "PASS"
             and all(result.get("engineering") == "PASS" and result.get("public_booking") == "CLOSED"
                     for result in (bk01, bk02))
             and bk01.get("real_sms") == "OPEN" and bk02.get("real_provider_schedules") == "OPEN"
             and bk02.get("public_slots") == "CLOSED"
             and all(type(phase5.get(k)) is int and phase5[k] > 0 for k in ("frontend_unit_total","chromium_total")))
    complete = (exact and migrated and prior and status == "success" and len(passing) == len(nodes)
                and totals == [str(len(nodes))] and set(measurements) == set(SERIES)
                and all(outcome == "PASS" for outcome in gates.values()))
    return {"commit": sha, "exact_head": exact, "engineering": "PASS" if complete else "OPEN",
            "coverage": coverage, "gates": gates, "measurements": measurements, "migration_round_trip": migrated,
            "previous_engineering": prior, "backend_total": len(nodes),
            "frontend_unit_total": phase5.get("frontend_unit_total"), "chromium_total": phase5.get("chromium_total"),
            "real_provider_schedules": "OPEN", "real_sms": "OPEN", "real_booking": "CLOSED",
            "public_otp": "CLOSED", "public_slots": "CLOSED", "public_booking": "CLOSED",
            "privacy_retention": "OPEN", "minor_authorization": "OPEN", "assessment_linking": "CLOSED",
            "real_model": "NOT_EVALUATED", "clinical_qa01": "OPEN", "native_qa04": "OPEN",
            "production_clinical_activation": "CLOSED"}


def main():
    temp = Path(os.environ["RUNNER_TEMP"])
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()

    def read(path):
        return (temp / path).read_text(encoding="utf-8")

    try:
        phase5, bk01, bk02 = (json.loads(read(f"{name}-acceptance.json")) for name in ("phase5", "bk01", "bk02"))
        result = acceptance((temp / "phase3-backend.xml").read_bytes(), read("phase2-backend.log"),
                            read("bk03-migrations.log"), phase5,bk01,bk02,sha,os.environ["VERIFIED_HEAD"],
                            os.environ["REGRESSION_STATUS"])
    except (OSError, ValueError, KeyError, TypeError, AttributeError, ET.ParseError):
        result = {"commit": sha, "engineering": "OPEN", "reason": "invalid_or_missing_evidence"}
    (temp / "bk03-acceptance.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return int(result["engineering"] != "PASS")


if __name__ == "__main__":
    raise SystemExit(main())
