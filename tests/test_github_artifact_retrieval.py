from unittest.mock import Mock

import pytest

from artifact_formats import validate_artifact
from github_artifact_retrieval import ConfirmedArtifactMissing, GitHubArtifactRetriever
from github_client import GitHubError


def client_with(files):
    def get_file(path, ref):
        value = files.get((path, ref), ConfirmedArtifactMissing("absent"))
        if isinstance(value, Exception):
            raise value
        return value
    return Mock(get_file=Mock(side_effect=get_file))


def path(product, release, filename):
    return f"artifacts/{product}/{release}/{filename}"


def test_success_preserves_bytes_associations_and_explicit_ref():
    sbom = b'{"bomFormat":"CycloneDX","specVersion":"1.6"}\r\n'
    instructions = "# Install café 日本語\r\n  \r\n".encode()
    client = client_with({(path("prd-1", "v2", "sbom.json"), "commit-sha"): sbom,
                          (path("prd-1", "v2", "user_instructions.md"), "commit-sha"): instructions})
    results = GitHubArtifactRetriever(client, revision="commit-sha").retrieve("prd-1", "v2")
    assert [r.category for r in results] == ["SBOM", "User Instructions"]
    assert [r.original_bytes for r in results] == [sbom, instructions]
    assert results[0].original_bytes is sbom
    for result in results:
        assert result.outcome == "RETRIEVED"
        assert (result.product_id, result.release_version, result.revision) == ("prd-1", "v2", "commit-sha")
        assert validate_artifact(result.original_bytes, result.declared_format).valid
    assert all(call.kwargs == {"ref": "commit-sha"} for call in client.get_file.call_args_list)


@pytest.mark.parametrize("filename,fmt,content", [
    ("sbom.json", "cyclonedx-json", b'{"bomFormat":"CycloneDX","specVersion":"1.5"}'),
    ("sbom.xml", "cyclonedx-xml", b'<bom xmlns="http://cyclonedx.org/schema/bom/1.5"/>'),
    ("sbom.md", "markdown", b"# SBOM\r\n"),
    ("sbom.html", "html", b"<html><body>SBOM</body></html>"),
])
def test_supported_sbom_candidates(filename, fmt, content):
    client = client_with({(path("p", "v", filename), "main"): content})
    result = GitHubArtifactRetriever(client, revision="main").retrieve("p", "v")[0]
    assert result.declared_format == fmt
    assert result.original_bytes == content


def test_html_instructions():
    client = client_with({(path("p", "v", "user_instructions.html"), "r"): b"<p>Install</p>"})
    result = GitHubArtifactRetriever(client, revision="r").retrieve("p", "v")[1]
    assert result.outcome == "RETRIEVED"
    assert result.declared_format == "html"


def test_confirmed_absence_only():
    client = client_with({})
    results = GitHubArtifactRetriever(client, revision="r").retrieve("p", "v")
    assert all(r.outcome == "MISSING" and r.source_failure_detail is None for r in results)
    assert client.get_file.call_count == 6


@pytest.mark.parametrize("code", ["authentication", "permission", "rate_limited", "timeout", "connection",
                                  "repository_access", "upstream_response", "secret-token"])
def test_source_failure_is_not_missing_and_other_artifact_survives(code):
    client = client_with({(path("p", "v", "sbom.json"), "r"): GitHubError(code, "sensitive-token body"),
                          (path("p", "v", "user_instructions.md"), "r"): b"# Install"})
    failed, success = GitHubArtifactRetriever(client, revision="r").retrieve("p", "v")
    assert failed.outcome == "INACCESSIBLE"
    assert failed.error_code == (code if code != "secret-token" else "source_failure")
    assert "sensitive-token" not in repr(failed)
    assert "secret-token" not in repr(failed)
    assert success.outcome == "RETRIEVED"
    assert client.get_file.call_count == 2


@pytest.mark.parametrize("content", [b"", b"{malformed", b"\xff", b"<html>wrong format</html>"])
def test_retrieval_does_not_validate_or_fallback_on_invalid_content(content):
    client = client_with({(path("p", "v", "sbom.json"), "r"): content})
    result = GitHubArtifactRetriever(client, revision="r").retrieve("p", "v")[0]
    assert result.outcome == "RETRIEVED"
    assert result.original_bytes is content
    assert not validate_artifact(content, result.declared_format).valid


@pytest.mark.parametrize("identifier", ["", "..", "../x", "a/../b", "/etc", "a\\b", "%2e%2e", "a?x", "a#x",
                                        "a\n", "a b", None])
@pytest.mark.parametrize("field", ["product", "release"])
def test_invalid_identifiers_never_call_client(identifier, field):
    client = Mock()
    retriever = GitHubArtifactRetriever(client, revision="r")
    with pytest.raises(ValueError):
        retriever.retrieve(identifier if field == "product" else "p", identifier if field == "release" else "v")
    client.get_file.assert_not_called()


@pytest.mark.parametrize("revision", [None, "", " ", "r\n"])
def test_explicit_revision_required(revision):
    with pytest.raises(ValueError):
        GitHubArtifactRetriever(Mock(), revision=revision)


def test_release_and_product_isolation():
    files = {(path(p, v, "sbom.json"), "fixed"): f"{p}-{v}".encode()
             for p, v in [("p1", "v1"), ("p1", "v2"), ("p2", "v1")]}
    retriever = GitHubArtifactRetriever(client_with(files), revision="fixed")
    for p, v in [("p1", "v1"), ("p1", "v2"), ("p2", "v1")]:
        assert retriever.retrieve(p, v)[0].original_bytes == f"{p}-{v}".encode()


@pytest.mark.parametrize("error", [TimeoutError("secret"), OSError("secret")])
def test_unexpected_client_errors_are_sanitized(error):
    client = Mock()
    client.get_file.side_effect = error
    results = GitHubArtifactRetriever(client, revision="r").retrieve("p", "v")
    assert all(r.outcome == "INACCESSIBLE" and "secret" not in repr(r) for r in results)


def test_client_must_return_bytes():
    client = Mock()
    client.get_file.return_value = "text"
    results = GitHubArtifactRetriever(client, revision="r").retrieve("p", "v")
    assert all(r.error_code == "upstream_response" for r in results)


def test_custom_safe_filenames():
    candidates = (("SBOM", (("bom.xml", "cyclonedx-xml"),)),
                  ("User Instructions", (("manual.html", "html"),)))
    client = client_with({(path("p", "v", "manual.html"), "r"): b"<p>Manual</p>"})
    result = GitHubArtifactRetriever(client, revision="r", candidates=candidates).retrieve("p", "v")[1]
    assert result.path.endswith("/manual.html")


def test_custom_candidates_cannot_escape_directory_or_add_categories():
    for category, filename in [("SBOM", "../sbom.json"), ("Product Description", "description.md")]:
        with pytest.raises(ValueError):
            GitHubArtifactRetriever(Mock(), revision="r", candidates=((category, ((filename, "markdown"),)),))
