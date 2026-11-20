import xml.etree.ElementTree as ET

import pytest

from report_loc04 import CLASS, REQUIRED, STEPS, acceptance


@pytest.mark.parametrize("case", [
    "valid", "missing", "skipped", "failed", "duplicate", "renamed", "wrong_class", "no_rls",
    "no_independence", "no_revocation", "no_search", "no_migration", "stale_sha", "prior_absent",
    "pipeline_failed", "no_summary",
])
def test_loc04_reporter_rejects_tampered_evidence(case):
    root = ET.Element("testsuite")
    focus = "test_loc04_publish_and_withdraw_search_integration"
    remove = {
        "missing": focus, "no_rls": "test_loc04_database_roles_and_immutable_history",
        "no_independence": "test_loc04_review_denials[self]",
        "no_revocation": "test_loc04_revocation_immediately_removes_both_searches[key]",
        "no_search": "test_loc04_legacy_verified_row_has_no_search_eligibility",
    }
    for name in (name for names in REQUIRED.values() for name in names):
        if name == remove.get(case):
            continue
        identity = name + "_changed" if case == "renamed" and name == focus else name
        suite = "foreign" if case == "wrong_class" and name == focus else CLASS
        node = ET.SubElement(root, "testcase", name=identity, classname=suite)
        if name == focus and case in ("failed", "skipped"):
            ET.SubElement(node, "failure" if case == "failed" else "skipped")
        if name == focus and case == "duplicate":
            ET.SubElement(root, "testcase", name=identity, classname=suite)
    sha = "a" * 40
    prior = {name: {"commit": sha, "exact_head": True, "engineering": "PASS"} for name in ("loc01", "loc02", "loc03")}
    if case == "prior_absent":
        prior.pop("loc02")
    log = "\n".join(STEPS) if case != "no_migration" else STEPS[0]
    if case != "no_summary":
        log += f"\n=== {len(root)} passed in 1.0s ===\n"
    result = acceptance(ET.tostring(root), log, prior, sha,
                        "b" * 40 if case == "stale_sha" else sha,
                        "failure" if case == "pipeline_failed" else "success")
    assert result["engineering"] == ("PASS" if case == "valid" else "OPEN")
    assert result["public_discovery"] == "CLOSED"
