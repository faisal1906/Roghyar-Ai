from copy import deepcopy
import xml.etree.ElementTree as ET

import pytest

from report_bk02 import CLASS, MIGRATIONS, REQUIRED, SERIES, acceptance

SHA = "b" * 40


@pytest.mark.parametrize("fault", ["none", "missing", "skip", "failed", "renamed", "wrong_class", "duplicate",
                                   "migration", "sha", "pipeline", "bk01", "phase5", "measurement", "summaries"])
def test_bk02_reporter_integrity(fault):
    xml = ET.Element("testsuite")
    for group in REQUIRED.values():
        for name in group:
            ET.SubElement(xml, "testcase", name=name, classname=CLASS)
    target = next(row for row in xml if row.get("name") == "test_bk02_window_and_confirmation")
    if fault == "missing":
        xml.remove(target)
    elif fault in ("skip", "failed"):
        ET.SubElement(target, "skipped" if fault == "skip" else "failure")
    elif fault in ("renamed", "wrong_class"):
        target.set("name" if fault == "renamed" else "classname", "fake")
    elif fault == "duplicate":
        xml.append(deepcopy(target))
    bk01 = {"exact_head": True, "commit": SHA, "engineering": "OPEN" if fault == "bk01" else "PASS",
            "public_booking": "CLOSED", "public_otp": "CLOSED", "real_sms": "OPEN", "backend_total": len(xml)}
    phase5 = {"exact_head": True, "commit": SHA, "phase4_preserved": True, "tickets": {"loc05": "PASS"},
              "phase5_synthetic_engineering": "OPEN" if fault == "phase5" else "PASS",
              "phase5_real_world_readiness": "OPEN", "backend_total": len(xml),
              "frontend_unit_total": 20, "chromium_total": 63}
    series = SERIES[1:] if fault == "measurement" else SERIES
    log = "BK02 measurements " + " ".join(f"{name}=10,1.000,2.000" for name in series)
    log += f"\n=== {len(xml) + (fault == 'summaries')} passed in 1s ==="
    result = acceptance(ET.tostring(xml), log, "" if fault == "migration" else "\n".join(MIGRATIONS), bk01,
                        phase5, SHA, "a"*40 if fault == "sha" else SHA, "failure" if fault == "pipeline" else "success")
    assert result["engineering"] == ("PASS" if fault == "none" else "OPEN")
