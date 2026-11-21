"""Exact-head BK-04 gate based on mandatory live and unit JUnit identities."""
from collections import Counter
import json
import os
from pathlib import Path
import re
import subprocess
import xml.etree.ElementTree as ET

CLASS = "apps.api.tests.test_appointment_lifecycle"
MIGRATIONS = ("BK04 previous revision restored", "Upgrade verified: revision 0014_appointment_lifecycle")
SERIES = ("history", "page", "cancel", "cancel_replay", "reschedule", "conflict", "parallel", "reschedule_replay")


def cases(name, labels):
    return tuple(f"test_bk04_{name}[{case}]" for case in labels.split())


REQUIRED = {
    "inputs": cases("mutation_validation", "cancel_slot missing_target action identity naive precision"),
    "ownership_and_history": ("test_bk04_history_ownership_cursor_and_withdrawal",
                              "test_bk04_large_history_and_live_performance"),
    "transitions_and_release": cases("state_machine", "pending-True confirmed-True completed-False no_show-False "
                                          "cancelled-False rescheduled-False"),
    "lineage_and_replay": cases("original_creation_replay_and_successor", "cancel reschedule"),
    "atomicity_and_recovery": cases("uncertain_commit_recovery", "True False")
                              + ("test_bk04_rollback_after_target_insert",),
    "contention": cases("concurrent_lifecycle", "same_cancel same_reschedule two_patients cancel_vs_reschedule"),
    "purpose_deadline_and_schedule": cases("boundary_denials", "wrong_purpose revoked expired cutoff closure target_busy"),
    "authority_races": cases("authority_races", "revocation withdrawal replacement")
                      + ("test_bk04_revocation_waits_for_locked_authority",),
    "rls_and_history_custody": ("test_bk04_session_capability_and_commit_guard", "test_bk04_nonempty_downgrade_guard"),
    "private_http_and_privacy": ("test_bk04_private_http",),
}


def acceptance(xml, log, migration, prior, sha, expected, status):
    nodes = list(ET.fromstring(xml).iter("testcase"))
    identities = [(node.get("classname"), node.get("name")) for node in nodes]
    counts = Counter(identities)
    passing = {identity for node, identity in zip(nodes, identities, strict=True)
               if all(node.find(tag) is None for tag in ("skipped", "failure", "error"))}
    coverage = {name: "PASS" if counts[(CLASS, name)] == 1 and (CLASS, name) in passing else "OPEN"
                for names in REQUIRED.values() for name in names}
    gates = {group: "PASS" if all(coverage[name] == "PASS" for name in names) else "OPEN"
             for group, names in REQUIRED.items()}
    exact = bool(re.fullmatch(r"[0-9a-f]{40}", sha)) and sha == expected
    migrated = all(marker in migration for marker in MIGRATIONS)
    totals = re.findall(r"^=+ (\d+) passed(?:, \d+ warnings?)? in .* =+$", log, re.M)
    marker = re.findall(r"^BK04 measurements (.+)$", log, re.M)
    measured = {}
    if len(marker) == 1:
        match = re.fullmatch(" ".join(fr"{name}=(\d+),(\d+\.\d+),(\d+\.\d+)" for name in SERIES), marker[0])
        if match:
            for i, name in enumerate(SERIES):
                n, low, high = match.groups()[i*3:i*3+3]
                if int(n) > 0 and 0 < float(low) <= float(high):
                    measured[name] = {"samples": int(n), "p50_ms": float(low), "p95_ms": float(high)}
    phase5 = prior.get("phase5", {})
    previous = (set(prior) == {"phase5", "bk01", "bk02", "bk03"}
                and all(item.get("commit") == sha and item.get("exact_head") is True
                        and item.get("backend_total") == len(nodes) for item in prior.values())
                and phase5.get("phase4_preserved") is True and phase5.get("phase5_synthetic_engineering") == "PASS"
                and all(prior[ticket].get("engineering") == "PASS" and prior[ticket].get("public_booking") == "CLOSED"
                        for ticket in ("bk01", "bk02", "bk03"))
                and prior["bk01"].get("real_sms") == "OPEN" and prior["bk03"].get("real_booking") == "CLOSED"
                and all(type(phase5.get(k)) is int and phase5[k] > 0 for k in ("frontend_unit_total", "chromium_total")))
    complete = (exact and migrated and previous and status == "success" and len(passing) == len(nodes)
                and totals == [str(len(nodes))] and set(measured) == set(SERIES)
                and all(outcome == "PASS" for outcome in gates.values()))
    return {"commit": sha, "exact_head": exact, "engineering": "PASS" if complete else "OPEN",
            "coverage": coverage, "gates": gates, "measurements": measured, "migration_round_trip": migrated,
            "previous_engineering": previous, "backend_total": len(nodes),
            "frontend_unit_total": phase5.get("frontend_unit_total"), "chromium_total": phase5.get("chromium_total"),
            "public_otp": "CLOSED", "public_slots": "CLOSED", "public_booking": "CLOSED", "public_lifecycle": "CLOSED",
            "real_provider_schedules": "OPEN", "real_sms": "OPEN", "operational_cutoffs": "OPEN",
            "privacy_retention": "OPEN", "representative_performance": "UNVERIFIED", "native_qa04": "OPEN",
            "clinical_qa01": "OPEN", "real_model": "NOT_EVALUATED", "production_clinical_activation": "CLOSED"}


def main():
    directory = Path(os.environ["RUNNER_TEMP"])
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()

    def read(name):
        return (directory / name).read_text(encoding="utf-8")

    try:
        prior = {ticket: json.loads(read(f"{ticket}-acceptance.json")) for ticket in ("phase5", "bk01", "bk02", "bk03")}
        result = acceptance((directory / "phase3-backend.xml").read_bytes(), read("phase2-backend.log"),
                            read("bk04-migrations.log"), prior, sha, os.environ["VERIFIED_HEAD"],
                            os.environ["REGRESSION_STATUS"])
    except (OSError, ValueError, KeyError, TypeError, AttributeError, ET.ParseError):
        result = {"commit": sha, "engineering": "OPEN", "reason": "invalid_or_missing_evidence"}
    (directory / "bk04-acceptance.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return int(result["engineering"] != "PASS")


if __name__ == "__main__":
    raise SystemExit(main())
