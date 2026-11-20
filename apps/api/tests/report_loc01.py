"""Build LOC-01 evidence from exact TAP and Playwright JUnit identities."""
import json
import os
from pathlib import Path
import re
import subprocess
import xml.etree.ElementTree as ET

# Test titles are a separate acceptance contract, independent of the test output.
REQUIRED = {
    "validation": ("unit", "validates boundaries and rejects malformed or stale fixes", "Malformed and expired fixes are excluded"),
    "single-watch": ("unit", "opens one standard accuracy watch and updates fixes", "One watch and ordered fixes"),
    "freshness": ("unit", "expires cached and current fixes and recovers", "Only fresh fixes are current"),
    "watch-zero": ("unit", "clears watch zero and ignores callbacks across restart and disposal", "Late callbacks cannot restore a stopped session"),
    "permission-race": ("unit", "ignores permission completion after stopping", "Late permission cannot start acquisition"),
    "browser-errors": ("unit", "handles browser errors and rejects denied permission", "Retry and recovery stay explicit"),
    "capabilities": ("unit", "handles unsupported insecure and throwing browsers", "Unavailable browsers fail safely"),
    "synchronous-denial": ("unit", "clears a watch denied synchronously", "Synchronous denial clears the returned ID"),
    "consent": ("browser", "requires explicit consent and keeps directory unavailable", "No query or watch before activation"),
    "stop": ("browser", "refreshes stale fixes and clears watch zero on stop", "Freshness and stop are visible"),
    "lifecycle": ("browser", "releases on navigation visibility and pagehide", "Foreground-only tracking"),
    "revocation": ("browser", "handles denied and revoked permission without prompting again", "Explicit restart after permission changes"),
    "unsupported": ("browser", "handles unsupported location capability", "Missing API leaves app usable"),
    "insecure": ("browser", "handles insecure location capability", "Insecure context cannot acquire a fix"),
    "recovery": ("browser", "reports timeout and unavailable and accepts a later fix", "Transient errors and recovery are visible"),
    "offline-emergency": ("browser", "keeps offline emergency independent of location", "Rescue 1122 works offline without a watch"),
    "privacy": ("browser", "keeps coordinates out of requests storage URLs and reloads", "No app-origin disclosure or persistence"),
    "accessibility": ("browser", "provides keyboard live status and accessible mobile layout", "Keyboard, live region, mobile and axe checks"),
    "emergency": ("browser", "emergency help stops an active watch and keeps call available", "Emergency never starts a watch"),
}


def acceptance(unit_log, browser_xml, commit, expected_head, regression_status):
    counts = re.findall(r"^# pass (\d+)$", unit_log, re.M)
    unit_total = int(counts[0]) if len(counts) == 1 else 0
    tests = list(ET.fromstring(browser_xml).iter("testcase"))
    rows = {}
    for key, (suite, title, expected) in REQUIRED.items():
        name = f"LOC01 {suite} {title}"
        if suite == "unit":
            matches = re.findall(rf"^(ok|not ok) \d+ - {re.escape(name)}(.*)$", unit_log, re.M)
            passed = matches == [("ok", "")]
        else:
            matches = [node for node in tests if node.get("name") == name]
            passed = len(matches) == 1 and all(matches[0].find(tag) is None for tag in ("skipped", "failure", "error"))
        rows[key] = {"test": name, "suite": suite, "expected": expected, "outcome": "PASS" if passed else "OPEN"}
    exact_head = bool(re.fullmatch(r"[0-9a-f]{40}", commit)) and commit == expected_head
    passed = (exact_head and regression_status == "success" and unit_total > 0
              and re.findall(r"^# fail (\d+)$", unit_log, re.M) == ["0"] and tests
              and all(row["outcome"] == "PASS" for row in rows.values()))
    return {
        "commit": commit, "exact_head": exact_head, "regression_status": regression_status,
        "engineering": "PASS" if passed else "OPEN", "unit_total": unit_total, "chromium_total": len(tests),
        "coverage": rows, "native_device_qa04": "OPEN", "provider_directory": "NOT_IMPLEMENTED",
        "production_clinical_activation": "CLOSED",
    }


def main():
    temp = Path(os.environ["RUNNER_TEMP"])
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    result = acceptance((temp / "phase2-unit.log").read_text(encoding="utf-8"),
                        (temp / "phase3-browser.xml").read_bytes(), commit,
                        os.environ["VERIFIED_HEAD"], os.environ["REGRESSION_STATUS"])
    (temp / "loc01-acceptance.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return int(result["engineering"] != "PASS")


if __name__ == "__main__":
    raise SystemExit(main())
