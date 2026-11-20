"""LOC-02 gates depend on proof, not editable gate labels."""
import xml.etree.ElementTree as ET

import pytest

from report_loc02 import CASES, CLASS, acceptance


@pytest.mark.parametrize("change", (
    "valid", "missing", "skipped", "failed", "duplicate", "renamed", "no_postgis", "no_rls",
    "wrong_class", "tampered_plan", "bad_summary", "wrong_head", "failed_pipeline", "loc01_open",
))
def test_loc02_reporter_rejects_incomplete_evidence(change):
    suite = ET.Element("testsuite")
    mandatory = [name for group in CASES.values() for name in group]
    first = mandatory[0]
    for name in mandatory:
        if ((name == first and change == "missing")
                or (name == "test_loc02_index_plan" and change == "no_postgis")
                or (name == "test_loc02_runtime_rls_eligibility_and_revocation" and change == "no_rls")):
            continue
        identity = name + "_changed" if name == first and change == "renamed" else name
        classname = "untrusted" if name == first and change == "wrong_class" else CLASS
        node = ET.SubElement(suite, "testcase", name=identity, classname=classname)
        if name == first:
            if change == "skipped":
                ET.SubElement(node, "skipped")
            if change == "failed":
                ET.SubElement(node, "failure")
            if change == "duplicate":
                ET.SubElement(suite, "testcase", name=identity, classname=classname)
    sha = "a" * 40
    plans = "" if change == "tampered_plan" else "LOC02 plan natural index=true diagnostic index=true\n"
    summary = "" if change == "bad_summary" else f"===== {len(suite)} passed in 1.0s =====\n"
    loc01 = {"commit": sha, "engineering": "OPEN" if change == "loc01_open" else "PASS", "exact_head": True}
    result = acceptance(ET.tostring(suite), plans + summary, loc01, sha,
                        "b" * 40 if change == "wrong_head" else sha,
                        "failure" if change == "failed_pipeline" else "success")
    assert result["engineering"] == ("PASS" if change == "valid" else "OPEN")
    assert result["public_discovery"] == "CLOSED"
