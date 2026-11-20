"""Check that missing or modified hosted evidence cannot close LOC-01."""
import xml.etree.ElementTree as ET

import pytest

from report_loc01 import REQUIRED, acceptance


@pytest.mark.parametrize("change", [
    "valid", "unit_missing", "unit_skip", "browser_missing", "browser_renamed", "browser_skip",
    "browser_failure", "browser_duplicate", "wrong_head", "regression_failure", "totals_missing",
])
def test_loc01_acceptance_requires_exact_evidence(change):
    root = ET.Element("testsuite")
    unit = []
    for key, (suite, title, _) in REQUIRED.items():
        name = f"LOC01 {suite} {title}"
        if suite == "unit":
            if key == "validation" and change == "unit_missing":
                continue
            skip = " # SKIP" if key == "validation" and change == "unit_skip" else ""
            unit.append(f"ok {len(unit) + 1} - {name}{skip}")
        elif key == "consent":
            if change == "browser_missing":
                continue
            if change == "browser_renamed":
                name += " renamed"
            row = ET.SubElement(root, "testcase", name=name)
            if change == "browser_skip":
                ET.SubElement(row, "skipped")
            if change == "browser_failure":
                ET.SubElement(row, "failure")
            if change == "browser_duplicate":
                ET.SubElement(root, "testcase", name=name)
        else:
            ET.SubElement(root, "testcase", name=name)
    if change != "totals_missing":
        unit += [f"# pass {len(unit)}", "# fail 0"]
    commit = "a" * 40
    result = acceptance("\n".join(unit), ET.tostring(root), commit,
                        "b" * 40 if change == "wrong_head" else commit,
                        "failure" if change == "regression_failure" else "success")
    assert result["engineering"] == ("PASS" if change == "valid" else "OPEN")
    assert result["native_device_qa04"] == "OPEN"
    assert result["production_clinical_activation"] == "CLOSED"
