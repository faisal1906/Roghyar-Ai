"""Emit sanitized engineering evidence from pytest JUnit. No model calls."""
import argparse
import hashlib
import json
import platform
import os
import re
import subprocess
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("junit", type=Path)
parser.add_argument("output", type=Path)
parser.add_argument("--frontend", type=int)
parser.add_argument("--chromium", type=int)
parser.add_argument("--backend-log", type=Path, required=True)
parser.add_argument("--browser-xml", type=Path, required=True)
args = parser.parse_args()
raw = Path(__file__).with_name("ai06_corpus.json").read_bytes()
corpus = json.loads(raw.decode("utf-8-sig"))
cases = corpus["cases"]
if (corpus["version"] != 1 or len(cases) != 12 or len({c["id"] for c in cases}) != len(cases)
        or any(set(c) != {"id", "group", "text", "languages", "labels", "category"} or not c["text"] for c in cases)):
    raise SystemExit("Invalid corpus")
benchmarks = {}
for name, count, p50, p95 in re.findall(r"AI06_BENCH (\w+) (\d+) ([\d.]+) ([\d.]+)",
                                       args.backend_log.read_text(encoding="utf-8")):
    if name in benchmarks or not 0 <= float(p50) <= float(p95) or int(count) != 200:
        raise SystemExit("Invalid benchmark evidence")
    benchmarks[name] = {"samples": int(count), "p50_ms": float(p50), "p95_ms": float(p95)}
tests = list(ET.parse(args.junit).iter("testcase"))
outcomes = {}
groups = {}
for case in corpus["cases"]:
    selected = [test for test in tests if test.get("name") ==
                f"test_corpus_signed_emergency_is_independent[{case['id']}]"]
    result = "missing" if len(selected) != 1 else next(
        (tag for tag in ("failure", "error", "skipped") if selected[0].find(tag) is not None), "passed")
    outcomes[case["id"]] = result
    groups.setdefault(case["group"], Counter())[result] += 1
mandatory = ["test_signed_chain_negative_controls[" + name + "]" for name in
             ("urgent-reviewed_urgent", "corrected-lower_urgency_unauthorized", "unknown-validated_candidates",
              "missing-evaluation_unavailable", "conflict-contradictory", "tampered-evaluation_unavailable")]
mandatory += ["test_metric_mutation_and_partial_results", "test_invalid_approval_and_annotation", "test_ai06_benchmark"]
counts = Counter(t.get("name") for t in tests)
coverage = json.loads(Path(__file__).with_name("ai06_coverage.json").read_text(encoding="utf-8"))
required = {f"{group}-{i:02}" for group, size in
            {"A": 4, "B": 5, "E": 4, "D": 5, "R": 5, "P": 4, "W": 11}.items()
            for i in range(1, size + 1)}
if len(coverage) != len(required) or {row["id"] for row in coverage} != required:
    raise SystemExit("Mandatory coverage manifest IDs mismatch")
browser_root = ET.parse(args.browser_xml)
indexed = {"backend": {}, "chromium": {}}
for suite, entries in (("backend", tests), ("chromium", browser_root.iter("testcase"))):
    for item in entries:
        file = item.get("classname", "")
        if suite == "backend":
            file = file.split(".")[-1] + ".py"
        indexed[suite].setdefault(f"{file}::{item.get('name')}", []).append(item)

coverage_status = {}
for row in coverage:
    if set(row) != {"id", "category", "boundary", "test", "invariant", "expected", "suite", "doubles"}:
        raise SystemExit("Invalid coverage manifest entry")
    entries = indexed[row["suite"]].get(row["test"], [])
    state = "NOT_TESTED"
    if len(entries) == 1 and entries[0].find("skipped") is None:
        state = "TESTED_AND_FAILED" if any(entries[0].find(tag) is not None
                                          for tag in ("failure", "error")) else "TESTED_AND_PASSED"
    coverage_status[row["id"]] = {**row, "status": state}
uncovered = [key for key, row in coverage_status.items() if row["status"] != "TESTED_AND_PASSED"]
series = {"corpus_parse", "classifier_validate", "evidence_validate", "state_transition", "signed_rules",
          "precedence", "signed_render", "full_chain"}
summary = {
    "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
    "python": platform.python_version(),
    "node": subprocess.check_output(["node", "--version"], text=True).strip(),
    "corpus_id": "roghyar-ai06-adversarial",
    "benchmarks": benchmarks,
    "coverage": coverage_status,
    "uncovered": uncovered,
    "external_status": "BLOCKED_EXTERNAL",
    "platform": platform.platform(),
    "missing_mandatory": [name for name in mandatory if counts[name] != 1],
    "corpus_version": corpus["version"],
    "corpus_sha256": hashlib.sha256(raw).hexdigest(),
    "sample_count": len(outcomes),
    "groups": groups,
    "outcomes": outcomes,
    "backend_total": len(tests),
    "backend_failed": sum(t.find("failure") is not None or t.find("error") is not None for t in tests),
    "backend_skipped": sum(t.find("skipped") is not None for t in tests),
    "frontend_passed": args.frontend,
    "chromium_passed": args.chromium,
    "tier_a": "passed" if outcomes and all(r == "passed" for r in outcomes.values()) else "failed",
    "tier_b": "NOT_EVALUATED ? APPROVAL_OR_DATASET_MISSING",
    "clinical_gate": "OPEN",
    "production_gate": "CLOSED",
    "untested": ["native speech quality", "clinical sensitivity", "atomic multi-worker state custody"],
}
passed = not (summary["tier_a"] != "passed" or summary["backend_failed"] or summary["backend_skipped"]
              or summary["missing_mandatory"] or uncovered or not args.frontend or not args.chromium
              or summary["commit"] != os.environ.get("VERIFIED_HEAD") or set(benchmarks) != series
              or os.environ.get("REGRESSION_STATUS") != "success")
summary["engineering_gate"] = "PASS" if passed else "OPEN"
args.output.mkdir(parents=True, exist_ok=True)
(args.output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
lines = ["# AI-06 verification", "", f"Commit: {summary['commit']}", "",
         f"Python {summary['python']}, Node {summary['node']}", "",
         f"Corpus v{corpus['version']}: {summary['corpus_sha256']}", "",
         f"Tier A: {summary['tier_a']}. Tier B: {summary['tier_b']}.", "",
         f"Backend: {len(tests)} cases. Frontend: {args.frontend}. Chromium: {args.chromium}.", "",
         "| Group | Outcomes |", "| --- | --- |"]
lines += [f"| {group} | {dict(counts)} |" for group, counts in groups.items()]
lines += ["", f"Gate A: {summary['engineering_gate']}", "", "| Requirement | Status |", "| --- | --- |"]
lines += [f"| {key} | {entry['status']} |" for key, entry in coverage_status.items()]
lines += ["", "Failures or missing cases: " + (", ".join(k for k, v in outcomes.items() if v != "passed") or "none")]
(args.output / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
if not passed:
    raise SystemExit(1)
