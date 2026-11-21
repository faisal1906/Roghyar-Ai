from copy import deepcopy
import xml.etree.ElementTree as ET

import pytest

from report_bk05 import CLASS, MIGRATIONS, REQUIRED, acceptance


@pytest.mark.parametrize("fault",["none","skip","failed","missing","duplicate","name","class","sha",
                                 "migration","prior","prior_missing","pipeline","total"])
def test_bk05_reporter_integrity(fault):
    sha = "5"*40
    root = ET.Element("testsuite")
    for cases in REQUIRED.values():
        for name in cases:
            ET.SubElement(root,"testcase",name=name,classname=CLASS)
    target = next(row for row in root if row.get("name")=="test_bk05_atomic_outbox_rollback[booking]")
    if fault in ("skip","failed"):
        ET.SubElement(target,"skipped" if fault=="skip" else "failure")
    if fault=="missing":
        root.remove(target)
    if fault=="duplicate":
        root.append(deepcopy(target))
    if fault in ("name","class"):
        target.set("name" if fault=="name" else "classname","unrelated")
    shared = {"commit":sha,"exact_head":True,"backend_total":len(root)}
    prior = {key:shared|{"engineering":"PASS","public_booking":"CLOSED"} for key in ("bk01","bk02","bk03","bk04")}
    prior["bk01"]["real_sms"]="OPEN"
    prior["bk04"]["public_lifecycle"]="CLOSED"
    prior["phase5"]=shared|{"phase4_preserved":True,"phase5_synthetic_engineering":"PASS",
                           "frontend_unit_total":20,"chromium_total":63}
    if fault=="prior":
        prior["bk04"]["engineering"]="OPEN"
    if fault=="prior_missing":
        del prior["bk04"]
    data=acceptance(ET.tostring(root),f"=== {len(root)+(fault=='total')} passed in 1s ===",
                    "" if fault=="migration" else "\n".join(MIGRATIONS),prior,sha,"b"*40 if fault=="sha" else sha,
                    "failure" if fault=="pipeline" else "success")
    assert data["engineering"]==("PASS" if fault=="none" else "OPEN")
