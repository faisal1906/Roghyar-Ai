import xml.etree.ElementTree as ET

import pytest

from report_loc05 import CLASS, PLAN_NAMES, REQUIRED, STEPS, acceptance


@pytest.mark.parametrize("case", [
    "valid", "missing", "failed", "skipped", "duplicate", "renamed", "wrong_class",
    "no_independence", "no_timezone", "no_revocation", "no_occupancy", "no_rls", "no_plan",
    "no_migration", "bad_plan", "stale_head", "missing_prior", "stale_prior", "incomplete_run", "no_summary",
])
def test_loc05_reporter_requires_current_complete_proof(case):
    root = ET.Element("testsuite")
    focus = "test_loc05_identity_discovery_and_affiliation"
    omitted = {
        "missing": focus, "no_independence": "test_loc05_independent_review_denials[self]",
        "no_timezone": "test_loc05_controlled_clock_and_timezone",
        "no_revocation": "test_loc05_replacement_revocation_and_stale_approval",
        "no_occupancy": "test_loc05_booking_snapshot_and_duplicate_active_start",
        "no_rls": "test_loc05_publication_race_and_permissions",
    }
    for name in (name for group in REQUIRED.values() for name in group):
        if omitted.get(case) == name:
            continue
        title = name + "_altered" if case == "renamed" and name == focus else name
        suite = "unrelated" if case == "wrong_class" and name == focus else CLASS
        test = ET.SubElement(root, "testcase", classname=suite, name=title)
        if name == focus and case in ("failed", "skipped"):
            ET.SubElement(test, "failure" if case == "failed" else "skipped")
        if name == focus and case == "duplicate":
            ET.SubElement(root, "testcase", classname=suite, name=title)
    sha = "a" * 40
    prior = {name: {"commit": sha, "engineering": "PASS", "exact_head": True}
             for name in ("loc01", "loc02", "loc03", "loc04")}
    if case == "missing_prior":
        prior.pop("loc04")
    if case == "stale_prior":
        prior["loc04"]["commit"] = "b" * 40
    output = "\n".join(STEPS if case != "no_migration" else STEPS[:1])
    if case != "no_plan":
        output += "\nLOC05 plans " + " ".join(f"{key}=1,1.000" for key in PLAN_NAMES)
        if case == "bad_plan":
            output = output.replace("occupancy=1,1.000", "occupancy=PASS")
    if case != "no_summary":
        output += f"\n=== {len(root)} passed in 1.1s ==="
    result = acceptance(ET.tostring(root), output, prior, sha, "b" * 40 if case == "stale_head" else sha,
                        "failure" if case == "incomplete_run" else "success")
    assert result["engineering"] == ("PASS" if case == "valid" else "OPEN")
    assert result["public_doctor_discovery"] == result["real_booking"] == "CLOSED"
