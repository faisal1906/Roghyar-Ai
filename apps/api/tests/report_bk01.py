"""BK-01 evidence assembled from exact mandatory JUnit identities."""
from collections import Counter
import json
import os
from pathlib import Path
import re
import subprocess
import xml.etree.ElementTree as ET

CLASS = "apps.api.tests.test_otp_service"


def cases(name, values):
    return tuple(f"{name}[{value}]" for value in values.split())


IDENTITY = ("test_bk01_identity_no_patient_creation_and_token_revocation",)
HTTP = ("test_bk01_private_http_and_rls",)
REQUIRED = {
    "phone_normalization": cases("test_bk01_invalid_phone",
                                 "empty foreign landline short long separator unicode injection int null"),
    "challenge_lifecycle": cases("test_bk01_invalid_verifications_are_indistinguishable",
                                "expired attempt_cap wrong_purpose wrong_phone superseded")
                           + ("test_bk01_late_ack_cannot_revive_superseded_challenge",),
    "provider": cases("test_bk01_gateway_contract", "accepted reject redirect wrong_id duplicate oversize timeout")
                + cases("test_bk01_provider_failures_never_activate", "rejected timeout unconfigured"),
    "shared_redis_limits": ("test_bk01_parallel_resends_use_shared_redis",
                            "test_bk01_redis_disconnected_and_global_budget"),
    "atomic_verification": cases("test_bk01_concurrent_consumption_is_atomic",
                                 "two_correct final_attempt_race resend_race"),
    "verified_identity_and_revocation": IDENTITY,
    "purpose_and_token_security": cases("test_bk01_token_denials_require_persisted_session",
                                        "issuer audience key type future expired unknown_subject admin signature "
                                        "algorithm revoked_identity"),
    "http_rls_and_release": HTTP,
}
MIGRATIONS = ("BK01 previous revision restored", "Upgrade verified: revision 0011_otp_sessions")


def acceptance(xml, backend_log, migration_log, phase5, sha, expected, status):
    nodes = list(ET.fromstring(xml).iter("testcase"))
    identities = [(node.get("classname"), node.get("name")) for node in nodes]
    counts = Counter(identities)
    passing = {identity for node, identity in zip(nodes, identities, strict=True)
               if all(node.find(tag) is None for tag in ("failure", "error", "skipped"))}
    coverage = {name: "PASS" if counts[(CLASS, name)] == 1 and (CLASS, name) in passing else "OPEN"
                for names in REQUIRED.values() for name in names}
    gates = {gate: "PASS" if all(coverage[name] == "PASS" for name in names) else "OPEN"
             for gate, names in REQUIRED.items()}
    totals = re.findall(r"^=+ (\d+) passed(?:, \d+ warnings?)? in .* =+$", backend_log, re.M)
    exact = bool(re.fullmatch(r"[0-9a-f]{40}", sha)) and sha == expected
    prior = (phase5.get("commit") == sha and phase5.get("exact_head") is True
             and phase5.get("phase5_synthetic_engineering") == "PASS"
             and phase5.get("phase5_real_world_readiness") == "OPEN"
             and phase5.get("phase4_preserved") is True and phase5.get("booking") == "CLOSED"
             and phase5.get("backend_total") == len(nodes)
             and all(type(phase5.get(key)) is int and phase5[key] > 0
                     for key in ("frontend_unit_total", "chromium_total")))
    migration = all(marker in migration_log for marker in MIGRATIONS)
    complete = (exact and prior and migration and status == "success"
                and totals == [str(len(nodes))] and len(passing) == len(nodes)
                and all(value == "PASS" for value in coverage.values()))
    return {"commit": sha, "exact_head": exact, "engineering": "PASS" if complete else "OPEN",
            "coverage": coverage, "gates": gates, "backend_total": len(nodes),
            "frontend_unit_total": phase5.get("frontend_unit_total"), "chromium_total": phase5.get("chromium_total"),
            "previous_phase5": prior, "migration_round_trip": migration, "real_sms": "OPEN",
            "production_keys": "OPEN", "privacy_retention": "OPEN", "public_otp": "CLOSED",
            "public_booking": "CLOSED", "native_qa04": "OPEN", "clinical_qa01": "OPEN",
            "real_model": "NOT_EVALUATED", "production_clinical_activation": "CLOSED"}


def main():
    temp = Path(os.environ["RUNNER_TEMP"])
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    path = temp / "bk01-acceptance.json"
    try:
        result = acceptance((temp / "phase3-backend.xml").read_bytes(),
                            (temp / "phase2-backend.log").read_text(encoding="utf-8"),
                            (temp / "bk01-migrations.log").read_text(encoding="utf-8"),
                            json.loads((temp / "phase5-acceptance.json").read_text(encoding="utf-8")),
                            sha, os.environ["VERIFIED_HEAD"], os.environ["REGRESSION_STATUS"])
    except (OSError, ValueError, TypeError, AttributeError, ET.ParseError):
        result = {"commit": sha, "engineering": "OPEN", "reason": "invalid_or_missing_evidence"}
    path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return int(result["engineering"] != "PASS")


if __name__ == "__main__":
    raise SystemExit(main())
