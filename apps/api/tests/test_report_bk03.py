from copy import deepcopy
import xml.etree.ElementTree as ET

import pytest

from report_bk03 import CLASS, MIGRATIONS, REQUIRED, SERIES, acceptance

SHA = "a" * 40


@pytest.mark.parametrize("fault", ["none", "missing", "skip", "failure", "duplicate", "wrong_name",
                                   "wrong_identity", "migration", "sha", "pipeline", "previous_commit",
                                   "previous_gate", "measurements", "totals"])
def test_bk03_reporter_integrity(fault):
    xml = ET.Element("testsuite")
    for group in REQUIRED.values():
        for name in group:
            ET.SubElement(xml, "testcase", classname=CLASS, name=name)
    target = next(node for node in xml if node.get("name") == "test_bk03_concurrent_booking[same_slot]")
    if fault == "missing":
        xml.remove(target)
    elif fault in ("skip","failure"):
        ET.SubElement(target, "skipped" if fault == "skip" else "failure")
    elif fault == "duplicate":
        xml.append(deepcopy(target))
    elif fault in ("wrong_name", "wrong_identity"):
        target.set("name" if fault == "wrong_name" else "classname", "another")
    prior = {"commit": SHA, "exact_head": True, "backend_total": len(xml)}
    phase5 = prior | {"phase4_preserved":True, "phase5_synthetic_engineering":"PASS",
                      "frontend_unit_total":20, "chromium_total":63}
    bk01 = prior | {"engineering":"PASS", "public_booking":"CLOSED", "real_sms":"OPEN"}
    bk02 = prior | {"engineering":"PASS", "public_booking":"CLOSED", "real_provider_schedules":"OPEN",
                    "public_slots":"CLOSED"}
    if fault == "previous_commit":
        bk01["commit"] = "b"*40
    if fault == "previous_gate":
        bk02["engineering"] = "OPEN"
    log = ("BK03 measurements " + " ".join(f"{name}=2,1.000,2.000" for name in SERIES)
           if fault != "measurements" else "")
    log += f"\n=== {len(xml) + (fault == 'totals')} passed in 1s ==="
    result = acceptance(ET.tostring(xml), log, "" if fault == "migration" else "\n".join(MIGRATIONS),
                        phase5,bk01,bk02,SHA,"b"*40 if fault == "sha" else SHA,
                        "failure" if fault == "pipeline" else "success")
    assert result["engineering"] == ("PASS" if fault == "none" else "OPEN")
