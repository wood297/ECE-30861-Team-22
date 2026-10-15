"""Tests of the acceptance suite itself: it must pass a correct system and catch broken ones.

Without these, a criterion that can never fail (or always fails) would go unnoticed.
"""
import pytest

from fake_systems import CODE_COVERAGE, make_reference_system
from spec_checks import assert_spec_report
from test_runner.client import WSGIClient
from test_runner.session import setup_target
from test_runner.suite import CRITERIA, run_suite

SESSION = "run-123"


def run_against(*defects, **kwargs):
    client = WSGIClient(make_reference_system(*defects))
    if "no_test_endpoints" not in defects:
        setup_target(client, SESSION)
    return run_suite(client, session_id=SESSION, target="http://reference.example",
                     poll_timeout=0, poll_interval=0, **kwargs)


def outcomes(*defects):
    return {r["test_id"]: r["outcome"] for r in run_against(*defects)["results"]}


@pytest.mark.parametrize("variant", [(), ("zip_only",)], ids=["inline-artifacts", "zip-download"])
def test_reference_system_passes_every_criterion(variant):
    report = run_against(*variant)

    assert_spec_report(report, [c.id for c in CRITERIA])
    results = {r["test_id"]: r["outcome"] for r in report["results"]}
    assert results.pop("AC-05") == "skip"
    assert set(results.values()) == {"pass"}, results
    assert report["passed"] is True


@pytest.mark.parametrize("defect, criterion_id", [
    ("lax_validation", "API-03"),
    ("leak_release", "AC-01"),
    ("always_completed", "AC-03"),
    ("accept_empty", "AC-04"),
    ("alter_content", "AC-06"),
    ("drop_missing", "AC-03"),
    ("drop_missing", "AC-08"),
])
def test_suite_detects_defect(defect, criterion_id):
    assert outcomes(defect)[criterion_id] == "fail"


def test_failure_messages_name_the_failed_check():
    result = next(r for r in run_against("alter_content")["results"] if r["test_id"] == "AC-06")

    assert "content is preserved exactly" in result["message"]


def test_internal_server_error_is_an_error_from_the_system():
    report = run_against("crash_on_bad_input")

    result = next(r for r in report["results"] if r["test_id"] == "API-03")
    assert (result["outcome"], result["source"]) == ("error", "system")
    assert "HTTP 500" in result["message"]
    assert_spec_report(report)


def test_unreachable_target_is_a_communication_error_not_a_pass():
    class Unreachable:
        def request(self, *args, **kwargs):
            raise OSError("connection refused")

    report = run_suite(Unreachable(), session_id=SESSION, target="http://down.example",
                       poll_timeout=0, only={"AC-02", "API-04"})

    assert {(r["outcome"], r["source"]) for r in report["results"]} == {("error", "communication")}
    assert report["passed"] is False
    assert report["coverage"]["code"]["covered"] is None
    assert_spec_report(report)


def test_exhausted_time_budget_stops_calling_the_target():
    report = run_against(time_budget=0)

    assert {r["outcome"] for r in report["results"]} == {"error", "skip"}
    assert all(r["source"] == "communication" for r in report["results"] if r["outcome"] == "error")


def test_coverage_counts_executed_tests_and_reads_code_coverage_from_the_system():
    coverage = run_against()["coverage"]

    # AC-05 is skipped, so its criterion and its only component are not covered
    assert (coverage["system"]["covered"], coverage["system"]["total"]) == (7, 8)
    assert (coverage["component"]["covered"], coverage["component"]["total"]) == (6, 7)
    assert coverage["code"] == CODE_COVERAGE


def test_coverage_counts_failed_tests_as_covered():
    # Coverage records what was exercised, not whether the system behaved correctly
    assert run_against("alter_content")["coverage"]["system"]["covered"] == 7


def test_code_coverage_is_null_with_a_reason_when_the_system_has_no_report():
    code = run_against("no_test_endpoints")["coverage"]["code"]

    assert (code["covered"], code["total"]) == (None, None)
    assert "HTTP 404" in code["basis"]


def test_missing_tester_fixtures_abort_the_suite(tmp_path):
    report = run_against(fixtures_root=tmp_path / "missing")

    by_id = {r["test_id"]: r for r in report["results"]}
    assert report["aborted"] is True and report["passed"] is False
    # AC-01 is the first test to read fixture files: it is prevented, everything after is skipped
    assert (by_id["AC-01"]["outcome"], by_id["AC-01"]["source"]) == ("error", "tester")
    ids = [c.id for c in CRITERIA]
    assert {by_id[i]["outcome"] for i in ids[ids.index("AC-01") + 1:]} == {"skip"}
    assert by_id["API-01"]["outcome"] == "pass"
    assert_spec_report(report, ids)


def test_unreadable_oracle_aborts_before_any_test(tmp_path):
    report = run_against(oracle_path=tmp_path / "missing.json")

    assert report["aborted"] is True
    assert {r["outcome"] for r in report["results"]} == {"skip"}
    assert report["coverage"]["system"]["covered"] is None
    assert_spec_report(report)


def test_project_requests_carry_the_session_id():
    seen = []

    class Recording(WSGIClient):
        def request(self, method, path, json_body=None, **kwargs):
            if method == "POST" and path == "/bundles":
                seen.append(json_body)
            return super().request(method, path, json_body=json_body, **kwargs)

    client = Recording(make_reference_system())
    setup_target(client, SESSION)
    run_suite(client, session_id=SESSION, poll_timeout=0, poll_interval=0, only={"AC-02"})

    assert seen and all(body["session_id"] == SESSION for body in seen)
