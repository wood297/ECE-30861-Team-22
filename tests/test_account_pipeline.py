"""The pod-test account in the pipeline and bundler: test_account.json -> connectors -> decide ->
manifest -> zip (kit steps 2.1 and 2.2)."""
import hashlib
import io
import json
import os
import zipfile
from pathlib import Path

import pytest
from connector_contract import FakeResponse, FakeSource

import artifact_pipeline
from account_config import ACCOUNT_PATH, AccountConfigError, load_account, parse_account
from app import app
from bundle_service import archive_names, build_archive
from connectors.base import FetchResult
from secret_store import InMemorySecretStore

REPO = Path(__file__).parent.parent
OFFLINE = Path(__file__).parent / "fixtures"
JENKINS_LOG = OFFLINE / "jenkins/artifacts/prd-88/v1.5-jenkins/test_logs.md"

ORG = "11111111-1111-4111-8111-111111111111"
FIXTURES_CONN = "22222222-2222-4222-8222-222222222222"
JENKINS_CONN = "33333333-3333-4333-8333-333333333333"
JENKINS_TEST_LOGS = {"conn": JENKINS_CONN, "job": "pod/cra-test-logs", "build": 2,
                     "paths": ["artifacts/{product}/{release}/test_logs.md"]}


def fixture_pin(name):
    return {"conn": FIXTURES_CONN, "paths": [f"prd-88/v1.2/{name}"]}


def account(releases=None, rules=None):
    return parse_account({
        "org_id": ORG, "name": "test",
        "connections": {
            FIXTURES_CONN: {"type": "fixtures", "settings": {}},
            JENKINS_CONN: {"type": "jenkins", "settings": {"url": "https://jenkins.example.com", "user": "team22"}},
        },
        "products": {"prd-77": {"rules": rules or {}, "releases": releases or {}}},
    })


FULL_RELEASE = {"v1.0": {"SBOM": fixture_pin("sbom.md"), "Risk Assessment": fixture_pin("risk_assessment.md"),
                         "User Instructions": fixture_pin("user_instructions.md"), "Test Logs": JENKINS_TEST_LOGS}}


def bundle_for(product, release):
    client = app.test_client()
    created = client.post("/bundles", json={"product_id": product, "release_version": release}).get_json()
    bundle = client.get(f"/bundles/{created['bundle_id']}").get_json()
    archive = client.get(f"/bundles/{created['bundle_id']}/download")
    files = {}
    if archive.status_code == 200:
        with zipfile.ZipFile(io.BytesIO(archive.data)) as zipped:
            files = {name: zipped.read(name) for name in zipped.namelist()}
    return bundle, {item["category"]: item for item in bundle["manifest"]}, files


# The committed account

def test_the_committed_account_loads_and_every_pinned_file_has_a_copy():
    loaded = load_account(ACCOUNT_PATH)
    assert loaded.name == "pod-test"
    assert {conn.type for conn in loaded.connections.values()} == {"fixtures", "jenkins", "github", "confluence"}
    for (product, release), pinned in loaded.releases.items():
        assert set(pinned.pins) == set(artifact_pipeline.EXPECTED_FILES), f"{release} must pin every category"
        for category, entry in pinned.pins.items():
            conn = loaded.connections[entry["conn"]]
            names = entry.get("paths") or [entry["filename"]]
            names = [name.replace("{product}", product).replace("{release}", release) for name in names]
            root = REPO / "fixtures" if conn.type == "fixtures" else OFFLINE / conn.type
            exists = any((root / name).is_file() for name in names)
            assert exists == (release != "v1.5-jenkins-missing" or category != "Test Logs"), (release, category)


def test_the_jenkins_copies_are_the_bytes_the_pinned_builds_hold():
    # Published to the pod; checked against Jenkins by connectors/jenkins_live_check.py
    assert hashlib.sha256(JENKINS_LOG.read_bytes()).hexdigest() == \
        "f5cf3fd835fdbbe957222dcc18a4fa12d3d192936e20a7eeee7cea22b1658173"
    assert (OFFLINE / "jenkins/artifacts/prd-88/v1.5-jenkins-empty/test_logs.md").read_bytes() == b""


# The shared pod releases, end to end through the API (offline copies stand in for the network)

@pytest.mark.parametrize("release, status, test_logs, detail", [
    ("v1.5-jenkins", "COMPLETED", "PRESENT", None),
    ("v1.5-jenkins-missing", "PARTIAL_SUCCESS", "INACCESSIBLE", "pinned"),
    ("v1.5-jenkins-empty", "PARTIAL_SUCCESS", "INVALID", "test_logs.md is empty"),
])
def test_the_pod_jenkins_releases(release, status, test_logs, detail):
    bundle, items, files = bundle_for("prd-88", release)
    assert bundle["status"] == status
    assert items["Test Logs"]["status"] == test_logs
    assert items["Test Logs"]["external_source"] == "Jenkins"
    assert items["Test Logs"]["source_file"] == "test_logs.md"
    if detail:
        assert detail in items["Test Logs"]["source_failure_detail"]
    for category, name in (("SBOM", "sbom.md"), ("Risk Assessment", "risk_assessment.md"),
                           ("User Instructions", "user_instructions.md")):
        assert items[category]["status"] == "PRESENT" and items[category]["external_source"] == "fixtures"
        assert files[name] == (REPO / "fixtures/prd-88/v1.2" / name).read_bytes()
    if test_logs == "PRESENT":
        assert files["test_logs.md"] == JENKINS_LOG.read_bytes()
    else:
        assert "test_logs.md" not in files


def test_a_release_from_all_four_sources_is_complete():
    bundle, items, files = bundle_for("prd-88", "v1.6-all-sources")
    assert bundle["status"] == "COMPLETED"
    assert {c: i["external_source"] for c, i in items.items()} == {
        "SBOM": "GitHub", "Risk Assessment": "Confluence", "Test Logs": "Jenkins", "User Instructions": "GitHub"}
    assert files["sbom.json"] == (OFFLINE / "github/artifacts/test-product/v1.0/sbom.json").read_bytes()
    assert files["risk_assessment.md"] == (OFFLINE / "confluence/risk_assessment.md").read_bytes()
    assert files["test_logs.md"] == JENKINS_LOG.read_bytes()
    assert json.loads(files["manifest.json"])["status"] == "COMPLETED"


def test_releases_outside_the_account_still_come_from_the_fixtures_folder():
    bundle, items, _ = bundle_for("prd-88", "v1.2")
    assert bundle["status"] == "COMPLETED"
    assert {item["external_source"] for item in items.values()} == {"fixtures"}
    bundle, items, _ = bundle_for("prd-88", "v1.2-github-inaccessible")
    assert items["Test Logs"]["external_source"] == "GitHub"


# The real Jenkins connector inside the pipeline, against a fake network

@pytest.fixture
def fake_jenkins(monkeypatch):
    """Our account on a fake Jenkins, with the connection's token in an in-memory store."""
    archive = {"artifacts/prd-77/v1.0/test_logs.md": JENKINS_LOG.read_bytes()}
    state = {"status": 200}

    def handler(request):
        if state["status"] != 200:
            return FakeResponse(state["status"], b"<html>down</html>")
        if request.target.startswith("/job/pod/job/cra-test-logs/2/api/json"):
            return FakeResponse.json({"building": False, "result": "SUCCESS",
                                      "artifacts": [{"relativePath": p} for p in archive]})
        prefix = "/job/pod/job/cra-test-logs/2/artifact/"
        if request.target.startswith(prefix) and request.target[len(prefix):] in archive:
            return FakeResponse(200, archive[request.target[len(prefix):]])
        return FakeResponse(404, b"Not Found")

    source = FakeSource(handler)
    secrets = InMemorySecretStore()
    secrets.put_connection_secret(ORG, JENKINS_CONN, "jenkins-api-token-for-tests")
    monkeypatch.setattr(artifact_pipeline, "ACCOUNT", account(FULL_RELEASE))
    monkeypatch.setattr(artifact_pipeline, "CONNECTOR_HTTP_OPTIONS", source.http_options())
    monkeypatch.setattr(artifact_pipeline, "get_secret_store", lambda: secrets)
    source.state = state
    return source


def test_the_jenkins_connector_runs_inside_a_bundle(fake_jenkins):
    bundle, items, files = bundle_for("prd-77", "v1.0")
    assert bundle["status"] == "COMPLETED"
    assert items["Test Logs"] == {"category": "Test Logs", "status": "PRESENT", "external_source": "Jenkins",
                                  "source_file": "test_logs.md", "classified_by": "rules"}
    assert files["test_logs.md"] == JENKINS_LOG.read_bytes()
    assert [r.host for r in fake_jenkins.requests] == ["jenkins.example.com"] * 2
    assert all(r.headers["Authorization"].startswith("Basic ") for r in fake_jenkins.requests)


def test_a_pinned_file_is_fetched_once_and_then_reused(fake_jenkins):
    bundle_for("prd-77", "v1.0")
    bundle_for("prd-77", "v1.0")
    assert len(fake_jenkins.requests) == 2


def test_an_outage_costs_only_test_logs_and_is_retried_next_time(fake_jenkins):
    fake_jenkins.state["status"] = 503
    bundle, items, files = bundle_for("prd-77", "v1.0")
    assert bundle["status"] == "PARTIAL_SUCCESS"
    assert items["Test Logs"]["status"] == "INACCESSIBLE"
    assert "unavailable" in items["Test Logs"]["source_failure_detail"]
    assert set(files) == {"sbom.md", "risk_assessment.md", "user_instructions.md", "manifest.json"}
    fake_jenkins.state["status"] = 200
    bundle, items, _ = bundle_for("prd-77", "v1.0")
    assert bundle["status"] == "COMPLETED"


def test_without_a_stored_token_jenkins_is_inaccessible_and_never_called(fake_jenkins, monkeypatch):
    monkeypatch.setattr(artifact_pipeline, "get_secret_store", lambda: None)
    _, items, _ = bundle_for("prd-77", "v1.0")
    assert items["Test Logs"]["status"] == "INACCESSIBLE"
    assert "no stored" in items["Test Logs"]["source_failure_detail"]
    assert fake_jenkins.requests == []


def test_a_broken_secret_store_is_inaccessible_not_a_failed_bundle(fake_jenkins, monkeypatch):
    class Broken:
        def get_connection_secret(self, org_id, conn_id):
            raise RuntimeError("AccessDeniedException: not authorized")
    monkeypatch.setattr(artifact_pipeline, "get_secret_store",
                        lambda: artifact_pipeline._GuardedSecrets(Broken()))
    bundle, items, _ = bundle_for("prd-77", "v1.0")
    assert bundle["status"] == "PARTIAL_SUCCESS"
    assert items["Test Logs"]["status"] == "INACCESSIBLE"
    assert "AccessDenied" not in json.dumps(bundle)


# Choosing the secret store

def test_a_local_secrets_file_is_used_when_set(tmp_path, monkeypatch):
    path = tmp_path / "local.secrets.json"
    path.write_text(json.dumps({"connections": {JENKINS_CONN: "local-token-value"}}))
    os.chmod(path, 0o600)
    monkeypatch.setenv(artifact_pipeline.LOCAL_SECRETS_ENV, str(path))
    store = artifact_pipeline.get_secret_store()
    assert store is not None and store.get_connection_secret(ORG, JENKINS_CONN).reveal() == "local-token-value"


@pytest.mark.skipif(os.name != "posix", reason="file modes")
def test_a_secrets_file_others_can_read_is_refused(tmp_path, monkeypatch):
    path = tmp_path / "open.secrets.json"
    path.write_text(json.dumps({"connections": {}}))
    os.chmod(path, 0o644)
    monkeypatch.setenv(artifact_pipeline.LOCAL_SECRETS_ENV, str(path))
    assert artifact_pipeline.get_secret_store() is None


def test_no_secret_settings_means_no_store():
    assert artifact_pipeline.get_secret_store() is None


# Rules, pins and unconfigured categories

def test_an_unconfigured_category_is_missing_from_no_source(monkeypatch):
    monkeypatch.setattr(artifact_pipeline, "ACCOUNT", account({"v1.0": {"SBOM": fixture_pin("sbom.md")}}))
    bundle, items, _ = bundle_for("prd-77", "v1.0")
    assert bundle["status"] == "PARTIAL_SUCCESS"
    assert items["SBOM"]["status"] == "PRESENT"
    assert items["Test Logs"] == {"category": "Test Logs", "status": "MISSING", "external_source": "none"}


def test_product_rules_serve_any_release_and_pins_win(monkeypatch):
    rules = {"SBOM": {"conn": FIXTURES_CONN, "paths": ["prd-88/{release}/sbom.md"]}}
    pins = {"v1.2": {"SBOM": fixture_pin("risk_assessment.md")}}
    monkeypatch.setattr(artifact_pipeline, "ACCOUNT", account(pins, rules))
    _, items, files = bundle_for("prd-77", "v1.3")
    assert items["SBOM"]["status"] == "PRESENT"
    assert files["sbom.md"] == (REPO / "fixtures/prd-88/v1.3/sbom.md").read_bytes()
    _, items, _ = bundle_for("prd-77", "v1.2")
    assert items["SBOM"]["status"] == "INVALID"   # the pin points at a risk assessment


def test_a_rule_that_resolves_to_an_absent_file_is_inaccessible(monkeypatch):
    rules = {"SBOM": {"conn": FIXTURES_CONN, "paths": ["prd-88/{release}/sbom.md"]}}
    monkeypatch.setattr(artifact_pipeline, "ACCOUNT", account(rules=rules))
    _, items, _ = bundle_for("prd-77", "v9.9")
    assert items["SBOM"]["status"] == "INACCESSIBLE" and items["SBOM"]["source_file"] == "sbom.md"


@pytest.mark.parametrize("name, category, status", [
    ("results.json", "Test Logs", "INVALID"),    # .json is only ever CycloneDX, an SBOM format
    ("test_logs.txt", "Test Logs", "INVALID"),
    ("test_logs.md", "Test Logs", "PRESENT"),
])
def test_retrieved_files_go_through_the_same_rules(name, category, status):
    result = FetchResult.retrieved(b"# Test Logs\n\nok\n", name)
    assert artifact_pipeline.decide_fetched(result, {}, category, None).status == status


# Loading test_account.json

@pytest.mark.parametrize("change, message", [
    (lambda a: a.update(org_id="not-a-uuid"), "org_id"),
    (lambda a: a["connections"][JENKINS_CONN].update(type="ftp"), "unknown type"),
    (lambda a: a["connections"][JENKINS_CONN]["settings"].update(url="http://jenkins.example.com"), "url"),
    (lambda a: a["connections"].update({"conn-1": {"type": "fixtures"}}), "connection id"),
    (lambda a: a["products"]["prd-77"]["releases"]["v1.0"].update(Docs=fixture_pin("sbom.md")), "not a category"),
    (lambda a: a["products"]["prd-77"]["releases"]["v1.0"]["SBOM"].update(conn=ORG), "conn must be"),
    (lambda a: a["products"]["prd-77"]["releases"]["v1.0"]["Test Logs"].update(build="lastSuccessfulBuild"),
     "build"),
    (lambda a: a["products"]["prd-77"]["releases"].update({"../v1": {}}), "release"),
])
def test_a_malformed_account_is_refused_with_its_location(change, message):
    data = {"org_id": ORG, "name": "test",
            "connections": {FIXTURES_CONN: {"type": "fixtures", "settings": {}},
                            JENKINS_CONN: {"type": "jenkins",
                                           "settings": {"url": "https://jenkins.example.com", "user": "team22"}}},
            "products": {"prd-77": {"releases": json.loads(json.dumps(FULL_RELEASE))}}}
    change(data)
    with pytest.raises(AccountConfigError, match=message):
        parse_account(data)


def test_a_missing_account_file_means_no_account(tmp_path):
    assert load_account(tmp_path / "absent.json").releases == {}


def test_tokens_cannot_be_put_in_the_account():
    data = {"org_id": ORG, "name": "test", "connections": {JENKINS_CONN: {
        "type": "jenkins", "settings": {"url": "https://jenkins.example.com", "user": "team22", "token": "x"}}}}
    with pytest.raises(AccountConfigError, match="token"):
        parse_account(data)


# The bundler

def test_archive_names_never_collide():
    artifacts = [{"category": "SBOM", "source_file": "report.md"},
                 {"category": "Test Logs", "source_file": "report.md"},
                 {"category": "User Instructions", "source_file": "manifest.json"}]
    assert archive_names(artifacts) == ["report.md", "test-logs/report.md", "user-instructions/manifest.json"]


def test_an_archive_with_shared_names_keeps_every_file_and_the_manifest():
    bundle = {"product_id": "p", "release_version": "r", "status": "COMPLETED", "manifest": [], "artifacts": [
        {"category": "SBOM", "source_file": "report.md", "content": "a"},
        {"category": "Test Logs", "source_file": "report.md", "content": "b"},
        {"category": "User Instructions", "source_file": "manifest.json", "content": "c"}]}
    with zipfile.ZipFile(io.BytesIO(build_archive(bundle))) as zipped:
        assert zipped.read("report.md") == b"a" and zipped.read("test-logs/report.md") == b"b"
        assert zipped.read("user-instructions/manifest.json") == b"c"
        assert json.loads(zipped.read("manifest.json"))["product_id"] == "p"
        assert len(zipped.namelist()) == len(set(zipped.namelist())) == 4


# connectors/account_live_check.py (offline: conftest serves the committed copies)

def test_the_live_check_passes_when_every_pin_matches_its_copy(tmp_path, monkeypatch, capsys):
    from connectors import account_live_check
    path = tmp_path / "pod.secrets.json"
    path.write_text(json.dumps({"connections": {}}))
    os.chmod(path, 0o600)
    monkeypatch.setenv(artifact_pipeline.LOCAL_SECRETS_ENV, str(path))
    assert account_live_check.main(["--type", "jenkins"]) == 0
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith("{")]
    assert len(rows) == 4 and all(row["ok"] for row in rows)
    assert {row["outcome"] for row in rows} == {"RETRIEVED", "NOT_FOUND"}


def test_the_live_check_fails_on_different_bytes(tmp_path, monkeypatch):
    from connectors import account_live_check
    path = tmp_path / "pod.secrets.json"
    path.write_text(json.dumps({"connections": {}}))
    os.chmod(path, 0o600)
    monkeypatch.setenv(artifact_pipeline.LOCAL_SECRETS_ENV, str(path))
    monkeypatch.setattr(artifact_pipeline, "run_connector",
                        lambda *args, **kwargs: FetchResult.retrieved(b"changed", "test_logs.md"))
    assert account_live_check.main(["--type", "jenkins"]) == 1


def test_the_live_check_needs_a_secret_store(capsys):
    from connectors import account_live_check
    assert account_live_check.main([]) == 2
    assert "CRA_LOCAL_SECRETS" in capsys.readouterr().out


# The tester's expected bytes for the Jenkins releases (fixtures/prd-88/v1.5-jenkins*/). Our own
# system never serves these copies (test_account.json wins); they must equal the real sources.

@pytest.mark.parametrize("release", ["v1.5-jenkins", "v1.5-jenkins-missing", "v1.5-jenkins-empty"])
def test_the_testers_expected_bytes_match_the_real_sources(release):
    folder = REPO / "fixtures/prd-88" / release
    for name in ("sbom.md", "risk_assessment.md", "user_instructions.md"):
        assert (folder / name).read_bytes() == (REPO / "fixtures/prd-88/v1.2" / name).read_bytes(), name
    jenkins_copy = OFFLINE / "jenkins/artifacts/prd-88" / release / "test_logs.md"
    assert (folder / "test_logs.md").is_file() == jenkins_copy.is_file()
    if jenkins_copy.is_file():
        assert (folder / "test_logs.md").read_bytes() == jenkins_copy.read_bytes()


def test_account_releases_are_served_from_their_sources_not_the_testers_copies():
    _, items, _ = bundle_for("prd-88", "v1.5-jenkins")
    assert items["Test Logs"]["external_source"] == "Jenkins"
    assert {items[c]["external_source"] for c in ("SBOM", "Risk Assessment", "User Instructions")} == {"fixtures"}
