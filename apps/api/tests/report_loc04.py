"""Exact-head LOC-04 evidence derived from required identities and earlier gate artifacts."""
from collections import Counter
import json
import os
from pathlib import Path
import re
import subprocess
import xml.etree.ElementTree as ET

CLASS = "apps.api.tests.test_directory_governance"
REQUIRED = {
    "import": (
        *[f"test_loc04_invalid_import[{case}]" for case in (
            "empty", "oversized", "invalid_utf8", "version", "coordinates", "formula", "duplicate", "unexpected",
            "missing_source")],
        "test_loc04_import_idempotency_and_transaction",
    ),
    "independent_review": tuple(f"test_loc04_review_denials[{case}]" for case in (
        "self", "revision", "digest", "scope", "organization", "signature", "revoked", "expired",
        "unprovisioned", "field", "evidence")),
    "publication": ("test_loc04_publish_and_withdraw_search_integration",
                    "test_loc04_rejected_revision_and_unknown_emergency"),
    "freshness_and_withdrawal": (
        "test_loc04_freshness_runtime_and_legacy_flags",
        "test_loc04_revocation_immediately_removes_both_searches[key]",
        "test_loc04_revocation_immediately_removes_both_searches[source]",
        "test_loc04_source_withdrawal_blocks_later_import",
    ),
    "aliases": ("test_loc04_individual_alias_withdrawal",
                "test_loc04_replacement_removes_old_alias_and_rejects_old_approval"),
    "concurrency": ("test_loc04_publication_race_one_winner", "test_loc04_late_old_review_cannot_publish",
                    *[f"test_loc04_invalidation_race[{case}]" for case in ("source", "key", "withdrawal")]),
    "security_and_search": ("test_loc04_database_roles_and_immutable_history",
                            "test_loc04_legacy_verified_row_has_no_search_eligibility"),
}
STEPS = ("LOC04 previous revision restored", "Upgrade verified: revision 0009_directory_governance")


def acceptance(xml, log, prior, commit, head, pipeline):
    nodes = list(ET.fromstring(xml).iter("testcase"))
    counts = Counter((n.get("classname"), n.get("name")) for n in nodes)
    good = {(n.get("classname"), n.get("name")) for n in nodes if not any(
        n.find(tag) is not None for tag in ("failure", "error", "skipped"))}
    total = re.findall(r"^=+ (\d+) passed(?:, \d+ warnings?)? in .* =+$", log, flags=re.M)
    verified_head = bool(re.fullmatch(r"[0-9a-f]{40}", commit)) and commit == head
    prior_ok = set(prior) == {"loc01", "loc02", "loc03"} and all(
        row.get("commit") == commit and row.get("exact_head") is True and row.get("engineering") == "PASS"
        for row in prior.values())
    # Workflow's downgrade and upgrade are hard prerequisites outside the final pytest stream.
    migrations = all(step in log for step in STEPS)
    gates = {}
    cases = {}
    for group, names in REQUIRED.items():
        for name in names:
            key = CLASS, name
            cases[name] = {"boundary": group, "outcome": "PASS" if counts[key] == 1 and key in good else "OPEN"}
        gates[group] = "PASS" if all(cases[name]["outcome"] == "PASS" for name in names) else "OPEN"
    complete = (verified_head and pipeline == "success" and prior_ok and migrations
                and total == [str(len(nodes))] and len(good) == len(nodes)
                and all(value == "PASS" for value in gates.values()))
    return {
        "commit": commit, "exact_head": verified_head, "engineering": "PASS" if complete else "OPEN",
        "gates": gates, "coverage": cases, "backend_total": len(nodes), "migration_round_trip": migrations,
        "prior_location_acceptance": prior_ok, "real_source_verification": "OPEN", "real_reviewers": "OPEN",
        "real_directory_availability": "OPEN", "public_discovery": "CLOSED",
        "native_device_qa04": "OPEN", "clinical_qa01": "OPEN", "production_clinical_activation": "CLOSED",
    }


def main():
    temp = Path(os.environ["RUNNER_TEMP"])
    prior = {name: json.loads((temp / f"{name}-acceptance.json").read_text(encoding="utf-8"))
             for name in ("loc01", "loc02", "loc03")}
    log = (temp / "phase2-backend.log").read_text(encoding="utf-8")
    log += "\n" + (temp / "loc04-migrations.log").read_text(encoding="utf-8")
    result = acceptance((temp / "phase3-backend.xml").read_bytes(), log, prior,
                        subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
                        os.environ["VERIFIED_HEAD"], os.environ["REGRESSION_STATUS"])
    (temp / "loc04-acceptance.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return int(result["engineering"] != "PASS")


if __name__ == "__main__":
    raise SystemExit(main())
