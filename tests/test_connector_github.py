"""The shared HTTP contract and GitHub-specific retrieval checks."""
import base64

import pytest

from connector_contract import SECRET, FakeResponse, HttpConnectorContract
from connectors.base import AUTH_FAILED, RETRIEVED, SOURCE_ERROR, UNAVAILABLE, SettingsError
from connectors.github import GitHubConnector

REVISION = "a" * 40
ROOT = "b" * 40
ARTIFACTS = "c" * 40
PRODUCT = "d" * 40
RELEASE = "e" * 40
BLOB = "f" * 40
BASE = "/repos/example/artifacts/git/"


def tree(sha, *entries, truncated=False):
    return FakeResponse.json({"sha": sha, "tree": list(entries), "truncated": truncated})


def entry(name, sha, kind="tree", mode="040000"):
    return {"path": name, "type": kind, "mode": mode, "sha": sha}


class TestGitHubContract(HttpConnectorContract):
    connector = GitHubConnector()
    settings = {"repository": "example/artifacts"}
    ref = {"revision": REVISION, "paths": ["artifacts/{product}/{release}/sbom.json",
                                          "artifacts/{product}/{release}/sbom.md"]}
    expected_filename = "sbom.json"
    expected_bytes = b"\xff\x00original bytes\r\n"

    def serve_file(self, request):
        assert request.headers["Authorization"] == f"Bearer {SECRET}"
        target = request.target
        if target == BASE + "commits/" + REVISION:
            return FakeResponse.json({"sha": REVISION, "tree": {"sha": ROOT}})
        if target == BASE + "trees/" + ROOT:
            return tree(ROOT, entry("artifacts", ARTIFACTS))
        if target == BASE + "trees/" + ARTIFACTS:
            return tree(ARTIFACTS, entry(self.product, PRODUCT))
        if target == BASE + "trees/" + PRODUCT:
            return tree(PRODUCT, entry(self.release, RELEASE))
        if target == BASE + "trees/" + RELEASE:
            return tree(RELEASE, entry("sbom.json", BLOB, "blob", "100644"))
        assert target == BASE + "blobs/" + BLOB
        return FakeResponse.json({"sha": BLOB, "encoding": "base64",
                                  "content": base64.encodebytes(self.expected_bytes).decode(),
                                  "size": len(self.expected_bytes)})

    def serve_missing(self, request):
        if request.target == BASE + "trees/" + RELEASE:
            return tree(RELEASE)
        return self.serve_file(request)

    def test_tries_next_candidate_after_complete_tree_confirms_absence(self):
        def only_markdown(request):
            if request.target == BASE + "trees/" + RELEASE:
                return tree(RELEASE, entry("sbom.md", BLOB, "blob", "100644"))
            return self.serve_file(request)

        source = self.source(only_markdown)
        result = self.fetch(source)
        assert (result.outcome, result.filename, result.version, result.data) == (
            RETRIEVED, "sbom.md", REVISION, self.expected_bytes
        )
        targets = [request.target for request in source.requests]
        assert targets.count(BASE + "commits/" + REVISION) == 1
        assert targets.count(BASE + "blobs/" + BLOB) == 1

    @pytest.mark.parametrize(("response", "outcome"), [
        (FakeResponse(404), AUTH_FAILED),
        (FakeResponse(410), SOURCE_ERROR),
        (FakeResponse(403, headers={"X-RateLimit-Remaining": "0"}), UNAVAILABLE),
        (FakeResponse(403, headers={"Retry-After": "10"}), UNAVAILABLE),
        (FakeResponse.redirect("https://api.github.com/repositories/123"), SOURCE_ERROR),
        (tree(ROOT, truncated=True), SOURCE_ERROR),
        (tree(PRODUCT), SOURCE_ERROR),
        (tree(ROOT, entry("artifacts", ARTIFACTS), entry("artifacts", PRODUCT)), SOURCE_ERROR),
    ])
    def test_stops_on_tree_errors(self, response, outcome):
        source = self.source(lambda request: response if "/trees/" in request.target else self.serve_file(request))
        assert self.fetch(source, direct=True).outcome == outcome
        assert [r.target for r in source.requests] == [BASE + "commits/" + REVISION, BASE + "trees/" + ROOT]

    def test_rejects_commit_mismatch(self):
        source = self.source(lambda request: FakeResponse.json({"sha": BLOB, "tree": {"sha": ROOT}}))
        assert self.fetch(source, direct=True).outcome == SOURCE_ERROR
        assert len(source.requests) == 1

    def test_repository_404_is_auth_failed(self):
        source = self.source(lambda request: FakeResponse(404))
        assert self.fetch(source, direct=True).outcome == AUTH_FAILED
        assert [r.target for r in source.requests] == [BASE + "commits/" + REVISION]

    @pytest.mark.parametrize("payload", [
        {"sha": BLOB, "encoding": "base64", "content": "!!!!", "size": 1},
        {"sha": BLOB, "encoding": "base64", "content": "YQ==", "size": 2},
        {"sha": ROOT, "encoding": "base64", "content": "YQ==", "size": 1},
    ])
    def test_rejects_malformed_blob(self, payload):
        source = self.source(lambda r: FakeResponse.json(payload) if "/blobs/" in r.target else self.serve_file(r))
        assert self.fetch(source, direct=True).outcome == SOURCE_ERROR
        assert source.requests[-1].target == BASE + "blobs/" + BLOB

    @pytest.mark.parametrize("revision", ["main", "a" * 39, "A" * 40, "{product}"])
    def test_rejects_unpinned_revision(self, revision):
        with pytest.raises(SettingsError):
            self.connector.validate_ref({**self.ref, "revision": revision})

    @pytest.mark.parametrize("repository", ["owner/..", "owner/repo/extra", "https://github.com/owner/repo"])
    def test_rejects_invalid_repository(self, repository):
        with pytest.raises(SettingsError):
            self.connector.validate_settings({"repository": repository})
