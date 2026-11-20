"""coverage.model (Cross-team Testing Interface, Phase 5): our design requirements evaluated by the
suite, reported only when the tester tests our own system."""
import json
import re
from pathlib import Path

import pytest

from fake_systems import make_reference_system
from spec_checks import assert_measure, assert_spec_report
from test_runner.client import WSGIClient
from test_runner.session import setup_target
from test_runner.suite import (CRITERIA, DESIGN_REQUIREMENTS, OWN_SYSTEM_ENV, aborted_report, compute_model_coverage,
                               is_own_system, run_suite)

DEPLOYED = "https://team22.13-58-35-224.sslip.io/system"
TEAM24 = "https://30861-p3.jscarfo.com"
MAPPING_DOC = Path(__file__).parent.parent / "docs" / "DESIGN_REQUIREMENTS_MAPPING.md"
DESIGN = json.loads(DESIGN_REQUIREMENTS.read_text(encoding="utf-8"))


def run(target, *defects, **kwargs):
    client = WSGIClient(make_reference_system(*defects))
    setup_target(client, "run-model")
    return run_suite(client, session_id="run-model", target=target, poll_timeout=0, poll_interval=0, **kwargs)


def result(test_id, outcome):
    return {"test_id": test_id, "outcome": outcome}


# Which targets are ours

@pytest.mark.parametrize("target", [DEPLOYED, DEPLOYED + "/", "HTTPS://Team22.13-58-35-224.sslip.io:443/system",
                                    "http://localhost:5000", "http://127.0.0.1:8000/system", "http://[::1]:5000"])
def test_our_systems_are_recognized(target):
    assert is_own_system(target)


@pytest.mark.parametrize("target", [TEAM24, "https://team22.13-58-35-224.sslip.io/tester",
                                    "https://team22.13-58-35-224.sslip.io", "http://reference.example",
                                    "in-process", "", None])
def test_other_targets_are_not_ours(target):
    assert not is_own_system(target)


def test_extra_own_urls_come_from_the_environment(monkeypatch):
    monkeypatch.setenv(OWN_SYSTEM_ENV, " https://staging.example/system , https://other.example ")
    assert is_own_system("https://staging.example/system/")
    assert is_own_system("https://other.example")
    assert not is_own_system(TEAM24)


# In the report

def test_a_report_on_our_system_includes_model_coverage():
    report = run(DEPLOYED)
    assert_spec_report(report, [c.id for c in CRITERIA])
    model = report["coverage"]["model"]
    assert (model["covered"], model["total"]) == (8, 8)
    assert "Phase 4 report Q1.1" in model["basis"]
    assert DESIGN["mapping_url"] in model["basis"]
    assert "all evaluated" in model["basis"]


def test_a_cross_team_report_leaves_model_coverage_out():
    report = run(TEAM24)
    assert "model" not in report["coverage"]
    assert set(report["coverage"]) == {"code", "component", "system"}


def test_the_caller_can_say_whose_system_it_is():
    assert "model" in run("in-process", own_system=True)["coverage"]
    assert "model" not in run(DEPLOYED, own_system=False)["coverage"]


def test_failed_tests_count_as_evaluated():
    # Coverage records what ran; whether the system behaved correctly is in results
    report = run(DEPLOYED, "alter_content")
    assert any(r["outcome"] == "fail" for r in report["results"])
    assert report["coverage"]["model"]["covered"] == 8


def test_a_partial_run_counts_only_the_requirements_its_tests_reach():
    model = run(DEPLOYED, only={"AC-01"})["coverage"]["model"]
    # AC-01 demonstrates REQ-01 and REQ-04
    assert (model["covered"], model["total"]) == (2, 8)
    assert "not evaluated: REQ-02, REQ-03, REQ-05, REQ-06, REQ-07, REQ-08" in model["basis"]


def test_skipped_and_errored_tests_do_not_count():
    model = compute_model_coverage([result("AC-05", "skip"), result("AC-06", "error"), result("AC-07", "pass")])
    # AC-07 demonstrates REQ-04, REQ-05 and REQ-08
    assert model["covered"] == 3


def test_an_unreadable_mapping_gives_null_counts_with_a_reason(tmp_path):
    bad = tmp_path / "design_requirements.json"
    bad.write_text("{not json")
    model = compute_model_coverage([result("AC-01", "pass")], bad)
    assert (model["covered"], model["total"]) == (None, None)
    assert_measure("model", model)
    model = run(DEPLOYED, design_path=tmp_path / "absent.json")["coverage"]["model"]
    assert model["covered"] is None and "could not load" in model["basis"]


def test_an_aborted_report_on_our_system_still_has_a_model_entry():
    ours = aborted_report("run-1", DEPLOYED, "boom")
    theirs = aborted_report("run-2", TEAM24, "boom")
    assert ours["coverage"]["model"]["covered"] is None and ours["coverage"]["model"]["basis"]
    assert "model" not in theirs["coverage"]
    assert_spec_report(ours)


# The mapping itself

def test_the_mapping_lists_req_01_to_req_08_with_real_suite_tests():
    ids = [r["id"] for r in DESIGN["requirements"]]
    assert ids == [f"REQ-0{n}" for n in range(1, 9)]
    suite_tests = {c.id for c in CRITERIA}
    for requirement in DESIGN["requirements"]:
        assert requirement["tests"], requirement["id"]
        assert set(requirement["tests"]) <= suite_tests, requirement


def test_the_mapping_doc_matches_the_mapping_the_tester_reads():
    rows = re.findall(r"^\| (REQ-\d+) \| ([^|]+) \| ([^|]+) \|$", MAPPING_DOC.read_text(encoding="utf-8"), re.M)
    doc = {req: (name.strip(), [t.strip() for t in tests.split(",")]) for req, name, tests in rows}
    assert doc == {r["id"]: (r["title"], r["tests"]) for r in DESIGN["requirements"]}
    assert DESIGN["mapping_url"].endswith("docs/DESIGN_REQUIREMENTS_MAPPING.md")
