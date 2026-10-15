"""Checks that a tester report is well formed under the Cross-team Testing Interface."""
from test_runner.suite import OUTCOMES

SOURCES = {"tester", "system", "communication", "unknown"}


def assert_measure(name, entry):
    assert isinstance(entry, dict), name
    assert isinstance(entry.get("basis"), str) and entry["basis"].strip(), f"{name}: basis must be non-empty"
    covered, total = entry["covered"], entry["total"]
    if covered is None or total is None:
        assert covered is None and total is None, f"{name}: use null for both counts"
    else:
        assert isinstance(covered, int) and isinstance(total, int), name
        assert 0 <= covered <= total, f"{name}: {covered}/{total}"


def assert_spec_report(report, planned_ids=None):
    assert isinstance(report.get("session_id"), str) and report["session_id"]
    assert isinstance(report.get("target_url"), str) and report["target_url"]

    ids = [r["test_id"] for r in report["results"]]
    assert all(isinstance(i, str) and i for i in ids), "every test_id must be non-empty"
    assert len(ids) == len(set(ids)), "each planned test appears exactly once"
    if planned_ids is not None:
        assert ids == list(planned_ids)
    for result in report["results"]:
        assert result["outcome"] in OUTCOMES, result
        assert isinstance(result["message"], str) and result["message"].strip(), result
        if result["outcome"] == "error":
            assert result.get("source") in SOURCES, result

    coverage = report["coverage"]
    for kind in ("code", "component", "system"):
        assert_measure(kind, coverage[kind])


def assert_error_body(body, source=None):
    assert isinstance(body.get("message"), str) and body["message"].strip()
    assert body.get("source") in SOURCES
    if source is not None:
        assert body["source"] == source
