"""Tests for the /test/setup, /test/run and /test/report endpoints and the CLI runner.

Covers both roles: this service as the validated system (selftest.py), and the
tester service (test_runner/service.py) that runs the suite against a URL.
"""
import urllib.error

import pytest
from flask import Flask

import selftest
from app import app, bundles
from fake_systems import make_reference_system
from test_runner import service
from test_runner.__main__ import main as cli_main
from test_runner.client import WSGIClient
from test_runner.suite import CRITERIA

TARGET = "http://system-under-test.example"


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    bundles.clear()
    selftest._last_report["report"] = None
    service._setups.clear()
    service._reports.clear()
    service._latest["url"] = None
    # /test/run sets this for its pytest subprocess; tests below control it explicitly
    monkeypatch.delenv(selftest.SELFTEST_ENV, raising=False)


@pytest.fixture
def system():
    return app.test_client()


@pytest.fixture
def tester(monkeypatch):
    """Tester service whose HTTP client is routed to our app in-process."""
    monkeypatch.setattr(service, "make_client", lambda url, token=None: WSGIClient(app))
    return service.app.test_client()


# --- Validated system (this service) ----------------------------------------

def test_system_setup_clears_bundles_and_lists_fixtures(system):
    system.post("/bundles", json={"product_id": "prd-88", "release_version": "v1.2"})
    assert bundles

    response = system.post("/test/setup")

    assert response.status_code == 200
    assert response.get_json()["status"] == "ready"
    fixtures = response.get_json()["fixtures"]
    for release in ("v1.2", "v1.3", "v1.4"):
        assert {"product_id": "prd-88", "release_version": release} in fixtures
    assert bundles == {}


def test_system_report_is_404_before_any_run(system):
    assert system.get("/test/report").status_code == 404


def test_system_run_stores_report_for_later_retrieval(system, monkeypatch):
    fake_report = {"run_id": "r1", "passed": True, "summary": {"total": 0}, "results": []}
    monkeypatch.setattr(selftest, "run_internal_tests", lambda: fake_report)

    run = system.post("/test/run")
    report = system.get("/test/report")

    assert run.status_code == 200
    assert run.get_json() == fake_report
    assert report.get_json() == fake_report


def test_system_run_refuses_to_recurse_inside_a_self_test(system, monkeypatch):
    monkeypatch.setenv(selftest.SELFTEST_ENV, "1")

    response = system.post("/test/run")

    assert response.status_code == 409


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


# --- Tester service ----------------------------------------------------------

@pytest.mark.parametrize("payload", [{}, {"url": ""}, {"url": "localhost:5000"}, {"url": "ftp://host/x"}])
def test_tester_rejects_missing_or_invalid_url(tester, payload):
    assert tester.post("/test/setup", json=payload).status_code == 400
    assert tester.post("/test/run", json=payload).status_code == 400


def test_tester_setup_resets_target(tester):
    bundles["stale"] = {}

    response = tester.post("/test/setup", json={"url": TARGET})

    assert response.status_code == 200
    assert response.get_json()["target_setup"] == "ok"
    assert bundles == {}


def test_tester_setup_tolerates_target_without_test_endpoints(monkeypatch):
    monkeypatch.setattr(service, "make_client", lambda url, token=None: WSGIClient(Flask("bare")))

    response = service.app.test_client().post("/test/setup", json={"url": TARGET})

    assert response.status_code == 200
    assert response.get_json()["target_setup"] == "not_supported"


def test_tester_setup_reports_unreachable_target(monkeypatch):
    class Unreachable:
        def request(self, *args, **kwargs):
            raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(service, "make_client", lambda url, token=None: Unreachable())

    response = service.app.test_client().post("/test/setup", json={"url": TARGET})

    assert response.status_code == 502


def test_tester_run_then_report(tester):
    run = tester.post("/test/run", json={"url": TARGET, "poll_timeout": 0})
    by_url = tester.get("/test/report", query_string={"url": TARGET})
    latest = tester.get("/test/report")

    assert run.status_code == 200
    report = run.get_json()
    assert report["target"] == TARGET
    assert [r["id"] for r in report["results"]] == [c.id for c in CRITERIA]
    assert by_url.get_json()["run_id"] == report["run_id"]
    assert latest.get_json()["run_id"] == report["run_id"]


def test_tester_run_can_select_criteria(tester):
    report = tester.post("/test/run", json={"url": TARGET, "poll_timeout": 0, "only": ["AC-03"]}).get_json()

    assert [r["id"] for r in report["results"]] == ["AC-03"]


@pytest.mark.parametrize("extra", [{"poll_timeout": "fast"}, {"poll_timeout": -1}, {"poll_timeout": 600},
                                   {"poll_timeout": True}, {"only": "AC-03"}])
def test_tester_run_validates_options(tester, extra):
    assert tester.post("/test/run", json={"url": TARGET, **extra}).status_code == 400


def test_tester_report_is_404_for_unknown_target(tester):
    assert tester.get("/test/report", query_string={"url": TARGET}).status_code == 404


# --- Command-line runner -----------------------------------------------------

def test_cli_exits_zero_when_every_criterion_passes(capsys):
    reference = make_reference_system()

    code = cli_main(["--url", TARGET, "--poll-timeout", "0"],
                    make_client=lambda url, token=None: WSGIClient(reference))

    assert code == 0
    assert "0 failed" in capsys.readouterr().out


def test_cli_exits_nonzero_when_a_criterion_fails():
    broken = make_reference_system("alter_content")

    code = cli_main(["--url", TARGET, "--poll-timeout", "0", "--no-setup"],
                    make_client=lambda url, token=None: WSGIClient(broken))

    assert code == 1


def test_cli_exits_two_when_target_is_unreachable():
    class Unreachable:
        def request(self, *args, **kwargs):
            raise urllib.error.URLError("connection refused")

    assert cli_main(["--url", TARGET], make_client=lambda url, token=None: Unreachable()) == 2
