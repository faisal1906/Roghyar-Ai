"""Emit sanitized engineering evidence from pytest JUnit. No model calls."""
import argparse
import hashlib
import json
import platform
import subprocess
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("junit", type=Path)
parser.add_argument("output", type=Path)
parser.add_argument("--frontend", type=int)
parser.add_argument("--chromium", type=int)
args = parser.parse_args()
raw = Path(__file__).with_name("ai06_corpus.json").read_bytes()
corpus = json.loads(raw.decode("utf-8-sig"))
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
summary = {
    "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
    "python": platform.python_version(),
    "node": subprocess.check_output(["node", "--version"], text=True).strip(),
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
    "tier_b": "blocked: approved provider and representative reviewed dataset required",
    "untested": ["native speech quality", "clinical sensitivity", "atomic multi-worker state custody"],
}
args.output.mkdir(parents=True, exist_ok=True)
(args.output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
lines = ["# AI-06 verification", "", f"Commit: {summary['commit']}", "",
         f"Python {summary['python']}, Node {summary['node']}", "",
         f"Corpus v{corpus['version']}: {summary['corpus_sha256']}", "",
         f"Tier A: {summary['tier_a']}. Tier B: {summary['tier_b']}.", "",
         f"Backend: {len(tests)} cases. Frontend: {args.frontend}. Chromium: {args.chromium}.", "",
         "| Group | Outcomes |", "| --- | --- |"]
lines += [f"| {group} | {dict(counts)} |" for group, counts in groups.items()]
lines += ["", "Failures or missing cases: " + (", ".join(k for k, v in outcomes.items() if v != "passed") or "none")]
(args.output / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
if summary["tier_a"] != "passed" or summary["backend_failed"] or summary["backend_skipped"]:
    raise SystemExit(1)
