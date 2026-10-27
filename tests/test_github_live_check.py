"""Offline verification of the opt-in script; never contacts GitHub."""

import json
from unittest.mock import Mock

import pytest

import github_live_check
from github_client import ConfirmedArtifactMissing, GitHubError


@pytest.fixture
def configured(tmp_path, monkeypatch):
    values = {
        "GITHUB_TOKEN": "fake-sensitive-token", "GITHUB_REPOSITORY": "example/project",
        "GITHUB_TEST_REVISION": "a" * 40, "GITHUB_TEST_PRODUCT_ID": "p",
        "GITHUB_TEST_RELEASE_VERSION": "v1", "GITHUB_TEST_MISSING_RELEASE": "absent",
        "GITHUB_TEST_EXPECTED_ROOT": str(tmp_path),
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    folder = tmp_path / "artifacts" / "p" / "v1"
    folder.mkdir(parents=True)
    files = {"sbom.md": b"# SBOM\r\n", "user_instructions.md": "# Install café\r\n".encode()}
    for filename, content in files.items():
        (folder / filename).write_bytes(content)

    def get_file(path, ref):
        assert ref == "a" * 40
        if path.startswith("artifacts/p/v1/") and path.rsplit("/", 1)[-1] in files:
            return files[path.rsplit("/", 1)[-1]]
        raise ConfirmedArtifactMissing()

    client = Mock(get_file=Mock(side_effect=get_file))
    factory = Mock(return_value=client)
    monkeypatch.setattr(github_live_check, "GitHubClient", factory)
    return files, client, factory


def test_success_and_confirmed_missing(configured, capsys):
    assert github_live_check.main() == 0
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [row["retrieval_status"] for row in rows] == ["RETRIEVED", "RETRIEVED", "MISSING", "MISSING"]
    assert all(set(row) == {"category", "retrieval_status", "source_path", "revision", "byte_count"} for row in rows)
    assert rows[0]["byte_count"] == len(configured[0]["sbom.md"])


@pytest.mark.parametrize("code", ["authentication", "permission", "repository_access"])
def test_access_failures_are_distinct_and_redacted(configured, capsys, code):
    configured[1].get_file.side_effect = GitHubError(code, "fake-sensitive-token private body")
    assert github_live_check.main() == 1
    output = capsys.readouterr().out
    assert "fake-sensitive-token" not in output and "private body" not in output
    assert all(json.loads(line)["retrieval_status"] == "INACCESSIBLE:" + code for line in output.splitlines())


@pytest.mark.parametrize("variable", ["GITHUB_TOKEN", "GITHUB_REPOSITORY", "GITHUB_TEST_REVISION",
                                      "GITHUB_TEST_PRODUCT_ID", "GITHUB_TEST_RELEASE_VERSION",
                                      "GITHUB_TEST_MISSING_RELEASE", "GITHUB_TEST_EXPECTED_ROOT"])
def test_missing_configuration_cannot_start_live_client(configured, monkeypatch, capsys, variable):
    monkeypatch.delenv(variable)
    assert github_live_check.main() == 2
    configured[2].assert_not_called()
    assert capsys.readouterr().out == ""


def test_mismatching_bytes_fail_without_printing_content(configured, capsys):
    original = configured[1].get_file.side_effect

    def changed(path, ref):
        data = original(path, ref)
        return data + b"private changed content"

    configured[1].get_file.side_effect = changed
    assert github_live_check.main() == 1
    assert "private changed content" not in capsys.readouterr().out


def test_missing_fixture_blocks_requests(configured, monkeypatch):
    monkeypatch.setenv("GITHUB_TEST_RELEASE_VERSION", "not-present")
    assert github_live_check.main() == 2
    configured[2].assert_not_called()


def test_branch_is_not_a_commit_sha(configured, monkeypatch):
    monkeypatch.setenv("GITHUB_TEST_REVISION", "main")
    assert github_live_check.main() == 2
    configured[2].assert_not_called()
