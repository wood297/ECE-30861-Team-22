"""test_runner/spec_check.py: our system against spec.yaml, the contract graders test with."""
import pytest
from flask import jsonify

import artifact_pipeline
from account_config import EMPTY_ACCOUNT
from app import app
from test_runner.client import WSGIClient
from test_runner.spec_check import Checker, load_spec, shared_releases

SPEC = load_spec()


def run_checks():
    return Checker(WSGIClient(app), SPEC, poll_seconds=0).run()


def failures(results):
    return {c.name: c.detail for c in results if not c.ok}


def test_our_system_conforms_to_spec_yaml():
    results = run_checks()
    assert not failures(results)
    assert len(results) >= 45


def test_the_spec_table_lists_every_shared_release_with_its_expected_statuses():
    rows = {row.release: row for row in shared_releases(SPEC)}
    assert set(rows) == {"v1.2", "v2.0", "v1.3", "v1.4", "v1.2-github-inaccessible", "v1.5-jenkins",
                         "v1.5-jenkins-missing", "v1.5-jenkins-empty", "v0.0-does-not-exist"}
    assert rows["v1.4"].categories["SBOM"] == "INVALID"
    assert rows["v1.5-jenkins-missing"].categories["Test Logs"] == "INACCESSIBLE"
    assert rows["v1.5-jenkins-empty"].categories["Test Logs"] == "INVALID"
    assert rows["v1.5-jenkins"].sha256 == {
        "test_logs.md": "f5cf3fd835fdbbe957222dcc18a4fa12d3d192936e20a7eeee7cea22b1658173"}
    assert rows["v0.0-does-not-exist"].bundle == "FAILED"
    assert rows["v1.5-jenkins-empty"].sources == {"Test Logs": "Jenkins"}
    assert rows["v1.2-github-inaccessible"].sources == {"Test Logs": "GitHub"}
    assert rows["v1.2"].sources == {}


def test_it_catches_a_release_the_system_does_not_serve(monkeypatch):
    monkeypatch.setattr(artifact_pipeline, "ACCOUNT", EMPTY_ACCOUNT)
    # Without the account, the Jenkins releases would come from the tester's local copies in
    # fixtures/: still "COMPLETED", but not from Jenkins, which the spec's table requires
    failed = failures(run_checks())
    assert "'fixtures'" in failed["prd-88 v1.5-jenkins: Test Logs comes from Jenkins"]
    assert "MISSING" in failed["prd-88 v1.5-jenkins-missing: categories as documented"]


def test_it_catches_a_response_that_breaks_its_schema(monkeypatch):
    monkeypatch.setitem(app.view_functions, "health", lambda: jsonify({"status": "maybe"}))
    failed = failures(run_checks())
    assert "status" in failed["GET /health -> 200 HealthResponse"]


@pytest.mark.parametrize("release", ["v1.5-jenkins", "v1.5-jenkins-missing", "v1.5-jenkins-empty"])
def test_setup_lists_the_jenkins_releases(release):
    body = app.test_client().post("/test/setup", json={"session_id": f"list-{release}"}).get_json()
    assert {"product_id": "prd-88", "release_version": release} in body["fixtures"]
