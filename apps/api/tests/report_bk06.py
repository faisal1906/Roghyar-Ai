"""Independently report verified BK-06 coverage while unfinished gates remain open."""
from collections import Counter
import json
import os
from pathlib import Path
import re
import subprocess
import xml.etree.ElementTree as ET

BACKEND = ("test_bk06_facility_scope_and_permission", "test_bk06_live_membership_revision_and_logout",
           "test_bk06_independent_provisioning[signature]", "test_bk06_independent_provisioning[scope]",
           "test_bk06_independent_provisioning[self]", "test_bk06_independent_provisioning[evidence]",
           "test_bk06_database_privileges_and_public_closure", "test_bk06_private_http",
           "test_bk06_clinic_confirmation_binding_and_notification")
BROWSER = ("BK06 private dashboard presents scoped records and clears the in-memory session",
           "BK06 public build does not mount the administration dashboard")
OPEN_REQUIREMENTS = ("governed_doctor_and_schedule_submission_handoff", "admin_appointment_mutations_and_concurrency",
                     "clinic_acknowledgement_revocation_and_race_matrix", "bk05_reminder_handoff",
                     "dashboard_mutation_sections_and_live_browser_integration",
                     "representative_admin_performance_and_full_security_matrix")


def passing(xml, classname, names):
    rows = list(ET.fromstring(xml).iter("testcase"))
    identities = [(node.get("classname"),node.get("name")) for node in rows]
    count = Counter(identities)
    good = {key for node,key in zip(rows,identities,strict=True)
            if all(node.find(tag) is None for tag in ("skipped","failure","error"))}
    return len(rows),len(rows)==len(good),{name:"PASS" if count[(classname,name)]==1 and (classname,name) in good
                                         else "OPEN" for name in names}


def acceptance(backend,browser,log,migrations,prior,commit,head,pipeline):
    total, backend_ok, backend_cases = passing(backend,"apps.api.tests.test_hospital_admin",BACKEND)
    chromium, chromium_ok, browser_cases = passing(browser,"private-admin.spec.ts",BROWSER)
    exact=bool(re.fullmatch(r"[0-9a-f]{40}",commit)) and commit==head
    totals=re.findall(r"^=+ (\d+) passed(?:, \d+ warnings?)? in .* =+$",log,re.M)
    migration=all(item in migrations for item in ("BK06 previous revision restored",
                                                  "Upgrade verified: revision 0016_hospital_admin_rbac"))
    previous=(prior.get("commit")==commit and prior.get("exact_head") is True and prior.get("engineering")=="PASS"
              and prior.get("backend_total")==total and prior.get("chromium_total")==chromium
              and type(prior.get("frontend_unit_total")) is int and prior["frontend_unit_total"]>0
              and prior.get("public_messaging")=="CLOSED")
    coverage=backend_cases|browser_cases
    verified=(exact and backend_ok and chromium_ok and totals==[str(total)] and migration and previous
              and pipeline=="success" and all(state=="PASS" for state in coverage.values()))
    return {"commit":commit,"exact_head":exact,"engineering":"OPEN","verified_foundation":"PASS" if verified else "OPEN",
            "coverage":coverage,"open_requirements":OPEN_REQUIREMENTS,"migration_round_trip":migration,
            "backend_total":total,"frontend_unit_total":prior.get("frontend_unit_total"),"chromium_total":chromium,
            "previous_engineering":previous,"public_admin":"CLOSED","real_staff_onboarding":"OPEN",
            "real_clinic_acceptance":"OPEN","production_clinical_activation":"CLOSED"}


def main():
    temp=Path(os.environ["RUNNER_TEMP"])
    commit=subprocess.check_output(["git","rev-parse","HEAD"],text=True).strip()

    def read(name):
        return (temp/name).read_text(encoding="utf-8")

    try:
        result=acceptance((temp/"phase3-backend.xml").read_bytes(),(temp/"phase3-browser.xml").read_bytes(),
                          read("phase2-backend.log"),read("bk06-migrations.log"),json.loads(read("bk05-acceptance.json")),
                          commit,os.environ["VERIFIED_HEAD"],os.environ["REGRESSION_STATUS"])
    except (OSError,ValueError,TypeError,KeyError,ET.ParseError):
        result={"commit":commit,"engineering":"OPEN","verified_foundation":"OPEN","reason":"invalid_or_missing_evidence"}
    (temp/"bk06-acceptance.json").write_text(json.dumps(result,indent=2)+"\n",encoding="utf-8")
    return int(result["verified_foundation"]!="PASS")


if __name__=="__main__":
    raise SystemExit(main())
