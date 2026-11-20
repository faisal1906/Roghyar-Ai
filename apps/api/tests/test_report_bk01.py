from copy import deepcopy
import xml.etree.ElementTree as ET

import pytest

from report_bk01 import CLASS, MIGRATIONS, REQUIRED, acceptance

SHA = "a" * 40


@pytest.fixture
def evidence():
    root = ET.Element("testsuite")
    for group in REQUIRED.values():
        for name in group:
            ET.SubElement(root, "testcase", classname=CLASS, name=name)
    prior = {"commit": SHA, "exact_head": True, "phase5_synthetic_engineering": "PASS",
             "phase5_real_world_readiness": "OPEN", "phase4_preserved": True, "booking": "CLOSED",
             "backend_total": len(root), "frontend_unit_total": 12, "chromium_total": 49}
    return root, prior


@pytest.mark.parametrize("fault", ["none", "missing", "wrong_identity", "renamed", "skip", "fail",
                                   "duplicate", "migration", "sha", "pipeline", "phase5", "totals"])
def test_bk01_acceptance_evidence(fault, evidence):
    root, prior = evidence
    target = next(row for row in root if row.get("name") == "test_bk01_parallel_resends_use_shared_redis")
    if fault == "missing":
        root.remove(target)
    if fault == "wrong_identity":
        target.set("classname", "unrelated")
    if fault == "renamed":
        target.set("name", "test_other")
    if fault in {"skip", "fail"}:
        ET.SubElement(target, "skipped" if fault == "skip" else "failure")
    if fault == "duplicate":
        root.append(deepcopy(target))
    prior["backend_total"] = len(root)
    if fault == "phase5":
        prior["phase5_synthetic_engineering"] = "OPEN"
    report = acceptance(ET.tostring(root), f"=== {len(root) + (fault == 'totals')} passed in 1s ===",
                        "" if fault == "migration" else "\n".join(MIGRATIONS), prior, SHA,
                        "b" * 40 if fault == "sha" else SHA, "failure" if fault == "pipeline" else "success")
    assert report["engineering"] == ("PASS" if fault == "none" else "OPEN")
