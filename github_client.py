"""GitHub authentication foundation; no artifact ingestion is performed here."""

import json
import os
import re
import socket
from dataclasses import dataclass, field
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener


class GitHubError(Exception):
    """A safe diagnostic that never includes upstream bodies or credentials."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class GitHubConfig:
    token: str = field(repr=False)
    repository: str
    timeout: float = 10.0

    def __post_init__(self):
        if not isinstance(self.token, str) or not self.token.strip():
            raise GitHubError("configuration", "Set GITHUB_TOKEN before verifying access.")
        if any(character.isspace() for character in self.token):
            raise GitHubError("configuration", "GITHUB_TOKEN must not contain whitespace.")
        if not isinstance(self.repository, str) or not re.fullmatch(
            r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?/[A-Za-z0-9_.-]+",
            self.repository,
        ) or self.repository.split("/")[-1] in {".", ".."}:
            raise GitHubError("configuration", "Set GITHUB_REPOSITORY to owner/repository.")
        if not 0 < self.timeout <= 60:
            raise GitHubError("configuration", "GitHub timeout must be greater than 0 and at most 60 seconds.")

    @classmethod
    def from_environment(cls):
        return cls(
            token=os.environ.get("GITHUB_TOKEN", ""),
            repository=os.environ.get("GITHUB_REPOSITORY", ""),
        )


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward an Authorization header to a redirected destination.
        return None


class GitHubClient:
    """Read-only client restricted to GitHub's HTTPS API."""

    def __init__(self, config, opener=None):
        self.config = config
        self._opener = opener if opener is not None else build_opener(_NoRedirect())

    def _get(self, path):
        request = Request(
            "https://api.github.com" + path,
            headers={
                "Authorization": "Bearer " + self.config.token,
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "cra-documentation-service",
            },
            method="GET",
        )
        try:
            with self._opener.open(request, timeout=self.config.timeout) as response:
                # Bound memory consumption and reject malformed upstream responses.
                body = response.read(1_048_577)
                if len(body) > 1_048_576:
                    raise GitHubError("upstream_response", "GitHub response exceeded the size limit.")
                result = json.loads(body)
                if not isinstance(result, dict):
                    raise GitHubError("upstream_response", "GitHub returned an unexpected response.")
                return result
        except HTTPError as error:
            status = error.code
            limited = status == 429 or (status == 403 and (
                error.headers.get("X-RateLimit-Remaining") == "0"
                or error.headers.get("Retry-After") is not None
            ))
            error.close()
            if limited:
                raise GitHubError("rate_limited", "GitHub rate limit reached; retry later.") from None
            if status == 401:
                raise GitHubError("authentication", "GitHub rejected the token; check its value and expiration.") from None
            if status == 403:
                raise GitHubError("permission", "GitHub denied access; check token permissions and organization approval.") from None
            if status == 404:
                raise GitHubError("repository_access", "Repository not found or unavailable to this token.") from None
            if 300 <= status < 400:
                raise GitHubError("redirect", "GitHub redirected the request; verify the repository name.") from None
            raise GitHubError("upstream", "GitHub request failed; try again later.") from None
        except (TimeoutError, socket.timeout):
            raise GitHubError("timeout", "GitHub request timed out; retry later.") from None
        except URLError:
            raise GitHubError("connection", "Could not connect securely to GitHub; check network access.") from None
        except (ValueError, UnicodeError):
            raise GitHubError("upstream_response", "GitHub returned invalid JSON.") from None

    def verify_access(self):
        """Validate the token, then check repository metadata visibility.

        Metadata access does not establish permission to download repository files.
        """
        user = self._get("/user")
        if not isinstance(user.get("login"), str) or not user["login"]:
            raise GitHubError("upstream_response", "GitHub did not return an authenticated user.")
        repository = self._get("/repos/" + self.config.repository)
        name = repository.get("full_name")
        if not isinstance(name, str) or name.casefold() != self.config.repository.casefold():
            raise GitHubError("upstream_response", "GitHub returned an unexpected repository.")
        return {"status": "connected", "user": user["login"], "repository": name}
