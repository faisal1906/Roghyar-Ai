"""Exact-head BK-02 acceptance based on live and unit JUnit test identities."""
from collections import Counter
import json
import os
from pathlib import Path
import re
import subprocess
import xml.etree.ElementTree as ET

CLASS = "apps.api.tests.test_booking_slots"
MIGRATIONS = ("BK02 previous revision restored", "Upgrade verified: revision 0012_booking_slot_governance")
SERIES = ("one", "seven", "full", "exceptions", "occupied", "parallel")


def cases(name, labels):
    return tuple(f"test_bk02_{name}[{label}]" for label in labels.split())


REQUIRED = {
    "calendar": ("test_bk02_window_and_confirmation",) + cases("whole_intervals_and_rolling_window", "15-4 20-3 25-2"),
    "signature_and_scope": cases("untrusted_submissions", "signature evidence reviewer organization scope malformed"),
    "exceptions": cases("newer_states_override_working",
                        "closure-closed_by_exception leave-closed_by_exception holiday-closed_by_exception "
                        "withdrawn-schedule_withdrawn unavailable-provider_unavailable")
                  + ("test_bk02_replacement_exact_binding",),
    "revision_and_revocation": cases("mismatched_or_revoked_proof",
                                     "doctor_revision hospital_revision schedule_digest sessions revoked"),
    "provider_and_freshness": cases("gateway_contract", "accepted redirect wrong_type oversize timeout")
                              + ("test_bk02_snapshot_expiry_and_provider_failure",),
    "database_snapshot": cases("atomic_read_snapshot",
                                "closure appointment cancel doctor_withdrawal revision source_revocation hospital_withdrawal")
                         + ("test_bk02_source_sync_races_with_schedule_correction",),
    "occupancy_and_privacy": cases("interval_occupancy_and_privacy",
                                   "pending-True confirmed-True cancelled-False completed-False rescheduled-False no_show-False"),
    "publication_and_role_security": ("test_bk02_concurrent_append_and_restricted_roles",
                                      "test_bk02_append_rollback_and_history",
                                      "test_bk02_restricted_cross_facility_insert"),
    "bounded_live_performance": ("test_bk02_live_calendar_measurements",),
    "release_boundary": ("test_bk02_public_routes_closed",),
}


def acceptance(xml, log, migration, bk01, phase5, sha, head, pipeline):
    nodes = list(ET.fromstring(xml).iter("testcase"))
    identities = [(node.get("classname"), node.get("name")) for node in nodes]
    counts = Counter(identities)
    passed = {identity for node, identity in zip(nodes, identities, strict=True)
              if all(node.find(tag) is None for tag in ("skipped", "failure", "error"))}
    coverage = {name: "PASS" if counts[(CLASS, name)] == 1 and (CLASS, name) in passed else "OPEN"
                for cases in REQUIRED.values() for name in cases}
    sections = {group: "PASS" if all(coverage[case] == "PASS" for case in cases) else "OPEN"
                for group, cases in REQUIRED.items()}
    evidence = re.findall(r"^BK02 measurements (.+)$", log, re.M)
    measurements = {}
    if len(evidence) == 1:
        pieces = re.fullmatch(" ".join(fr"{name}=(\d+),(\d+\.\d+),(\d+\.\d+)" for name in SERIES), evidence[0])
        if pieces:
            for i, name in enumerate(SERIES):
                samples, p50, p95 = pieces.groups()[3*i:3*i+3]
                if int(samples) >= 10 and 0 < float(p50) <= float(p95):
                    measurements[name] = {"samples": int(samples), "p50_ms": float(p50), "p95_ms": float(p95)}
    exact = bool(re.fullmatch(r"[a-f0-9]{40}", sha)) and sha == head
    migrated = all(marker in migration for marker in MIGRATIONS)
    previous = (bk01.get("exact_head") is True and bk01.get("commit") == sha
                and bk01.get("engineering") == "PASS" and bk01.get("public_booking") == "CLOSED"
                and bk01.get("public_otp") == "CLOSED" and bk01.get("real_sms") == "OPEN"
                and phase5.get("exact_head") is True and phase5.get("commit") == sha
                and phase5.get("phase5_synthetic_engineering") == "PASS"
                and phase5.get("phase5_real_world_readiness") == "OPEN"
                and phase5.get("phase4_preserved") is True and phase5.get("tickets", {}).get("loc05") == "PASS"
                and phase5.get("backend_total") == bk01.get("backend_total") == len(nodes)
                and all(type(phase5.get(k)) is int and phase5[k] > 0 for k in ("frontend_unit_total", "chromium_total")))
    totals = re.findall(r"^=+ (\d+) passed(?:, \d+ warnings?)? in .* =+$", log, re.M)
    complete = (exact and migrated and previous and pipeline == "success"
                and len(passed) == len(nodes) and totals == [str(len(nodes))]
                and set(measurements) == set(SERIES) and all(state == "PASS" for state in sections.values()))
    return {"commit": sha, "exact_head": exact, "engineering": "PASS" if complete else "OPEN",
            "coverage": coverage, "gates": sections, "measurements": measurements, "migration": migrated,
            "previous_engineering": previous, "backend_total": len(nodes), "frontend_unit_total": phase5.get("frontend_unit_total"),
            "chromium_total": phase5.get("chromium_total"), "real_provider_schedules": "OPEN", "real_sms": "OPEN",
            "real_doctor_credentials": "OPEN", "real_hospital_affiliations": "OPEN",
            "provider_sync_operations": "OPEN", "representative_performance": "UNVERIFIED", "real_bookability": "CLOSED",
            "production_key_operations": "OPEN", "privacy": "OPEN", "public_otp": "CLOSED",
            "public_slots": "CLOSED", "public_booking": "CLOSED", "real_model": "NOT_EVALUATED",
            "clinical_qa01": "OPEN", "native_qa04": "OPEN", "production_clinical_activation": "CLOSED"}


def main():
    directory = Path(os.environ["RUNNER_TEMP"])
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()

    def read(name):
        return (directory / name).read_text(encoding="utf-8")

    try:
        result = acceptance((directory / "phase3-backend.xml").read_bytes(), read("phase2-backend.log"),
                            read("bk02-migrations.log"), json.loads(read("bk01-acceptance.json")),
                            json.loads(read("phase5-acceptance.json")), sha, os.environ["VERIFIED_HEAD"],
                            os.environ["REGRESSION_STATUS"])
    except (OSError, ValueError, KeyError, TypeError, AttributeError, ET.ParseError):
        result = {"commit": sha, "engineering": "OPEN", "reason": "invalid_or_missing_evidence"}
    (directory / "bk02-acceptance.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return int(result["engineering"] != "PASS")


if __name__ == "__main__":
    raise SystemExit(main())
