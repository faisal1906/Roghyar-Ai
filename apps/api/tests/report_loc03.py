"""Independent exact-head LOC-03 acceptance from live test identities."""
from collections import Counter
import json
import os
from pathlib import Path
import re
import subprocess
import xml.etree.ElementTree as ET

CLASS = "apps.api.tests.test_hospital_autocomplete"
CASES = {
    "validation": {f"test_loc03_validation[{case}]": "Strict bound or unsupported input rejected" for case in (
        "empty", "whitespace", "short", "length", "null", "control", "surrogate", "repeat", "combining",
        "wildcard", "limit", "boolean", "bad_city", "bad_district", "unknown_field")},
    "exact": {f"test_loc03_matching[{case}]": "Exact normalized canonical match" for case in ("name", "case")},
    "prefix_and_alias": {
        **{f"test_loc03_matching[{case}]": "Exact alias or prefix as specified" for case in ("prefix", "alias", "alias_prefix")},
        "test_loc03_multiple_aliases_same_term_and_literal_input": "Distinct hospitals, unique rows and literal SQL input",
    },
    "fuzzy_and_ranking": {
        **{f"test_loc03_matching[{case}]": "Trigram finds a reviewed spelling candidate"
           for case in ("omission", "insertion", "transpose", "punctuation")},
        "test_loc03_ranking": "Match priority, relative similarity, threshold, limit and stability",
        "test_loc03_same_name_across_cities": "Same names remain distinct with exact location context",
    },
    "unicode": {f"test_loc03_unicode[{case}]": "Original script preserved under documented normalization" for case in (
        "urdu", "pashto", "mixed", "nfc", "no_transliteration", "spaces")},
    "rls_and_verification": {
        "test_loc03_eligibility_and_withdrawal": "Verified and published only, even with hidden administrator records",
        "test_loc03_identity_change_requires_withdrawal": "Changed identity cannot retain verified status",
    },
    "performance_and_failures": {
        "test_loc03_index_plans": "Live restricted-role EXPLAIN over a synthetic name population",
        "test_loc03_runtime_and_timeout": "Restricted role and read-only query timeout recover safely",
    },
    "privacy": {"test_loc03_outage_and_closed_route": "Typed outage, no public route or sensitive query echo"},
}
PLAN = r"^LOC03 plans common=([01]),([01]),(\d+\.\d+) uncommon=([01]),([01]),(\d+\.\d+) fuzzy=([01]),([01]),(\d+\.\d+)$"


def acceptance(xml, log, prior, commit, head, pipeline):
    nodes = list(ET.fromstring(xml).iter("testcase"))
    keys = Counter((n.get("classname"), n.get("name")) for n in nodes)
    passing = {(n.get("classname"), n.get("name")) for n in nodes if not any(
        n.find(tag) is not None for tag in ("failure", "error", "skipped"))}
    summaries = re.findall(r"^=+ (\d+) passed(?:, \d+ warnings?)? in .* =+$", log, re.M)
    complete = summaries == [str(len(nodes))] and len(passing) == len(nodes)
    exact_head = bool(re.fullmatch(r"[0-9a-f]{40}", commit)) and commit == head
    plans = re.findall(PLAN, log, re.M)
    case_rows, gates = {}, {}
    for group, required in CASES.items():
        for test, expected in required.items():
            key = CLASS, test
            case_rows[test] = {"group": group, "expected": expected, "outcome":
                               "PASS" if keys[key] == 1 and key in passing else "OPEN"}
        gates[group] = "PASS" if all(case_rows[test]["outcome"] == "PASS" for test in required) else "OPEN"
    if len(plans) != 1:
        gates["performance_and_failures"] = "OPEN"
    prior_ok = set(prior) == {"loc01", "loc02"} and all(
        artifact.get("engineering") == "PASS" and artifact.get("exact_head") is True
        and artifact.get("commit") == commit for artifact in prior.values())
    ready = exact_head and pipeline == "success" and complete and prior_ok and all(
        state == "PASS" for state in gates.values())
    return {
        "commit": commit, "exact_head": exact_head, "backend_total": len(nodes),
        "engineering": "PASS" if ready else "OPEN", "gates": gates, "coverage": case_rows,
        "prior_location_acceptance": "PASS" if prior_ok else "OPEN", "regression_status": pipeline,
        "query_plans": {name: {
            "prefix_index_referenced": plans[0][i] == "1",
            "trigram_index_referenced": plans[0][i+1] == "1",
            "execution_ms": float(plans[0][i+2]),
        } for name, i in (("common", 0), ("uncommon", 3), ("fuzzy", 6))} if len(plans) == 1 else {},
        "public_discovery": "CLOSED", "verified_directory": "NOT_IMPLEMENTED",
        "production_performance": "UNVERIFIED", "native_device_qa04": "OPEN", "clinical_qa01": "OPEN",
        "real_model": "NOT_EVALUATED", "production_clinical_activation": "CLOSED",
    }


def main():
    temp = Path(os.environ["RUNNER_TEMP"])
    prior = {name: json.loads((temp / f"{name}-acceptance.json").read_text(encoding="utf-8"))
             for name in ("loc01", "loc02")}
    result = acceptance((temp / "phase3-backend.xml").read_bytes(),
                        (temp / "phase2-backend.log").read_text(encoding="utf-8"), prior,
                        subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
                        os.environ["VERIFIED_HEAD"], os.environ["REGRESSION_STATUS"])
    (temp / "loc03-acceptance.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return int(result["engineering"] != "PASS")


if __name__ == "__main__":
    raise SystemExit(main())
