"""Tests for the /test/* endpoints of both roles and the CLI runner.

Covers this service as the validated system (selftest.py), and the tester service
(test_runner/service.py) that runs the suite against a target, following the
course's Cross-team Testing Interface.
"""
import urllib.error
import uuid

import pytest
from flask import Flask, jsonify

import artifact_pipeline
import selftest
from app import app, bundles
from fake_systems import make_reference_system
from spec_checks import assert_error_body, assert_spec_report
from test_runner import service
from test_runner.__main__ import main as cli_main
from test_runner.client import WSGIClient
from test_runner.suite import CRITERIA, run_suite

TARGET = "http://system-under-test.example"


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    bundles.clear()
    selftest._sessions.clear()
    selftest._last_report["report"] = None
    service._sessions.clear()
    # /test/selftest sets this for its pytest subprocess; tests below control it explicitly
    monkeypatch.delenv(selftest.SELFTEST_ENV, raising=False)
    monkeypatch.setenv(selftest.SELFTEST_ENABLE_ENV, "1")


@pytest.fixture
def system():
    return app.test_client()


def route_tester_to(monkeypatch, target_app):
    monkeypatch.setattr(service, "make_client", lambda url, token=None: WSGIClient(target_app))
    return service.app.test_client()


@pytest.fixture
def tester(monkeypatch):
    """Tester service whose HTTP client is routed to our app in-process."""
    return route_tester_to(monkeypatch, app)


def new_session(tester):
    response = tester.post("/test/setup", json={"target_url": TARGET})
    assert response.status_code == 201, response.get_json()
    return response.get_json()["session_id"]


# --- Validated system (this service) ----------------------------------------

def test_system_setup_returns_the_session_id(system):
    response = system.post("/test/setup", json={"session_id": "run-123", "setup": {}})

    assert response.status_code == 201
    assert response.get_json()["session_id"] == "run-123"
    for release in ("v1.2", "v1.3", "v1.4"):
        assert {"product_id": "prd-88", "release_version": release} in response.get_json()["fixtures"]


def test_system_setup_does_not_disturb_existing_bundles(system):
    system.post("/bundles", json={"product_id": "prd-88", "release_version": "v1.2"})

    system.post("/test/setup", json={"session_id": "run-123", "setup": {}})

    assert len(bundles) == 1


def test_system_setup_rejects_a_reused_session_id(system):
    system.post("/test/setup", json={"session_id": "run-123", "setup": {}})

    response = system.post("/test/setup", json={"session_id": "run-123", "setup": {}})

    assert response.status_code == 409
    assert_error_body(response.get_json(), "system")


@pytest.mark.parametrize("body", [None, {}, {"session_id": ""}, {"session_id": 5},
                                  {"session_id": "run-1", "setup": []}])
def test_system_setup_rejects_invalid_requests(system, body):
    response = system.post("/test/setup", json=body) if body is not None else system.post("/test/setup")

    assert response.status_code == 400
    assert_error_body(response.get_json(), "system")


def test_system_setup_reports_a_preparation_failure(system, monkeypatch, tmp_path):
    monkeypatch.setattr(artifact_pipeline, "FIXTURES_ROOT", tmp_path / "missing")

    response = system.post("/test/setup", json={"session_id": "run-123", "setup": {}})

    assert response.status_code == 500
    assert_error_body(response.get_json(), "system")


def test_system_report_returns_code_coverage_for_the_session(system):
    system.post("/test/setup", json={"session_id": "run-123", "setup": {}})

    response = system.get("/test/report", query_string={"session_id": "run-123"})

    assert response.status_code == 200
    body = response.get_json()
    assert body["session_id"] == "run-123"
    assert body["coverage"]["code"]["covered"] is None and body["coverage"]["code"]["total"] is None
    assert body["coverage"]["code"]["basis"]


def test_system_report_errors(system):
    assert system.get("/test/report").status_code == 400
    response = system.get("/test/report", query_string={"session_id": "never-set-up"})
    assert response.status_code == 404
    assert_error_body(response.get_json(), "system")


def test_system_selftest_is_off_unless_enabled(system, monkeypatch):
    monkeypatch.delenv(selftest.SELFTEST_ENABLE_ENV)
    monkeypatch.setattr(selftest, "run_internal_tests", lambda: pytest.fail("self-test ran while disabled"))

    for response in (system.post("/test/selftest"), system.get("/test/selftest")):
        assert response.status_code == 404
        assert_error_body(response.get_json(), "system")


def test_system_selftest_report_is_404_before_any_run(system):
    assert system.get("/test/selftest").status_code == 404


def test_system_selftest_stores_report_for_later_retrieval(system, monkeypatch):
    fake_report = {"run_id": "r1", "passed": True, "summary": {"total": 0}, "results": []}
    monkeypatch.setattr(selftest, "run_internal_tests", lambda: fake_report)

    run = system.post("/test/selftest")
    report = system.get("/test/selftest")

    assert run.status_code == 200
    assert run.get_json() == fake_report
    assert report.get_json() == fake_report


def test_system_selftest_refuses_to_recurse(system, monkeypatch):
    monkeypatch.setenv(selftest.SELFTEST_ENV, "1")

    assert system.post("/test/selftest").status_code == 409


def test_run_internal_tests_reports_each_outcome(tmp_path):
    (tmp_path / "test_sample.py").write_text(
        "import pytest\n"
        "def test_ok(): pass\n"
        "def test_broken(): assert 1 == 2\n"
        "@pytest.mark.skip(reason='not today')\n"
        "def test_skipped(): pass\n"
        "@pytest.mark.xfail(reason='known gap')\n"
        "def test_known_gap(): assert False\n"
    )

    report = selftest.run_internal_tests(tmp_path)

    outcomes = {r["id"].split("::")[-1]: r["outcome"] for r in report["results"]}
    assert outcomes == {"test_ok": "passed", "test_broken": "failed",
                        "test_skipped": "skipped", "test_known_gap": "xfailed"}
    assert report["passed"] is False
    assert report["summary"]["total"] == 4


# --- Tester service: setup ----------------------------------------------------

@pytest.mark.parametrize("payload", [{}, {"target_url": ""}, {"target_url": "localhost:5000"},
                                     {"target_url": "ftp://host/x"}, {"url": TARGET},
                                     {"target_url": "https://user:pw@host/system"},
                                     {"target_url": "https://host/system?x=1"}])
def test_tester_setup_rejects_missing_or_invalid_target_url(tester, payload):
    response = tester.post("/test/setup", json=payload)

    assert response.status_code == 400
    assert_error_body(response.get_json(), "tester")


def test_tester_setup_prepares_the_system_with_the_same_session_id(tester):
    session_id = new_session(tester)

    uuid.UUID(session_id)
    assert session_id in selftest._sessions


def test_tester_setup_creates_a_new_session_every_time(tester):
    assert new_session(tester) != new_session(tester)


@pytest.mark.parametrize("status, body", [
    (500, {"message": "Could not initialize the session database.", "source": "system"}),
    (409, {"message": "Only one active session is allowed.", "source": "system"}),
])
def test_tester_setup_forwards_a_system_setup_error(monkeypatch, status, body):
    failing = Flask("failing")
    failing.add_url_rule("/test/setup", "setup", lambda: (jsonify(body), status), methods=["POST"])
    tester = route_tester_to(monkeypatch, failing)

    response = tester.post("/test/setup", json={"target_url": TARGET})

    assert response.status_code == status
    assert response.get_json() == body
    assert service._sessions == {}


def test_tester_setup_reports_a_target_without_test_endpoints(monkeypatch):
    tester = route_tester_to(monkeypatch, Flask("bare"))

    response = tester.post("/test/setup", json={"target_url": TARGET})

    assert response.status_code == 404
    assert_error_body(response.get_json(), "system")


def test_tester_setup_reports_an_unreachable_target(monkeypatch):
    class Unreachable:
        def request(self, *args, **kwargs):
            raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(service, "make_client", lambda url, token=None: Unreachable())

    response = service.app.test_client().post("/test/setup", json={"target_url": TARGET})

    assert response.status_code == 500
    assert_error_body(response.get_json(), "communication")


# --- Tester service: run and report ------------------------------------------

def test_tester_run_returns_the_report_and_stores_it(tester):
    session_id = new_session(tester)

    run = tester.post("/test/run", json={"session_id": session_id, "poll_timeout": 0})
    stored = tester.get("/test/report", query_string={"session_id": session_id})

    assert run.status_code == 200
    report = run.get_json()
    assert report["session_id"] == session_id
    assert report["target_url"] == TARGET
    assert_spec_report(report, [c.id for c in CRITERIA])
    assert stored.status_code == 200
    assert stored.get_json() == report


def test_tester_report_is_409_before_the_suite_runs(tester):
    session_id = new_session(tester)

    response = tester.get("/test/report", query_string={"session_id": session_id})

    assert response.status_code == 409
    assert_error_body(response.get_json(), "tester")


def test_tester_runs_each_session_only_once(tester):
    session_id = new_session(tester)
    tester.post("/test/run", json={"session_id": session_id, "poll_timeout": 0})

    again = tester.post("/test/run", json={"session_id": session_id, "poll_timeout": 0})

    assert again.status_code == 409
    assert_error_body(again.get_json(), "tester")


def test_tester_rejects_a_run_while_one_is_in_progress(tester):
    session_id = new_session(tester)
    service._sessions[session_id]["state"] = "running"

    assert tester.post("/test/run", json={"session_id": session_id}).status_code == 409


def test_tester_run_and_report_errors(tester):
    assert tester.post("/test/run", json={}).status_code == 400
    assert tester.post("/test/run", data="not json").status_code == 400
    assert tester.post("/test/run", json={"session_id": "unknown"}).status_code == 404
    assert tester.get("/test/report").status_code == 400
    assert tester.get("/test/report", query_string={"session_id": "unknown"}).status_code == 404


@pytest.mark.parametrize("extra", [{"poll_timeout": "fast"}, {"poll_timeout": -1}, {"poll_timeout": 600},
                                   {"poll_timeout": True}, {"only": "AC-03"}])
def test_tester_invalid_run_options_do_not_consume_the_session(tester, extra):
    session_id = new_session(tester)

    rejected = tester.post("/test/run", json={"session_id": session_id, **extra})
    accepted = tester.post("/test/run", json={"session_id": session_id, "poll_timeout": 0})

    assert rejected.status_code == 400
    assert accepted.status_code == 200


def test_tester_run_can_select_tests(tester):
    session_id = new_session(tester)

    report = tester.post("/test/run", json={"session_id": session_id, "poll_timeout": 0,
                                            "only": ["AC-03"]}).get_json()

    assert [r["test_id"] for r in report["results"]] == ["AC-03"]


def test_tester_abort_returns_500_and_keeps_the_available_results(tester, monkeypatch, tmp_path):
    monkeypatch.setattr(service, "run_suite",
                        lambda *args, **kwargs: run_suite(*args, fixtures_root=tmp_path / "missing", **kwargs))
    session_id = new_session(tester)

    run = tester.post("/test/run", json={"session_id": session_id, "poll_timeout": 0})
    stored = tester.get("/test/report", query_string={"session_id": session_id})
    again = tester.post("/test/run", json={"session_id": session_id})

    assert run.status_code == 500
    assert_error_body(run.get_json(), "tester")
    assert stored.status_code == 200
    assert stored.get_json()["aborted"] is True
    assert_spec_report(stored.get_json(), [c.id for c in CRITERIA])
    assert again.status_code == 409


def test_tester_crash_still_stores_a_report(tester, monkeypatch):
    def crash(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(service, "run_suite", crash)
    session_id = new_session(tester)

    run = tester.post("/test/run", json={"session_id": session_id})
    stored = tester.get("/test/report", query_string={"session_id": session_id}).get_json()

    assert run.status_code == 500
    assert {r["outcome"] for r in stored["results"]} == {"skip"}
    assert_spec_report(stored, [c.id for c in CRITERIA])


def test_tester_against_reference_system_reads_its_code_coverage(monkeypatch):
    tester = route_tester_to(monkeypatch, make_reference_system())
    session_id = new_session(tester)

    report = tester.post("/test/run", json={"session_id": session_id, "poll_timeout": 0}).get_json()

    assert report["coverage"]["code"]["covered"] == 42
    assert report["passed"] is True


# --- Command-line runner -----------------------------------------------------

def test_cli_exits_zero_when_every_criterion_passes(capsys):
    reference = make_reference_system()

    code = cli_main(["--url", TARGET, "--poll-timeout", "0"],
                    make_client=lambda url, token=None: WSGIClient(reference))

    assert code == 0
    out = capsys.readouterr().out
    assert "0 failed" in out and "Coverage:" in out


def test_cli_exits_nonzero_when_a_criterion_fails():
    broken = make_reference_system("alter_content")

    code = cli_main(["--url", TARGET, "--poll-timeout", "0"],
                    make_client=lambda url, token=None: WSGIClient(broken))

    assert code == 1


def test_cli_can_skip_setup_for_a_system_without_test_endpoints():
    bare = make_reference_system("no_test_endpoints")

    assert cli_main(["--url", TARGET, "--poll-timeout", "0"],
                    make_client=lambda url, token=None: WSGIClient(bare)) == 2
    assert cli_main(["--url", TARGET, "--poll-timeout", "0", "--no-setup"],
                    make_client=lambda url, token=None: WSGIClient(bare)) == 0


def test_cli_exits_two_when_target_is_unreachable():
    class Unreachable:
        def request(self, *args, **kwargs):
            raise urllib.error.URLError("connection refused")

    assert cli_main(["--url", TARGET], make_client=lambda url, token=None: Unreachable()) == 2
