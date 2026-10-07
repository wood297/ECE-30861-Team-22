"""Runs the cross-team acceptance suite (test_runner/suite.py) against this service in-process.

This is the same suite the tester service runs over HTTP, so a regression here is
what another team would see when testing our deployment.
"""
import pytest

from app import app, bundles
from test_runner.client import WSGIClient
from test_runner.suite import CRITERIA, run_suite

# Criteria that fail today because the functionality is not built yet. Non-strict,
# so once fixed they show up as XPASS: delete the entry when that happens.
KNOWN_GAPS = {
    "API-03": "POST /bundles does not yet reject empty or non-string identifiers (spec 5.2)",
    "API-06": "Bundle generation (Person 3) is not implemented; status stays PROCESSING",
    "AC-02": "Bundle generation (Person 3) is not implemented; status never becomes COMPLETED",
}


@pytest.fixture(scope="module")
def report():
    bundles.clear()
    # Processing is synchronous here, so there is nothing to wait for
    return run_suite(WSGIClient(app), target="in-process", poll_timeout=0, poll_interval=0)


@pytest.mark.parametrize("criterion_id", [c.id for c in CRITERIA])
def test_acceptance_criterion(report, criterion_id, request):
    if criterion_id in KNOWN_GAPS:
        request.applymarker(pytest.mark.xfail(reason=KNOWN_GAPS[criterion_id]))
    result = next(r for r in report["results"] if r["id"] == criterion_id)
    if result["outcome"] == "skipped":
        pytest.skip(result["reason"])

    failures = [f"{c['description']} ({c['detail']})" for c in result["checks"] if c["passed"] is False]
    assert result["outcome"] == "passed", result["reason"] or "\n".join(failures)
