import xml.etree.ElementTree as ET

import pytest

from report_bk06 import BACKEND, BROWSER, acceptance
from report_phase6 import acceptance as rollup


@pytest.mark.parametrize("fault",["none","skip","failure","duplicate","missing","wrong_sha","missing_migration"])
def test_bk06_report_open_and_verification_integrity(fault):
    backend=ET.Element("testsuite")
    browser=ET.Element("testsuite")
    for title in BACKEND:
        ET.SubElement(backend,"testcase",classname="apps.api.tests.test_hospital_admin",name=title)
    for title in BROWSER:
        ET.SubElement(browser,"testcase",classname="private-admin.spec.ts",name=title)
    if fault in ("skip","failure"):
        ET.SubElement(backend[0],"skipped" if fault=="skip" else "failure")
    if fault=="duplicate":
        ET.SubElement(backend,"testcase",attrib=backend[0].attrib)
    if fault=="missing":
        browser.remove(browser[0])
    sha="6"*40
    prior={"commit":sha,"exact_head":True,"engineering":"PASS","backend_total":len(backend),
           "chromium_total":len(browser),"frontend_unit_total":20,"public_messaging":"CLOSED"}
    migrations="BK06 previous revision restored\nUpgrade verified: revision 0016_hospital_admin_rbac"
    report=acceptance(ET.tostring(backend),ET.tostring(browser),f"=== {len(backend)} passed in 1s ===",
                      "" if fault=="missing_migration" else migrations,prior,sha,"7"*40 if fault=="wrong_sha" else sha,
                      "success")
    assert report["engineering"]=="OPEN"
    assert report["verified_foundation"]==("PASS" if fault=="none" else "OPEN")


def test_phase6_rollup_keeps_incomplete_and_wrong_head_tickets_open():
    sha="a"*40
    baseline={"commit":sha,"exact_head":True,"backend_total":80,"frontend_unit_total":20,"chromium_total":65}
    evidence={f"bk0{i}":baseline|{"engineering":"PASS","migration_round_trip":True} for i in range(1,7)}
    evidence["bk02"]["migration"]=True
    evidence["bk01"]["public_otp"]="CLOSED"
    evidence["bk03"]["public_booking"]="CLOSED"
    evidence["bk05"]["public_messaging"]="CLOSED"
    evidence["bk06"].update(public_admin="CLOSED",real_clinic_acceptance="OPEN")
    previous=baseline|{"phase4_preserved":True,"phase5_synthetic_engineering":"PASS"}
    assert rollup(evidence,previous,sha,sha,"success")["phase6_synthetic_engineering"]=="PASS"
    evidence["bk06"]["engineering"]="OPEN"
    assert rollup(evidence,previous,sha,sha,"success")["phase6_synthetic_engineering"]=="OPEN"
    evidence["bk06"].update(engineering="PASS",commit="b"*40)
    assert rollup(evidence,previous,sha,sha,"success")["tickets"]["bk06"]=="OPEN"
