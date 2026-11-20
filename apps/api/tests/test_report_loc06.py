import xml.etree.ElementTree as ET

import pytest

from report_loc06 import BROWSER, CLASS, REQUIRED, acceptance


@pytest.mark.parametrize("case", [
    "valid", "missing", "skipped", "failed", "duplicate", "wrong_class", "renamed", "no_live",
    "no_provider", "no_disclosure", "no_withdrawal", "no_stale", "no_eta", "no_accessibility",
    "bad_browser_class", "wrong_sha", "no_loc05", "pipeline", "summary", "no_measurement",
])
def test_loc06_reporter_requires_complete_proof(case):
    root, browser = ET.Element("testsuite"), ET.Element("testsuite")
    focus = "test_loc06_walking_binding_and_bounded_provenance"
    omissions = {
        "missing": focus, "no_live": "test_loc06_governed_destination_and_database_boundaries",
        "no_provider": "test_loc06_provider_contract[timeout-provider_timeout]",
        "no_disclosure": "test_loc06_disclosure_offline_replay_and_cross_session",
        "no_withdrawal": "test_loc06_withdrawal_during_http_and_adapter_measurement",
        "no_stale": "test_loc06_inflight_cancellation[navigation]",
        "no_eta": "test_loc06_provider_contract[no_eta-route_available]",
    }
    for name in (name for values in REQUIRED.values() for name in values):
        if omissions.get(case) == name:
            continue
        title = name + "_changed" if name == focus and case == "renamed" else name
        cls = "foreign" if name == focus and case == "wrong_class" else CLASS
        node = ET.SubElement(root, "testcase", classname=cls, name=title)
        if name == focus and case in ("failed", "skipped"):
            ET.SubElement(node, "failure" if case == "failed" else "skipped")
        if name == focus and case == "duplicate":
            ET.SubElement(root, "testcase", classname=cls, name=title)
    for name in BROWSER:
        if case == "no_accessibility":
            continue
        ET.SubElement(browser, "testcase", classname="forged.ts" if case == "bad_browser_class" else "routing.spec.ts",
                      name=name)
    sha = "b" * 40
    prior = {} if case == "no_loc05" else {"commit": sha, "exact_head": True, "engineering": "PASS"}
    log = "" if case == "no_measurement" else "LOC06 synthetic routing n=5 destination_ms=1.000 stub_with_checks_ms=2.000\n"
    if case != "summary":
        log += f"=== {len(root)} passed in 1.0s ==="
    result = acceptance(ET.tostring(root), ET.tostring(browser), log, prior, sha,
                        "a" * 40 if case == "wrong_sha" else sha, "failure" if case == "pipeline" else "success")
    assert result["engineering"] == ("PASS" if case == "valid" else "OPEN")
    assert result["public_directions"] == "CLOSED"
