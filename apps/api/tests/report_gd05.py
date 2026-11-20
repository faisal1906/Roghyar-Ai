"""Bind Phase 4 evidence to actual required test outcomes."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

temp = Path(os.environ["RUNNER_TEMP"])
manifest = Path(__file__).with_name("gd05_coverage.json").read_bytes()
def unique(pairs):
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError("Duplicate manifest key")
    return result

inventory = json.loads(manifest.decode("utf-8-sig"), object_pairs_hook=unique)
requirements = inventory["requirements"]
if inventory["version"] != 3:
    raise SystemExit("Unsupported Phase 4 manifest")

mandatory = {"GD01", "GD02", "GD03-AGE", "GD03-PREG", "GD03-HISTORY", "GD04-TRANSLATION",
             "GD04-ATOMIC", "GD04-WRITER", "GD05-LANDSCAPE", "GD05-OFFLINE", "GD05-CHAIN",
             "GD05-PROVENANCE", "GD05-PREG-APPLICABILITY", "GD05-INGREDIENT", "GD05-CHAIN-POSTGRES"}
mandatory |= {"GD05-AGE-INPUT-" + suffix for suffix in (
    "NULL", "BOOLEAN", "STRING", "FRACTIONAL", "NEGATIVE", "ABOVE-MAXIMUM", "MISSING", "0", "1", "17", "18", "120",
)}
if requirements.keys() != mandatory or len({(row[0], row[1]) for row in requirements.values()}) != len(requirements):
    raise SystemExit("Invalid Phase 4 manifest")
suites = {name: list(ET.parse(temp / f"phase3-{name}.xml").iter("testcase"))
          for name in ("backend", "browser")}
results = {}
for key, (suite, test, expected) in requirements.items():
    matches = [node for node in suites[suite] if node.get("name") == test]
    status = "NOT_TESTED"
    if len(matches) == 1 and matches[0].find("skipped") is None:
        status = "FAIL" if any(matches[0].find(tag) is not None for tag in ("failure", "error")) else "PASS"
    results[key] = dict(suite=suite, test=test, expected=expected, result=status)
log = (temp / "phase2-backend.log").read_text(encoding="utf-8")
benchmarks = {name: float(value) for name, value in re.findall(
    r"GD-05 publication (\w+) n=1 elapsed_ms=([\d.]+)", log)}
sha = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
engineering_ok = sha == os.environ["VERIFIED_HEAD"] and os.environ["REGRESSION_STATUS"] == "success"
publication_ok = all(results[key]["result"] == "PASS" for key in mandatory if key.startswith("GD04"))
publication_ok &= benchmarks.keys() == {"stage", "review", "ready", "activate", "replace", "rollback", "revoke", "post_revoke"}
# Track independently required inventories until each has a complete case-level mapping.
required_open = {"GD05-CHAIN-MUTATIONS", "GD05-AGE-INVENTORY",
                 "GD05-PROVENANCE-INVENTORY", "GD05-SCREENING-INVENTORY"}
if set(inventory["uncovered"]) != required_open:
    raise SystemExit("Phase 4 inventory missing a mandatory open group")
uncovered = dict(inventory["uncovered"])
for key, result in results.items():
    if result["result"] != "PASS":
        uncovered[key] = result["result"]
if not engineering_ok:
    uncovered["GD05-EXACT-HEAD"] = "Regression pipeline or SHA verification failed"
if not publication_ok:
    uncovered["GD04-PUBLICATION"] = "Publication tests or timing evidence incomplete"
passed = not uncovered
frontend = re.findall(r"^# pass (\d+)$", (temp / "phase2-unit.log").read_text(encoding="utf-8"), re.M)
if len(frontend) != 1 or int(frontend[0]) == 0:
    raise SystemExit("Missing frontend verification")
report = dict(external=inventory["external"], uncovered=uncovered, gd05="PASS" if passed else "OPEN",
              frontend_passed=int(frontend[0]), real_model="NOT_EVALUATED",commit=sha, manifest_sha256=hashlib.sha256(manifest).hexdigest(), coverage=results,
              totals={key: len(rows) for key, rows in suites.items()}, publication_single_sample_ms=benchmarks,
              publication="PASS" if publication_ok and engineering_ok else "OPEN",
              phase4="PASS" if passed else "OPEN", clinical="EXTERNAL_VALIDATION_REQUIRED",
              physical_device="EXTERNAL_VALIDATION_REQUIRED", production="CLOSED")
path = temp / "phase4-acceptance.json"
path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
sys.exit(0 if engineering_ok and publication_ok and all(r["result"] == "PASS" for r in results.values()) else 1)
