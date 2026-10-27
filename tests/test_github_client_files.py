"""File transport contracts, using only fake GitHub responses."""

import base64
import io
import json
from unittest.mock import Mock
from urllib.error import HTTPError, URLError

import pytest

from github_artifact_retrieval import ConfirmedArtifactMissing as AdapterMissing
from github_artifact_retrieval import GitHubArtifactRetriever
from github_client import ConfirmedArtifactMissing, GitHubClient, GitHubConfig, GitHubError

TREE = "a" * 40
BLOB = "b" * 40


def response(payload):
    result = Mock()
    result.__enter__ = Mock(return_value=result)
    result.__exit__ = Mock(return_value=False)
    result.read.return_value = json.dumps(payload).encode()
    return result


def client_with(*payloads):
    opener = Mock()
    opener.open.side_effect = [p if isinstance(p, Exception) else response(p) for p in payloads]
    return GitHubClient(GitHubConfig("fake-secret", "example/project"), opener), opener


def entry(name="sbom.json", kind="blob", mode="100644", sha=BLOB):
    return {"path": name, "type": kind, "mode": mode, "sha": sha}


def tree(*entries, truncated=False):
    return {"sha": TREE, "tree": list(entries), "truncated": truncated}


def blob(content):
    return {"sha": BLOB, "size": len(content), "encoding": "base64",
            "content": base64.b64encode(content).decode() + "\n"}


@pytest.mark.parametrize("content", [b"", b"\x00\xff\xfe", "café 日本語\r\n  \r\n".encode()])
def test_original_bytes_headers_timeout_and_explicit_ref(content):
    client, opener = client_with(tree(entry()), blob(content))
    assert client.get_file("sbom.json", "refs/tags/release?x#y") == content
    calls = opener.open.call_args_list
    assert calls[0].args[0].full_url.endswith("/git/trees/refs%2Ftags%2Frelease%3Fx%23y")
    assert calls[1].args[0].full_url.endswith("/git/blobs/" + BLOB)
    for call in calls:
        headers = {k.lower(): v for k, v in call.args[0].header_items()}
        assert headers["authorization"] == "Bearer fake-secret"
        assert headers["accept"] == "application/vnd.github+json"
        assert headers["x-github-api-version"] == "2022-11-28"
        assert call.kwargs["timeout"] == 10.0
        assert call.args[0].get_method() == "GET"


def test_nested_paths_use_immutable_tree_ids():
    client, opener = client_with(tree(entry("artifacts", "tree", "040000", TREE)), tree(entry()), blob(b"original"))
    assert client.get_file("artifacts/sbom.json", "main") == b"original"
    assert opener.open.call_args_list[1].args[0].full_url.endswith("/git/trees/" + TREE)


def test_complete_tree_confirms_absence():
    client, opener = client_with(tree())
    with pytest.raises(ConfirmedArtifactMissing):
        client.get_file("sbom.json", "main")
    assert opener.open.call_count == 1
    assert AdapterMissing is ConfirmedArtifactMissing


def test_real_client_and_adapter_missing_contract():
    client, _ = client_with(*(tree() for _ in range(6)))
    results = GitHubArtifactRetriever(client, revision="fixed").retrieve("p", "v")
    assert all(result.outcome == "MISSING" for result in results)


@pytest.mark.parametrize("phase", ["tree", "blob"])
@pytest.mark.parametrize("status,headers,code", [
    (404, {}, "repository_access"), (401, {}, "authentication"), (403, {}, "permission"),
    (403, {"X-RateLimit-Remaining": "0"}, "rate_limited"), (429, {}, "rate_limited"),
    (302, {}, "redirect"), (500, {}, "upstream"),
])
def test_http_errors_never_mean_missing(phase, status, headers, code):
    body = io.BytesIO(b"fake-secret private upstream response")
    error = HTTPError("https://api.github.com/private", status, "fake-secret", headers, body)
    client, _ = client_with(*([tree(entry())] if phase == "blob" else []), error)
    with pytest.raises(GitHubError) as captured:
        client.get_file("sbom.json", "main")
    assert captured.value.code == code
    assert "fake-secret" not in str(captured.value)
    assert "private" not in str(captured.value)
    assert body.closed


@pytest.mark.parametrize("error,code", [(TimeoutError("fake-secret"), "timeout"),
                                        (URLError("fake-secret"), "connection")])
def test_network_failures(error, code):
    client, _ = client_with(error)
    with pytest.raises(GitHubError) as captured:
        client.get_file("sbom.json", "main")
    assert captured.value.code == code
    assert "fake-secret" not in str(captured.value)


@pytest.mark.parametrize("payload", [
    tree(truncated=True), {"sha": TREE, "tree": []}, tree(entry(), entry()), tree({"path": "x"}),
    tree(entry(kind="blob", mode="120000")), tree(entry(kind="commit", mode="160000")),
    tree(entry(kind="tree", mode="040000")), tree(entry(sha="bad")),
    {"sha": TREE, "tree": "bad", "truncated": False},
])
def test_malformed_or_unsupported_tree_cannot_prove_absence(payload):
    client, _ = client_with(payload)
    with pytest.raises(GitHubError) as captured:
        client.get_file("sbom.json", "main")
    assert captured.value.code == "upstream_response"


@pytest.mark.parametrize("change", [
    {"content": "not base64!"}, {"encoding": "utf-8"}, {"size": 100}, {"size": True},
    {"sha": TREE}, {"content": None}, {"size": -1}, {"size": 1_048_577},
])
def test_malformed_blob_is_safe(change):
    payload = blob(b"original")
    payload.update(change)
    client, _ = client_with(tree(entry()), payload)
    with pytest.raises(GitHubError) as captured:
        client.get_file("sbom.json", "main")
    assert captured.value.code == "upstream_response"


@pytest.mark.parametrize("body", [b"not JSON", b"\xff", b"x" * 1_048_577])
def test_response_limit_and_invalid_json(body):
    upstream = response({})
    upstream.read.return_value = body
    opener = Mock()
    opener.open.return_value = upstream
    client = GitHubClient(GitHubConfig("fake-secret", "example/project"), opener)
    with pytest.raises(GitHubError) as captured:
        client.get_file("sbom.json", "main")
    assert captured.value.code == "upstream_response"
    upstream.read.assert_called_once_with(1_048_577)
    upstream.__exit__.assert_called_once()


@pytest.mark.parametrize("path,ref", [
    ("../secret", "main"), ("/absolute", "main"), ("a//b", "main"), ("a\\b", "main"),
    ("%2e%2e/file", "main"), ("file?x", "main"), ("file", ""), ("file", None), ("file", "x\n"),
])
def test_invalid_arguments_never_send_requests(path, ref):
    client, opener = client_with()
    with pytest.raises(GitHubError) as captured:
        client.get_file(path, ref)
    assert captured.value.code == "configuration"
    opener.open.assert_not_called()
