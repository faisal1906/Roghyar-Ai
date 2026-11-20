"""Independent LOC-02 acceptance catalog for the exact hosted commit."""
from collections import Counter
import json
import os
from pathlib import Path
import re
import subprocess
import xml.etree.ElementTree as ET

CASES = {
    "validation": {f"test_loc02_invalid_query[{case}]": "Invalid input is rejected before a DB read" for case in (
        "latitude", "longitude", "nan", "infinity", "boolean", "numeric_string", "missing",
        "radius", "radius_float", "sort", "injection", "limit", "unknown_field")},
    "filters": {f"test_loc02_unsupported_filters[{case}]": "Unsupported filters are denied explicitly" for case in (
        "type", "open_now", "specialty", "specialty_sort", "combined")},
    "radii": {
        **{f"test_loc02_radius_boundary[{radius}]": "PostGIS includes inside and excludes outside the radius"
           for radius in (5, 15, 30, 60)},
        **{f"test_loc02_expansion[{step}]": "First qualifying window preserves filters"
           for step in ("initial", "once", "twice", "to_sixty", "empty")},
        "test_loc02_explicit_radius_and_empty_directory": "Explicit radius stays fixed and directory absence is distinct",
    },
    "geography": {
        "test_loc02_geographic_edges[antimeridian]": "WGS84 distance wraps longitude",
        "test_loc02_geographic_edges[near_pole]": "WGS84 distance reflects polar curvature",
        "test_loc02_index_plan": "Live restricted-role EXPLAIN and existing GiST index",
    },
    "role_and_eligibility": {
        "test_loc02_runtime_rls_eligibility_and_revocation": "Runtime scope excludes unpublished and unverified records",
        "test_loc02_runtime_cannot_publish": "Writer and privileged login boundaries remain closed",
    },
    "failures": {
        "test_loc02_query_timeout": "Real blocked query times out and then recovers",
        "test_loc02_database_outage": "Database outage is distinct from an empty result",
    },
    "privacy": {
        "test_loc02_private_route_and_no_coordinate_output": "No public coordinate intake or nearby API",
    },
}
CASES["filters"]["test_loc02_sort_limit_count_zero_distance_and_null_filters"] = (
    "Stable nearest and emergency ordering, exact counts and bounded output")
CLASS = "test_hospital_search"


def acceptance(xml, log, loc01, commit, head, pipeline):
    tests = list(ET.fromstring(xml).iter("testcase"))
    identity_counts = Counter((node.get("classname"), node.get("name")) for node in tests)
    actual = {(node.get("classname"), node.get("name")): not any(
        node.find(tag) is not None for tag in ("failed", "failure", "error", "skipped")) for node in tests}
    loc01_ok = (loc01.get("commit") == commit and loc01.get("exact_head") is True
                and loc01.get("engineering") == "PASS")
    exact_head = bool(re.fullmatch(r"[0-9a-f]{40}", commit)) and commit == head
    plans = re.findall(r"^LOC02 plan natural index=(true|false) diagnostic index=(true|false)$", log, re.M)
    summaries = re.findall(r"^=+ (\d+) passed(?:, \d+ warnings?)? in .* =+$", log, re.M)
    totals_ok = summaries == [str(len(tests))] and all(actual.values())
    cases, gates = {}, {}
    for group, required in CASES.items():
        for name, boundary in required.items():
            identity = CLASS, name
            passed = identity_counts[identity] == 1 and actual.get(identity, False)
            cases[name] = {"group": group, "expected": boundary, "outcome": "PASS" if passed else "OPEN"}
        gates[group] = "PASS" if all(cases[name]["outcome"] == "PASS" for name in required) else "OPEN"
    if len(plans) != 1:
        gates["geography"] = "OPEN"
    if not loc01_ok:
        gates["privacy"] = "OPEN"
    passed = exact_head and pipeline == "success" and totals_ok and all(value == "PASS" for value in gates.values())
    return {
        "commit": commit, "exact_head": exact_head, "regression_status": pipeline, "backend_total": len(tests),
        "summary_verified": totals_ok, "loc01": "PASS" if loc01_ok else "OPEN",
        "query_plans": dict(zip(("natural_index", "diagnostic_index"), (v == "true" for v in plans[0]))) if len(plans) == 1 else {},
        "engineering": "PASS" if passed else "OPEN", "gates": gates, "coverage": cases,
        "verified_directory": "NOT_IMPLEMENTED", "public_discovery": "CLOSED",
        "native_device_qa04": "OPEN", "clinical_qa01": "OPEN", "real_model": "NOT_EVALUATED",
        "production_clinical_activation": "CLOSED",
    }


def main():
    temp = Path(os.environ["RUNNER_TEMP"])
    result = acceptance(
        (temp / "phase3-backend.xml").read_bytes(), (temp / "phase2-backend.log").read_text(encoding="utf-8"),
        json.loads((temp / "loc01-acceptance.json").read_text(encoding="utf-8")),
        subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        os.environ["VERIFIED_HEAD"], os.environ["REGRESSION_STATUS"],
    )
    (temp / "loc02-acceptance.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return int(result["engineering"] != "PASS")


if __name__ == "__main__":
    raise SystemExit(main())
