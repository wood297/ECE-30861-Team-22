"""Contract checks for authentication, error isolation, and credential handling."""

import io
import json
from unittest.mock import Mock
from urllib.error import HTTPError, URLError
from urllib.request import Request

import pytest

import github_auth_check
from github_client import GitHubClient, GitHubConfig, GitHubError, _NoRedirect

TOKEN = "fake-test-token"
REPOSITORY = "example/project"


def response(payload):
    result = Mock()
    result.__enter__ = Mock(return_value=result)
    result.__exit__ = Mock(return_value=False)
    result.read.return_value = json.dumps(payload).encode()
    return result


def client_with(*results):
    opener = Mock()
    opener.open.side_effect = results
    return GitHubClient(GitHubConfig(TOKEN, REPOSITORY), opener), opener


def test_verifies_user_before_repository_with_authenticated_requests():
    client, opener = client_with(
        response({"login": "tester"}), response({"full_name": "example/project"})
    )
    assert client.verify_access() == {
        "status": "connected", "user": "tester", "repository": REPOSITORY
    }
    assert opener.open.call_count == 2
    assert [call.args[0].full_url for call in opener.open.call_args_list] == [
        "https://api.github.com/user", "https://api.github.com/repos/example/project"
    ]
    for call in opener.open.call_args_list:
        request = call.args[0]
        assert request.get_method() == "GET"
        headers = {key.lower(): value for key, value in request.header_items()}
        assert headers["authorization"] == "Bearer " + TOKEN
        assert headers["accept"] == "application/vnd.github+json"
        assert headers["x-github-api-version"] == "2022-11-28"
        assert call.kwargs["timeout"] == 10.0


def test_token_is_excluded_from_config_representation():
    assert TOKEN not in repr(GitHubConfig(TOKEN, REPOSITORY))


@pytest.mark.parametrize("token", ["", " ", "fake\nheader", "fake token", None])
def test_rejects_invalid_tokens(token):
    with pytest.raises(GitHubError) as captured:
        GitHubConfig(token, REPOSITORY)
    assert captured.value.code == "configuration"


@pytest.mark.parametrize("repository", ["", "project", "https://github.com/a/b", "a/b/c", "a/..", "a/b?x=1", "a/b#fragment", None])
def test_rejects_invalid_repository_configuration(repository):
    with pytest.raises(GitHubError):
        GitHubConfig(TOKEN, repository)


@pytest.mark.parametrize("timeout", [0, -1, 61, float("nan"), float("inf")])
def test_timeout_is_bounded(timeout):
    with pytest.raises(GitHubError):
        GitHubConfig(TOKEN, REPOSITORY, timeout)


def test_environment_configuration(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    monkeypatch.setenv("GITHUB_REPOSITORY", REPOSITORY)
    config = GitHubConfig.from_environment()
    assert config.token == TOKEN
    assert config.repository == REPOSITORY


def test_missing_environment_fails_before_network(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    with pytest.raises(GitHubError):
        GitHubConfig.from_environment()


@pytest.mark.parametrize("status,headers,code", [
    (401, {}, "authentication"),
    (403, {}, "permission"),
    (403, {"X-RateLimit-Remaining": "0"}, "rate_limited"),
    (403, {"Retry-After": "60"}, "rate_limited"),
    (429, {}, "rate_limited"),
    (404, {}, "repository_access"),
    (301, {}, "redirect"),
    (500, {}, "upstream"),
])
def test_http_failures_are_safe_and_stop_processing(status, headers, code):
    body = io.BytesIO(TOKEN.encode())
    error = HTTPError("https://api.github.com/user", status, TOKEN, headers, body)
    client, opener = client_with(error)
    with pytest.raises(GitHubError) as captured:
        client.verify_access()
    assert captured.value.code == code
    assert TOKEN not in str(captured.value)
    assert opener.open.call_count == 1
    assert body.closed


@pytest.mark.parametrize("error,code", [
    (TimeoutError(TOKEN), "timeout"),
    (URLError(TOKEN), "connection"),
])
def test_network_failures_are_sanitized(error, code):
    client, _ = client_with(error)
    with pytest.raises(GitHubError) as captured:
        client.verify_access()
    assert captured.value.code == code
    assert TOKEN not in str(captured.value)


@pytest.mark.parametrize("payload", [[], None, {"login": ""}, {"login": 123}, {}])
def test_invalid_user_response_does_not_proceed_to_repository(payload):
    client, opener = client_with(response(payload))
    with pytest.raises(GitHubError) as captured:
        client.verify_access()
    assert captured.value.code == "upstream_response"
    assert opener.open.call_count == 1


@pytest.mark.parametrize("body", [b"not-json", b"\xff", b"x" * 1_048_577], ids=["invalid-json", "invalid-utf8", "oversized"])
def test_malformed_or_oversized_body(body):
    upstream = response({})
    upstream.read.return_value = body
    client, _ = client_with(upstream)
    with pytest.raises(GitHubError) as captured:
        client.verify_access()
    assert captured.value.code == "upstream_response"
    upstream.__exit__.assert_called_once()


def test_repository_access_checked_after_successful_authentication():
    error = HTTPError("https://api.github.com/repos/example/project", 404, "hidden", {}, io.BytesIO())
    client, opener = client_with(response({"login": "tester"}), error)
    with pytest.raises(GitHubError) as captured:
        client.verify_access()
    assert captured.value.code == "repository_access"
    assert opener.open.call_count == 2


def test_unexpected_repository_rejected():
    client, _ = client_with(response({"login": "tester"}), response({"full_name": "other/project"}))
    with pytest.raises(GitHubError):
        client.verify_access()


def test_redirect_handler_does_not_forward_credentials():
    request = Request("https://api.github.com/user", headers={"Authorization": TOKEN})
    assert _NoRedirect().redirect_request(request, None, 302, "Moved", {}, "https://other.test") is None


def test_cli_success(monkeypatch, capsys):
    fake = Mock()
    fake.verify_access.return_value = {"status": "connected"}
    monkeypatch.setattr(github_auth_check.GitHubConfig, "from_environment", lambda: Mock())
    monkeypatch.setattr(github_auth_check, "GitHubClient", lambda config: fake)
    assert github_auth_check.main() == 0
    assert json.loads(capsys.readouterr().out) == {"status": "connected"}


def test_cli_configuration_error(monkeypatch, capsys):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert github_auth_check.main() == 1
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "error"
    assert output["code"] == "configuration"
    assert TOKEN not in json.dumps(output)
