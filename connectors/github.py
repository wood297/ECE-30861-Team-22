"""Fetch one pinned artifact from a GitHub repository."""

import json
from typing import Any, Mapping

from connectors.base import (LIST, SECRET, BaseConnector, FetchContext, FetchResult, Field, MissingSecret,
                             SettingsError, result_for_http_error, result_for_status)
from github_client import (PATH_SEGMENT_PATTERN, REPOSITORY_PATTERN, SHA_PATTERN, ConfirmedArtifactMissing,
                           GitHubError, read_git_file, valid_path, valid_repository, valid_sha)
from safe_http import SafeHttpError
from tenant_store import Connection

_API_HOST = "api.github.com"


class _FetchFailure(Exception):
    def __init__(self, result: FetchResult):
        super().__init__(result.detail)
        self.result = result


class GitHubConnector(BaseConnector):
    type = "github"
    display_name = "GitHub"
    settings_fields = (
        Field("repository", "Repository", pattern=REPOSITORY_PATTERN, help="owner/repository"),
        Field("token", "Fine-grained read-only token", kind=SECRET),
    )
    ref_fields = (
        Field("revision", "Commit SHA", pattern=SHA_PATTERN),
        Field("paths", "Artifact paths, tried in order", kind=LIST,
              pattern=rf"{PATH_SEGMENT_PATTERN}(/{PATH_SEGMENT_PATTERN})*",
              help="e.g. artifacts/{product}/{release}/sbom.json"),
    )

    def validate_settings(self, settings: Mapping[str, Any]) -> dict[str, Any]:
        clean = super().validate_settings(settings)
        if not valid_repository(clean["repository"]):
            raise SettingsError("repository", "must be owner/repository")
        return clean

    def validate_ref(self, ref: Mapping[str, Any]) -> dict[str, Any]:
        clean = super().validate_ref(ref)
        if not valid_sha(clean["revision"]):
            raise SettingsError("revision", "must be a lowercase 40-character commit SHA")
        if any(".." in path for path in clean["paths"]):
            raise SettingsError("paths", "must not contain '..'")
        return clean

    def allowed_hosts(self, settings: Mapping[str, Any]) -> list[str]:
        return [_API_HOST]

    def fetch(self, conn: Connection, ref: Mapping[str, Any], ctx: FetchContext) -> FetchResult:
        revision = ref["revision"]
        paths = ref["paths"]
        if not all(valid_path(path) for path in paths):
            return FetchResult.source_error("GitHub requires safe relative artifact paths.")
        try:
            token = ctx.secret().reveal()
        except MissingSecret:
            return FetchResult.auth_failed("The GitHub connection has no token.")
        if not token or any(not 33 <= ord(char) <= 126 for char in token):
            return FetchResult.auth_failed("The GitHub connection token is invalid.")
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "cra-documentation-service",
        }
        base = f"/repos/{conn.settings['repository']}/git/"

        def get(path: str) -> dict[str, Any]:
            response = ctx.http.get(f"https://{_API_HOST}{path}", headers=headers)
            if response.status == 403 and (response.headers.get("x-ratelimit-remaining") == "0"
                                           or "retry-after" in response.headers):
                raise _FetchFailure(FetchResult.unavailable("GitHub rate limit reached."))
            # GitHub can hide repositories and Git objects from tokens without Contents access.
            if response.status == 404:
                raise _FetchFailure(FetchResult.auth_failed("GitHub could not locate the repository or Git object."))
            if response.status == 410:
                raise _FetchFailure(FetchResult.source_error("GitHub reported that the Git resource is gone."))
            failure = result_for_status(response.status, self.display_name)
            if failure:
                raise _FetchFailure(failure)
            try:
                payload = json.loads(response.body)
            except (ValueError, RecursionError):
                raise GitHubError("upstream_response", "GitHub returned invalid JSON.") from None
            if not isinstance(payload, dict):
                raise GitHubError("upstream_response", "GitHub returned invalid Git metadata.")
            return payload

        try:
            commit = get(base + "commits/" + revision)
            tree = commit.get("tree")
            if commit.get("sha") != revision or not isinstance(tree, dict) or not valid_sha(tree.get("sha")):
                return FetchResult.source_error("GitHub returned invalid commit metadata.")
            for path in paths:
                try:
                    data = read_git_file(get, base, path, tree["sha"], exact_root=True)
                except ConfirmedArtifactMissing:
                    continue
                return FetchResult.retrieved(data, path.rsplit("/", 1)[-1], version=revision)
        except SafeHttpError as error:
            return result_for_http_error(error, self.display_name)
        except _FetchFailure as error:
            return error.result
        except GitHubError as error:
            return FetchResult.source_error(str(error))
        return FetchResult.not_found("GitHub has none of the pinned paths.")
