"""Phase 6 rollup. All six acceptance artifacts must pass on the same head."""
import json
import os
from pathlib import Path
import re
import subprocess


def acceptance(artifacts, phase5, sha, head, pipeline):
    keys={f"bk0{i}" for i in range(1,7)}
    exact=bool(re.fullmatch(r"[0-9a-f]{40}",sha)) and sha==head
    tickets={ticket:"PASS" if item.get("engineering")=="PASS" and item.get("commit")==sha
             and item.get("exact_head") is True and item.get("migration" if ticket=="bk02" else "migration_round_trip") is True
             and all(item.get(key)==phase5.get(key) for key in ("backend_total","frontend_unit_total","chromium_total"))
             else "OPEN" for ticket in sorted(keys) for item in (artifacts.get(ticket,{}),)}
    prior=(phase5.get("exact_head") is True and phase5.get("commit")==sha
           and phase5.get("phase4_preserved") is True and phase5.get("phase5_synthetic_engineering")=="PASS"
           and all(type(phase5.get(key)) is int and phase5[key]>0
                   for key in ("backend_total","frontend_unit_total","chromium_total")))
    closed=(artifacts.get("bk01",{}).get("public_otp")=="CLOSED"
            and artifacts.get("bk03",{}).get("public_booking")=="CLOSED"
            and artifacts.get("bk05",{}).get("public_messaging")=="CLOSED"
            and artifacts.get("bk06",{}).get("public_admin")=="CLOSED"
            and artifacts.get("bk06",{}).get("real_clinic_acceptance")=="OPEN")
    complete=exact and prior and closed and set(artifacts)==keys and pipeline=="success" and set(tickets.values())=={"PASS"}
    return {"commit":sha,"exact_head":exact,"tickets":tickets,"phase5_preserved":prior,
            "phase6_synthetic_engineering":"PASS" if complete else "OPEN",
            "phase6_real_world_readiness":"OPEN","public_admin":"CLOSED","production_activation":"CLOSED"}


def main():
    temp=Path(os.environ["RUNNER_TEMP"])
    sha=subprocess.check_output(["git","rev-parse","HEAD"],text=True).strip()

    def read(key):
        path=temp/f"{key}-acceptance.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    result=acceptance({f"bk0{i}":read(f"bk0{i}") for i in range(1,7)},read("phase5"),
                      sha,os.environ["VERIFIED_HEAD"],os.environ["REGRESSION_STATUS"])
    (temp/"phase6-acceptance.json").write_text(json.dumps(result,indent=2)+"\n",encoding="utf-8")


if __name__=="__main__":
    main()
