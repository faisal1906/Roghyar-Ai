"""LOC-06 exact-head contract, live PostgreSQL and browser evidence."""
from collections import Counter
import json
import os
from pathlib import Path
import re
import subprocess
import xml.etree.ElementTree as ET

CLASS = "apps.api.tests.test_route_directions"
BROWSER = (
    "LOC06 browser keeps directions unavailable and emergency accessible",
    "LOC06 browser keeps offline emergency independent of routing",
)
ENDPOINTS = ("http://93.184.215.14/v1/route", "https://169.254.169.254/v1/route",
             "https://127.0.0.1/v1/route", "https://[::1]/v1/route", "https://unapproved.example/v1/route",
             "https://93.184.215.14:444/v1/route", "https://token@93.184.215.14/v1/route",
             "https://93.184.215.14/v1/route?token=synthetic", "https://93.184.215.14/v1/other")
REQUIRED = {
    "validation": tuple(f"test_loc06_request_validation[{case}-{status}]" for case, status in (
        ("lat", "invalid_origin"), ("lng", "invalid_origin"), ("bool", "invalid_origin"),
        ("nan", "invalid_origin"), ("accuracy", "invalid_origin"), ("stale", "stale_origin"),
        ("destination", "invalid_destination"), ("revision", "invalid_destination"),
        ("mode", "unsupported_mode"), ("options", "invalid_origin"))),
    "provider_geometry_and_eta": (
        *[f"test_loc06_provider_contract[{case}-{status}]" for case, status in (
            ("valid", "route_available"), ("no_eta", "route_available"), ("no_geometry", "route_available"),
            ("stale_eta", "route_available"), ("no_route", "no_route"), ("timeout", "provider_timeout"),
            ("redirect", "provider_unavailable"), ("unavailable", "provider_unavailable"),
            *[(case, "invalid_provider_response") for case in (
                "stale_route", "content_type", "json", "duplicate", "oversize", "wrong_version", "wrong_request",
                "wrong_mode", "wrong_destination", "negative_distance", "incomplete", "negative_eta", "zero_eta",
                "nan", "infinity", "excessive_eta", "geometry", "geometry_limit", "bad_coordinate")])],
        "test_loc06_walking_binding_and_bounded_provenance", "test_loc06_provider_connection_failure"),
    "consent_and_privacy": (
        "test_loc06_disclosure_offline_replay_and_cross_session", "test_loc06_external_maps_destination_only"),
    "revocation_and_concurrency": (
        *[f"test_loc06_inflight_cancellation[{case}]" for case in (
            "permission_revoked", "stopped", "navigation", "different_request")],
        "test_loc06_withdrawal_during_http_and_adapter_measurement"),
    "authorization_and_rls": (
        "test_loc06_governed_destination_and_database_boundaries", "test_loc06_doctor_affiliation_and_withdrawal",
        "test_loc06_legacy_and_unpublished_destination_denied",
        *[f"test_loc06_governed_destination_invalidation[{case}]" for case in (
            "withdrawal", "source", "key", "expiry", "replacement")]),
    "security": tuple(f"test_loc06_endpoint_policy[{endpoint}]" for endpoint in ENDPOINTS),
}


def acceptance(backend, chromium, log, prior, commit, head, pipeline):
    tests = list(ET.fromstring(backend).iter("testcase"))
    browser = list(ET.fromstring(chromium).iter("testcase"))
    keys = Counter((node.get("classname"), node.get("name")) for node in tests)
    good = {(node.get("classname"), node.get("name")) for node in tests if not any(
        node.find(tag) is not None for tag in ("failure", "error", "skipped"))}
    gates, cases = {}, {}
    for gate, names in REQUIRED.items():
        for name in names:
            key = CLASS, name
            cases[name] = {"boundary": gate, "outcome": "PASS" if keys[key] == 1 and key in good else "OPEN"}
        gates[gate] = "PASS" if all(cases[name]["outcome"] == "PASS" for name in names) else "OPEN"
    for name in BROWSER:
        matches = [node for node in browser if node.get("name") == name and node.get("classname") == "routing.spec.ts"]
        passed = len(matches) == 1 and all(matches[0].find(tag) is None for tag in ("failure", "error", "skipped"))
        cases[name] = {"boundary": "offline_and_accessibility", "outcome": "PASS" if passed else "OPEN"}
    gates["offline_and_accessibility"] = "PASS" if all(cases[name]["outcome"] == "PASS" for name in BROWSER) else "OPEN"
    overhead = re.findall(r"^LOC06 synthetic routing n=5 destination_ms=(\d+\.\d+) stub_with_checks_ms=(\d+\.\d+)$",
                          log, re.M)
    totals = re.findall(r"^=+ (\d+) passed(?:, \d+ warnings?)? in .* =+$", log, re.M)
    exact = bool(re.fullmatch(r"[0-9a-f]{40}", commit)) and commit == head
    prior_ok = (prior.get("commit") == commit and prior.get("exact_head") is True and prior.get("engineering") == "PASS")
    passed = (exact and prior_ok and pipeline == "success" and len(overhead) == 1
              and totals == [str(len(tests))] and len(good) == len(tests)
              and all(status == "PASS" for status in gates.values()))
    return {
        "commit": commit, "exact_head": exact, "engineering": "PASS" if passed else "OPEN",
        "coverage": cases, "gates": gates, "backend_total": len(tests), "chromium_total": len(browser),
        "prior_location_acceptance": prior_ok, "regression_status": pipeline,
        "synthetic_overhead": ({"samples": 5, "destination_ms": float(overhead[0][0]),
                               "stub_with_checks_ms": float(overhead[0][1])} if len(overhead) == 1 else {}),
        "provider_onboarding": "OPEN", "real_eta_accuracy": "UNVERIFIED",
        "public_directions": "CLOSED", "production_clinical_activation": "CLOSED",
    }


def main():
    temp = Path(os.environ["RUNNER_TEMP"])
    result = acceptance((temp / "phase3-backend.xml").read_bytes(), (temp / "phase3-browser.xml").read_bytes(),
                        (temp / "phase2-backend.log").read_text(encoding="utf-8"),
                        json.loads((temp / "loc05-acceptance.json").read_text(encoding="utf-8")),
                        subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
                        os.environ["VERIFIED_HEAD"], os.environ["REGRESSION_STATUS"])
    (temp / "loc06-acceptance.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return int(result["engineering"] != "PASS")


if __name__ == "__main__":
    raise SystemExit(main())
