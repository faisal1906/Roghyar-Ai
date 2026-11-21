"""Exact-head BK-05 synthetic notification acceptance."""
from collections import Counter
import json
import os
from pathlib import Path
import re
import subprocess
import xml.etree.ElementTree as ET

CLASS = "apps.api.tests.test_notifications"
MIGRATIONS = ("BK05 previous revision restored", "Upgrade verified: revision 0015_notification_outbox")


def cases(name, labels):
    return tuple(f"test_bk05_{name}[{value}]" for value in labels.split())


REQUIRED = {
    "external_contracts": cases("gateway_pinned_contract",
        "accepted-sms accepted-whatsapp rate-sms rate-whatsapp timeout-sms timeout-whatsapp redirect-sms "
        "redirect-whatsapp oversized-sms oversized-whatsapp wrong_request-sms wrong_request-whatsapp"),
    "consent_and_delivery": cases("consent_booking_replay_and_signed_delivery", "sms whatsapp")
                            + ("test_bk05_no_default_consent_no_historical_backfill",),
    "eligibility_and_minimization": cases("eligibility_revalidated",
        "withdraw missing_translation revoked_template phone_mismatch identity_revoked"),
    "reminder_lifecycle": cases("reminder_lifecycle_races", "queued leased in_flight acknowledged successor")
                          + ("test_bk05_reminder_clock_and_clinic_scope",),
    "leasing_and_recovery": cases("lease_fencing_and_unknown_recovery",
        "leased_restart submitting_restart timeout ack_db_failure parallel"),
    "shared_cost_controls": cases("quota_retries_and_no_fallback", "redis_down quota retry_budget wrong_channel"),
    "callback_authority": cases("callback_authority_and_conflicts", "signature provider message job expired duplicate terminal"),
    "transactional_outbox": cases("atomic_outbox_rollback", "booking lifecycle"),
    "rls_metrics_and_release": ("test_bk05_private_callback_rls_and_metrics",
                                "test_bk05_nonempty_downgrade_preserves_custody"),
}


def acceptance(xml, log, migration, prior, sha, expected, status):
    rows = list(ET.fromstring(xml).iter("testcase"))
    identities = [(node.get("classname"),node.get("name")) for node in rows]
    counts = Counter(identities)
    passing = {identity for node,identity in zip(rows,identities,strict=True)
               if all(node.find(kind) is None for kind in ("failure","error","skipped"))}
    coverage = {case:"PASS" if counts[(CLASS,case)]==1 and (CLASS,case) in passing else "OPEN"
                for group in REQUIRED.values() for case in group}
    gates = {name:"PASS" if all(coverage[case]=="PASS" for case in cases) else "OPEN"
             for name,cases in REQUIRED.items()}
    exact = bool(re.fullmatch(r"[0-9a-f]{40}",sha)) and sha==expected
    migrated = all(marker in migration for marker in MIGRATIONS)
    totals = re.findall(r"^=+ (\d+) passed(?:, \d+ warnings?)? in .* =+$",log,re.M)
    phase5 = prior.get("phase5",{})
    tickets = tuple(f"bk0{i}" for i in range(1,5))
    previous = (set(prior)=={"phase5",*tickets}
                and all(item.get("commit")==sha and item.get("exact_head") is True
                        and item.get("backend_total")==len(rows) for item in prior.values())
                and phase5.get("phase4_preserved") is True and phase5.get("phase5_synthetic_engineering")=="PASS"
                and all(prior[t].get("engineering")=="PASS" and prior[t].get("public_booking")=="CLOSED"
                        for t in tickets)
                and prior["bk01"].get("real_sms")=="OPEN" and prior["bk04"].get("public_lifecycle")=="CLOSED"
                and all(type(phase5.get(k)) is int and phase5[k]>0 for k in ("frontend_unit_total","chromium_total")))
    complete = (exact and migrated and previous and status=="success" and len(passing)==len(rows)
                and totals==[str(len(rows))] and all(result=="PASS" for result in gates.values()))
    return {"commit":sha,"exact_head":exact,"engineering":"PASS" if complete else "OPEN",
            "coverage":coverage,"gates":gates,"migration_round_trip":migrated,"previous_engineering":previous,
            "backend_total":len(rows),"frontend_unit_total":phase5.get("frontend_unit_total"),
            "chromium_total":phase5.get("chromium_total"),"real_sms":"OPEN","official_whatsapp":"OPEN",
            "real_delivery":"NOT_EVALUATED","template_and_consent_approval":"OPEN","provider_acknowledgement":"OPEN",
            "public_callbacks":"CLOSED","public_messaging":"CLOSED","public_reminders":"CLOSED",
            "public_booking":"CLOSED","public_lifecycle":"CLOSED","public_otp":"CLOSED",
            "native_qa04":"OPEN","clinical_qa01":"OPEN","production_clinical_activation":"CLOSED"}


def main():
    temp = Path(os.environ["RUNNER_TEMP"])
    sha = subprocess.check_output(["git","rev-parse","HEAD"],text=True).strip()

    def read(filename):
        return (temp/filename).read_text(encoding="utf-8")

    try:
        keys = ("phase5","bk01","bk02","bk03","bk04")
        prior = {key:json.loads(read(f"{key}-acceptance.json")) for key in keys}
        report = acceptance((temp/"phase3-backend.xml").read_bytes(),read("phase2-backend.log"),
                            read("bk05-migrations.log"),prior,sha,os.environ["VERIFIED_HEAD"],
                            os.environ["REGRESSION_STATUS"])
    except (OSError, ValueError, TypeError, KeyError, AttributeError, ET.ParseError):
        report = {"commit":sha,"engineering":"OPEN","reason":"invalid_or_missing_evidence"}
    (temp/"bk05-acceptance.json").write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    return int(report["engineering"]!="PASS")


if __name__=="__main__":
    raise SystemExit(main())
