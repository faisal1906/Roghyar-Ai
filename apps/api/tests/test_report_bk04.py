from copy import deepcopy
import xml.etree.ElementTree as ET

import pytest

from report_bk04 import CLASS, MIGRATIONS, REQUIRED, SERIES, acceptance


@pytest.mark.parametrize("fault", ["none", "missing", "skip", "failed", "duplicate", "renamed", "wrong_class", "sha",
                                   "migration", "pipeline", "earlier_gate", "earlier_missing", "totals", "timings"])
def test_bk04_reporter_integrity(fault):
    sha = "4"*40
    root = ET.Element("testsuite")
    for group in REQUIRED.values():
        for name in group:
            ET.SubElement(root, "testcase", name=name, classname=CLASS)
    target = next(row for row in root if row.get("name") == "test_bk04_concurrent_lifecycle[two_patients]")
    if fault == "missing":
        root.remove(target)
    elif fault in ("skip", "failed"):
        ET.SubElement(target, "skipped" if fault == "skip" else "failure")
    elif fault == "duplicate":
        root.append(deepcopy(target))
    elif fault in ("renamed", "wrong_class"):
        target.set("name" if fault == "renamed" else "classname", "unrelated")
    shared = {"commit": sha, "exact_head": True, "backend_total": len(root)}
    prior = {key: shared | {"engineering":"PASS","public_booking":"CLOSED"}
             for key in ("bk01", "bk02", "bk03")}
    prior["bk01"]["real_sms"] = "OPEN"
    prior["bk03"]["real_booking"] = "CLOSED"
    prior["phase5"] = shared | {"phase4_preserved":True, "phase5_synthetic_engineering":"PASS",
                                "frontend_unit_total":20, "chromium_total":63}
    if fault == "earlier_gate":
        prior["bk03"]["engineering"] = "OPEN"
    if fault == "earlier_missing":
        del prior["bk03"]
    log = "BK04 measurements " + " ".join(f"{name}=2,1.000,2.000" for name in SERIES
                                           if fault != "timings" or name != "cancel")
    log += f"\n=== {len(root)+(fault=='totals')} passed in 1s ==="
    out = acceptance(ET.tostring(root), log, "" if fault == "migration" else "\n".join(MIGRATIONS),
                     prior, sha, "2"*40 if fault == "sha" else sha, "failure" if fault == "pipeline" else "success")
    assert out["engineering"] == ("PASS" if fault == "none" else "OPEN")
