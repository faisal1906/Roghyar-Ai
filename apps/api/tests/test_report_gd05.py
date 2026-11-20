"""Exercise the reporter against disposable JUnit inputs, without a provider."""
import json
import os
from pathlib import Path
import runpy
import subprocess
import xml.etree.ElementTree as ET

import pytest

ROOT = Path(__file__).parent


@pytest.mark.parametrize("case,exit_code", [
    ("valid", 0), ("missing_id", 1), ("duplicate_id", 1), ("duplicate_mapping", 1),
    ("wrong_expected", 1), ("missing_case", 1), ("skipped", 1), ("failure", 1),
    ("postgres_missing", 1), ("publishing_missing", 1), ("sha", 1), ("frontend", 1),
    ("edited_gaps", 0),
    ("changed_missing", 1), ("transplant_missing", 1), ("transplant_skipped", 1),
    ("transplant_failed", 1), ("transplant_duplicate", 1), ("transplant_renamed", 1),
    ("screening_missing", 1), ("screening_skipped", 1), ("screening_failed", 1),
    ("screening_duplicate", 1), ("screening_renamed", 1), ("screening_wrong_expected", 1),
    ("ingredient_missing", 1), ("exact_set_missing", 1), ("screening_metadata_tampered", 1),
    ("regression_failure", 1),
])
def test_reporter_evidence_requirements(case, exit_code, tmp_path, monkeypatch):
    manifest = json.loads((ROOT / "gd05_coverage.json").read_text(encoding="utf-8"))
    rows = manifest["requirements"]
    key = "GD05-CASE-lower:signature"
    transplant = "GD05-CASE-transplant:age:event"
    screening = "GD05-SCREENING-canonical"
    benchmark_names = ("stage", "review", "ready", "activate", "replace", "rollback", "revoke", "post_revoke")
    for suite in ("backend", "browser"):
        tree = ET.Element("testsuite")
        for identity, (target, test, _) in rows.items():
            if target == suite:
                if (case, identity) in (
                    ("missing_case", key), ("postgres_missing", "GD05-CHAIN-POSTGRES"),
                    ("publishing_missing", "GD04-WRITER"),
                    ("changed_missing", "GD05-CASE-changed:age:denied"),
                    ("transplant_missing", transplant), ("screening_missing", screening),
                    ("ingredient_missing", "GD05-SCREENING-ingredient_signature"),
                    ("exact_set_missing", "GD05-SCREENING-two_current"),
                    ("screening_metadata_tampered", "GD05-SCREENING-pairwise_only"),
                ):
                    continue
                node = ET.SubElement(tree, "testcase", name=test)
                if identity == transplant:
                    if case == "transplant_skipped":
                        ET.SubElement(node, "skipped")
                    if case == "transplant_failed":
                        ET.SubElement(node, "failure")
                    if case == "transplant_duplicate":
                        ET.SubElement(tree, "testcase", name=test)
                    if case == "transplant_renamed":
                        node.set("name", test + "-renamed")
                if identity == screening:
                    if case == "screening_skipped":
                        ET.SubElement(node, "skipped")
                    if case == "screening_failed":
                        ET.SubElement(node, "failure")
                    if case == "screening_duplicate":
                        ET.SubElement(tree, "testcase", name=test)
                    if case == "screening_renamed":
                        node.set("name", test + "-renamed")
                if identity == key and case in ("skipped", "failure"):
                    ET.SubElement(node, case)
        if suite == "backend":
            for regression in ("changed_missing-1", "transplant_missing-1", "transplant_skipped-1",
                               "transplant_failed-1", "transplant_duplicate-1", "transplant_renamed-1",
                               "missing_id-1", "edited_gaps-0"):
                ET.SubElement(tree, "testcase", name=f"test_reporter_evidence_requirements[{regression}]")
            for regression in ("screening_missing", "screening_skipped", "screening_failed", "screening_duplicate",
                               "screening_renamed", "screening_wrong_expected", "ingredient_missing",
                               "exact_set_missing", "screening_metadata_tampered", "regression_failure"):
                ET.SubElement(tree, "testcase", name=f"test_reporter_evidence_requirements[{regression}-1]")
        ET.ElementTree(tree).write(tmp_path / f"phase3-{suite}.xml")
    if case == "missing_id":
        rows.pop(key)
    if case == "screening_wrong_expected":
        rows[screening][2] = "Invented"
    if case == "wrong_expected":
        rows[key][2] = "Invented"
    if case == "duplicate_mapping":
        rows["GD01"] = rows["GD02"]
    if case == "edited_gaps":
        manifest["uncovered"].clear()
    if case == "screening_metadata_tampered":
        manifest["uncovered"].clear()
    raw = json.dumps(manifest)
    if case == "duplicate_id":
        raw = raw.replace('"version": 3', '"version": 3, "version": 3', 1)
    (tmp_path / "gd05_coverage.json").write_text(raw, encoding="utf-8")
    (tmp_path / "phase2-backend.log").write_text("".join(
        f"GD-05 publication {name} n=1 elapsed_ms=1.0\n" for name in benchmark_names), encoding="utf-8")
    (tmp_path / "phase2-unit.log").write_text("" if case == "frontend" else "# pass 12\n", encoding="utf-8")
    reporter = tmp_path / "report_gd05.py"
    reporter.write_bytes((ROOT / reporter.name).read_bytes())
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    monkeypatch.setenv("REGRESSION_STATUS", "failure" if case == "regression_failure" else "success")
    monkeypatch.setenv("VERIFIED_HEAD", "b"*40 if case == "sha" else "a"*40)
    monkeypatch.setattr(subprocess, "check_output", lambda *args, **kwargs: "a"*40)
    if case == "duplicate_id":
        with pytest.raises(ValueError, match="Duplicate"):
            runpy.run_path(str(reporter))
        return
    with pytest.raises(SystemExit) as result:
        runpy.run_path(str(reporter))
    if exit_code:
        assert result.value.code != 0
    else:
        assert result.value.code == 0
        report = json.loads((tmp_path / "phase4-acceptance.json").read_text())
        assert report["publication"] == report["provenance"] == report["screening"] == "PASS"
        assert "GD05-PROVENANCE-INVENTORY" not in report["uncovered"]
        assert report["gd05"] == report["phase4"] == "OPEN"
        assert report["clinical"] == report["physical_device"] == "EXTERNAL_VALIDATION_REQUIRED"
        assert report["production"] == "CLOSED" and report["real_model"] == "NOT_EVALUATED"


def test_gate_calculation_accepts_complete_engineering_evidence_only():
    import ast
    tree = ast.parse((ROOT / "report_gd05.py").read_text(encoding="utf-8"))
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                    and node.name == "engineering_gates")
    namespace = {}
    exec(compile(ast.Module(body=[function], type_ignores=[]), "<gate calculation>", "exec"), namespace)
    gates = namespace["engineering_gates"]
    result = gates({}, True, True)
    assert result == {
        "publication": "PASS", "gd05": "PASS", "phase4": "PASS",
        "clinical": "EXTERNAL_VALIDATION_REQUIRED", "physical_device": "EXTERNAL_VALIDATION_REQUIRED",
        "real_model": "NOT_EVALUATED", "production": "CLOSED",
    }
    assert gates({"case": "NOT_TESTED"}, True, True)["phase4"] == "OPEN"
    assert gates({}, False, True)["gd05"] == "OPEN"
    assert gates({}, True, False)["phase4"] == "OPEN"
