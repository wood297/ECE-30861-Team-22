"""Tests of the acceptance suite itself: it must pass a correct system and catch broken ones.

Without these, a criterion that can never fail (or always fails) would go unnoticed.
"""
import pytest

from fake_systems import make_reference_system
from test_runner.client import WSGIClient
from test_runner.suite import run_suite


def outcomes(*defects):
    report = run_suite(WSGIClient(make_reference_system(*defects)), poll_timeout=0, poll_interval=0)
    return {r["id"]: r["outcome"] for r in report["results"]}


@pytest.mark.parametrize("variant", [(), ("zip_only",)], ids=["inline-artifacts", "zip-download"])
def test_reference_system_passes_every_criterion(variant):
    results = outcomes(*variant)

    assert results.pop("AC-05") == "skipped"
    assert set(results.values()) == {"passed"}, results


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
    assert outcomes(defect)[criterion_id] == "failed"


def test_unreachable_target_is_reported_as_error_not_pass():
    class Unreachable:
        def request(self, *args, **kwargs):
            raise OSError("connection refused")

    report = run_suite(Unreachable(), poll_timeout=0, only={"AC-02", "API-04"})

    assert {r["outcome"] for r in report["results"]} == {"error"}
    assert report["passed"] is False
