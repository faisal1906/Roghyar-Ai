import pytest

from report_phase5 import CATALOG, RELEASE, acceptance


@pytest.mark.parametrize("case", [
    "valid", "missing", "stale", "no_case", "skipped", "extra_case", "opened_gate", "unverified_provider",
    "loc06_open", "prior_phase4", "wrong_head", "pipeline",
])
def test_phase5_enforces_same_head_and_independent_release_gates(case):
    sha = "a" * 40
    artifacts = {ticket: {"commit": sha, "exact_head": True, "engineering": "PASS",
                           "coverage": {key: {"outcome": "PASS"} for key in keys}} | RELEASE[ticket]
                 for ticket, keys in CATALOG.items()}
    phase4 = {"commit": sha, "phase4": "PASS", "production": "CLOSED"}
    sample = sorted(CATALOG["loc06"])[0]
    if case == "missing":
        artifacts.pop("loc02")
    if case == "stale":
        artifacts["loc03"]["commit"] = "b" * 40
    if case == "no_case":
        artifacts["loc06"]["coverage"].pop(sample)
    if case == "skipped":
        artifacts["loc06"]["coverage"][sample]["outcome"] = "SKIP"
    if case == "extra_case":
        artifacts["loc06"]["coverage"]["fabricated"] = {"outcome": "PASS"}
    if case == "opened_gate":
        artifacts["loc05"]["real_booking"] = "PASS"
    if case == "unverified_provider":
        artifacts["loc06"]["provider_onboarding"] = "PASS"
    if case == "loc06_open":
        artifacts["loc06"]["engineering"] = "OPEN"
    if case == "prior_phase4":
        phase4["production"] = "OPEN"
    result = acceptance(artifacts, phase4, sha, "b" * 40 if case == "wrong_head" else sha,
                        "failure" if case == "pipeline" else "success")
    assert result["phase5_synthetic_engineering"] == ("PASS" if case == "valid" else "OPEN")
    assert result["phase5_real_world_readiness"] == "OPEN"
    assert result["booking"] == result["public_directions"] == "CLOSED"
