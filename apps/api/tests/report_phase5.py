"""Independent Phase 5 rollup. Exact catalogs stay owned by the ticket reporters."""
import json
import os
from pathlib import Path
import re
import subprocess

from report_loc01 import REQUIRED as LOC01
from report_loc02 import CASES as LOC02
from report_loc03 import CASES as LOC03
from report_loc04 import REQUIRED as LOC04
from report_loc05 import REQUIRED as LOC05
from report_loc06 import BROWSER, REQUIRED as LOC06


def inventory(groups):
    return {name for group in groups.values() for name in group}


CATALOG = {
    "loc01": set(LOC01), "loc02": inventory(LOC02), "loc03": inventory(LOC03),
    "loc04": inventory(LOC04), "loc05": inventory(LOC05), "loc06": inventory(LOC06) | set(BROWSER),
}
RELEASE = {
    "loc01": {"production_clinical_activation": "CLOSED", "provider_directory": "NOT_IMPLEMENTED"},
    "loc02": {"public_discovery": "CLOSED", "verified_directory": "NOT_IMPLEMENTED"},
    "loc03": {"public_discovery": "CLOSED", "verified_directory": "NOT_IMPLEMENTED",
              "production_performance": "UNVERIFIED"},
    "loc04": {"public_discovery": "CLOSED", "real_source_verification": "OPEN",
              "real_reviewers": "OPEN", "real_directory_availability": "OPEN"},
    "loc05": {"public_doctor_discovery": "CLOSED", "real_booking": "CLOSED", "real_credentials": "OPEN",
              "real_affiliations": "OPEN", "genuine_schedules": "OPEN", "real_model": "NOT_EVALUATED"},
    "loc06": {"public_directions": "CLOSED", "provider_onboarding": "OPEN", "real_eta_accuracy": "UNVERIFIED",
              "production_clinical_activation": "CLOSED"},
}


def acceptance(artifacts, phase4, sha, head, pipeline):
    exact = bool(re.fullmatch(r"[0-9a-f]{40}", sha)) and sha == head
    decisions = {}
    for ticket, keys in CATALOG.items():
        artifact = artifacts.get(ticket, {})
        coverage = artifact.get("coverage", {})
        closed = all(artifact.get(gate) == expected for gate, expected in RELEASE[ticket].items())
        valid = (artifact.get("commit") == sha and artifact.get("exact_head") is True
                 and artifact.get("engineering") == "PASS" and set(coverage) == keys
                 and all(row.get("outcome") == "PASS" for row in coverage.values()) and closed)
        decisions[ticket] = "PASS" if valid else "OPEN"
    prior = phase4.get("commit") == sha and phase4.get("phase4") == "PASS" and phase4.get("production") == "CLOSED"
    passed = exact and prior and pipeline == "success" and all(status == "PASS" for status in decisions.values())
    return {
        "commit": sha, "exact_head": exact, "regression_status": pipeline, "tickets": decisions,
        "phase5_synthetic_engineering": "PASS" if passed else "OPEN",
        "phase5_real_world_readiness": "OPEN", "phase4_preserved": prior,
        "public_discovery": "CLOSED", "public_directions": "CLOSED", "booking": "CLOSED",
        "native_qa04": "OPEN", "clinical_qa01": "OPEN", "production_clinical_activation": "CLOSED",
        "backend_total": artifacts.get("loc06", {}).get("backend_total"),
        "chromium_total": artifacts.get("loc01", {}).get("chromium_total"),
        "frontend_unit_total": artifacts.get("loc01", {}).get("unit_total"),
    }


def main():
    temp = Path(os.environ["RUNNER_TEMP"])
    def read(name):
        path = temp / f"{name}-acceptance.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    report = acceptance({name: read(name) for name in CATALOG}, read("phase4"),
                        subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
                        os.environ["VERIFIED_HEAD"], os.environ["REGRESSION_STATUS"])
    (temp / "phase5-acceptance.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return int(report["phase5_synthetic_engineering"] != "PASS")


if __name__ == "__main__":
    raise SystemExit(main())
