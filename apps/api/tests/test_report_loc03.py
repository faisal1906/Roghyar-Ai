"""LOC-03 artifact checks for missing, invalid, or mismatched evidence."""
import xml.etree.ElementTree as ET

import pytest

from report_loc03 import CASES, CLASS, acceptance


@pytest.mark.parametrize("case", (
    "valid", "missing", "skipped", "failed", "duplicate", "renamed", "wrong_class", "fuzzy_missing",
    "withdrawal_missing", "rls_missing", "plan_missing", "plan_altered", "summary", "stale_head",
    "pipeline", "loc01_open", "loc02_stale",
))
def test_loc03_reporter_requires_live_evidence(case):
    sha = "a" * 40
    root = ET.Element("testsuite")
    removed = {"missing": "test_loc03_matching[name]", "fuzzy_missing": "test_loc03_ranking",
               "withdrawal_missing": "test_loc03_eligibility_and_withdrawal",
               "rls_missing": "test_loc03_runtime_and_timeout"}
    for name in (name for cases in CASES.values() for name in cases):
        if removed.get(case) == name:
            continue
        selected = name == "test_loc03_matching[name]"
        classname = "forged" if selected and case == "wrong_class" else CLASS
        identity = name + "_changed" if selected and case == "renamed" else name
        node = ET.SubElement(root, "testcase", classname=classname, name=identity)
        if selected and case in ("skipped", "failed"):
            ET.SubElement(node, "skipped" if case == "skipped" else "failure")
        if selected and case == "duplicate":
            ET.SubElement(root, "testcase", classname=classname, name=identity)
    prior = {name: {"commit": sha, "engineering": "PASS", "exact_head": True} for name in ("loc01", "loc02")}
    if case == "loc01_open":
        prior["loc01"]["engineering"] = "OPEN"
    elif case == "loc02_stale":
        prior["loc02"]["commit"] = "b" * 40
    line = "LOC03 plans common=0,0,1.000 uncommon=1,0,2.000 fuzzy=0,1,3.000\n"
    if case == "plan_altered":
        line = line.replace("0,1,3.000", "PASS")
    elif case == "plan_missing":
        line = ""
    summary = "" if case == "summary" else f"=== {len(root)} passed in 1.0s ===\n"
    decision = acceptance(ET.tostring(root), line + summary, prior, sha,
                          "b" * 40 if case == "stale_head" else sha,
                          "failure" if case == "pipeline" else "success")
    assert decision["engineering"] == ("PASS" if case == "valid" else "OPEN")
    assert decision["public_discovery"] == "CLOSED"
