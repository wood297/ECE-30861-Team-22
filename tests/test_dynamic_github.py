import io
from unittest.mock import Mock
from urllib.error import HTTPError
from zipfile import ZipFile

import pytest

import artifact_pipeline
import github_client
from app import app
from github_client import ConfirmedArtifactMissing, GitHubClient

SOURCE = {"repository": "example/artifact-repo", "revision": "release/v2"}
SBOM = b'{"bomFormat":"CycloneDX","specVersion":"1.5","version":1}\r\n'
INSTRUCTIONS = "# User Instructions\r\n\r\nStart the device.\r\n".encode()


@pytest.fixture
def client(monkeypatch):
    # Exercise the real client factory for dynamic requests. Every GitHub call is mocked.
    monkeypatch.setattr(artifact_pipeline, "make_github_client", REAL_CLIENT_FACTORY)
    monkeypatch.setenv("GITHUB_TOKEN", "test-secret")
    return app.test_client()


# Save the factory before conftest's session-scoped legacy catalog stub replaces it.
REAL_CLIENT_FACTORY = artifact_pipeline.make_github_client


def create(client, product="github-only", release="v2", *, source=SOURCE):
    request_body = {"product_id": product, "release_version": release}
    if source is not None:
        request_body["github"] = source
    response = client.post("/bundles", json=request_body)
    assert response.status_code == 202
    assert response.json["status"] == "PENDING"
    return client.get(f"/bundles/{response.json['bundle_id']}").json


def manifest(bundle):
    return {item["category"]: item for item in bundle["manifest"]}


def test_github_only_release_flows_through_to_zip(client, monkeypatch):
    prefix = "artifacts/github-only/v2/"
    files = {prefix + "sbom.json": SBOM, prefix + "user_instructions.md": INSTRUCTIONS}
    seen = []

    def get_file(self, path, ref):
        assert self.config.repository == SOURCE["repository"]
        assert self.config.token == "test-secret"
        seen.append((path, ref))
        return files[path]

    monkeypatch.setattr(GitHubClient, "get_file", get_file)
    bundle = create(client, source={**SOURCE, "token": "ignored-client-secret"})
    items = manifest(bundle)

    assert bundle["status"] == "PARTIAL_SUCCESS"
    assert bundle["github"] == SOURCE
    assert seen == [(path, SOURCE["revision"]) for path in files]
    for category in ("SBOM", "User Instructions"):
        assert items[category]["status"] == "PRESENT"
        assert items[category]["external_source"] == "GitHub"
    for category in ("Risk Assessment", "Test Logs"):
        assert items[category]["status"] == "MISSING"
    with ZipFile(io.BytesIO(client.get(bundle["download_url"]).data)) as archive:
        assert set(archive.namelist()) == {"sbom.json", "user_instructions.md", "manifest.json"}
        assert archive.read("sbom.json") == SBOM
        assert archive.read("user_instructions.md") == INSTRUCTIONS


def test_missing_and_invalid_artifacts_use_existing_manifest_statuses(client, monkeypatch):
    def get_file(self, path, ref):
        if path.endswith("sbom.json"):
            return b'{"bomFormat":"Other"}'
        raise ConfirmedArtifactMissing()

    monkeypatch.setattr(GitHubClient, "get_file", get_file)
    bundle = create(client)
    items = manifest(bundle)
    assert items["SBOM"]["status"] == "INVALID"
    assert items["User Instructions"] == {
        "category": "User Instructions", "status": "MISSING", "external_source": "GitHub",
    }
    assert bundle["artifacts"] == []


def test_dynamic_source_skips_catalog_while_legacy_request_uses_it(client, monkeypatch):
    seen = []

    def get_file(self, path, ref):
        seen.append((self.config.repository, path))
        raise ConfirmedArtifactMissing()

    monkeypatch.setattr(GitHubClient, "get_file", get_file)
    release = "v1.2-github-inaccessible"
    artifact_pipeline.clear_github_cache()
    try:
        dynamic = manifest(create(client, "prd-88", release))
        assert dynamic["Test Logs"]["external_source"] == "fixtures"
        assert dynamic["Risk Assessment"]["status"] == "PRESENT"
        assert all(repository == SOURCE["repository"] for repository, _ in seen)

        legacy = manifest(create(client, "prd-88", release, source=None))
        assert legacy["Test Logs"]["status"] == "INACCESSIBLE"
        assert legacy["Test Logs"]["external_source"] == "GitHub"
        assert seen[-1] == ("prithika-sundar/cra-artifact-test", f"artifacts/prd-88/{release}/test_logs.md")
    finally:
        artifact_pipeline.clear_github_cache()


@pytest.mark.parametrize(("http_status", "error_code"), [
    (401, "authentication"),
    (404, "repository_access"),
    (422, "upstream"),
])
def test_github_http_errors_are_sanitized(client, monkeypatch, caplog, http_status, error_code):
    opener = Mock()

    def reject(request, **kwargs):
        raise HTTPError(request.full_url, http_status, "test-secret", {}, io.BytesIO(b"test-secret"))

    opener.open.side_effect = reject
    monkeypatch.setattr(github_client, "build_opener", lambda *args: opener)
    bundle = create(client, source={**SOURCE, "revision": "unknown-ref"})

    assert bundle["status"] == "PARTIAL_SUCCESS"
    for category in ("SBOM", "User Instructions"):
        item = manifest(bundle)[category]
        assert item["status"] == "INACCESSIBLE"
        assert item["source_failure_detail"] == f"GitHub artifact retrieval failed ({error_code})."
    assert "test-secret" not in str(bundle)
    assert "test-secret" not in caplog.text


def test_missing_server_token_does_not_make_requests(client, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN")
    opener = Mock()
    monkeypatch.setattr(github_client, "build_opener", opener)
    items = manifest(create(client))
    assert items["SBOM"]["status"] == items["User Instructions"]["status"] == "INACCESSIBLE"
    opener.assert_not_called()


@pytest.mark.parametrize("overrides", [
    {"github": None},
    {"github": "example/repo"},
    {"github": {**SOURCE, "repository": "https://github.com/example/repo"}},
    {"github": {**SOURCE, "revision": ""}},
    {"github": {**SOURCE, "revision": "bad\nref"}},
    {"product_id": "../unsafe"},
    {"release_version": "nested/version"},
])
def test_invalid_dynamic_source_is_rejected(client, overrides):
    payload = {"product_id": "p", "release_version": "v2", "github": SOURCE, **overrides}
    assert client.post("/bundles", json=payload).status_code == 400
