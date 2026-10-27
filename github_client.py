"""Read-only GitHub authentication and bounded repository-file retrieval."""

import base64
import binascii
import json
import os
import re
import socket
from dataclasses import dataclass, field
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import HTTPRedirectHandler, Request, build_opener


class GitHubError(Exception):
    """A safe diagnostic that never includes upstream bodies or credentials."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class ConfirmedArtifactMissing(Exception):
    """A complete Git tree established absence at the requested revision."""


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
                raise GitHubError(
                    "authentication", "GitHub rejected the token; check its value and expiration."
                ) from None
            if status == 403:
                raise GitHubError(
                    "permission", "GitHub denied access; check token permissions and organization approval."
                ) from None
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

    def get_file(self, path: str, ref: str) -> bytes:
        """Read a regular Git blob without decoding or transforming artifact bytes.

        Resolve the explicit ref through a nonrecursive tree, then follow immutable
        tree/blob SHAs. Only absence in a complete, validated tree raises
        ConfirmedArtifactMissing. All HTTP 404s retain the existing ambiguous access
        error. Symlinks/submodules are rejected, never followed. Each JSON response
        retains _get's 1 MiB cap (base64 overhead reduces maximum artifact size).
        No retries or caching are introduced; use a commit SHA for repeatable calls.
        """
        if not isinstance(path, str) or not path or any(
            not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", part) or ".." in part
            for part in path.split("/")
        ):
            raise GitHubError("configuration", "File path must contain safe relative path segments.")
        if not isinstance(ref, str) or not ref.strip() or any(ord(c) < 32 or ord(c) == 127 for c in ref):
            raise GitHubError("configuration", "An explicit Git revision is required.")
        base = "/repos/" + self.config.repository + "/git/"

        def invalid():
            return GitHubError("upstream_response", "GitHub returned invalid file metadata.")

        def valid_sha(value):
            return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{40}", value) is not None

        tree_ref = quote(ref, safe="")
        parts = path.split("/")
        for index, part in enumerate(parts):
            tree = self._get(base + "trees/" + tree_ref)
            if not valid_sha(tree.get("sha")) or tree.get("truncated") is not False:
                raise invalid()
            if index and tree["sha"] != tree_ref:
                raise invalid()
            entries = tree.get("tree")
            if not isinstance(entries, list):
                raise invalid()
            names = set()
            selected = None
            modes = {"tree": {"040000"}, "blob": {"100644", "100755", "120000"}, "commit": {"160000"}}
            for entry in entries:
                if not isinstance(entry, dict):
                    raise invalid()
                name = entry.get("path")
                kind = entry.get("type")
                if (not isinstance(name, str) or not name or "/" in name or name in names
                        or not isinstance(kind, str) or kind not in modes
                        or not isinstance(entry.get("mode"), str)
                        or entry.get("mode") not in modes[kind] or not valid_sha(entry.get("sha"))):
                    raise invalid()
                names.add(name)
                if name == part:
                    selected = entry
            if selected is None:
                raise ConfirmedArtifactMissing("File absent from the requested Git tree.")
            if index < len(parts) - 1:
                if selected["type"] != "tree":
                    raise invalid()
                tree_ref = selected["sha"]
                continue
            if selected["type"] != "blob" or selected["mode"] not in {"100644", "100755"}:
                raise invalid()
            blob = self._get(base + "blobs/" + selected["sha"])
            if (blob.get("sha") != selected["sha"] or blob.get("encoding") != "base64"
                    or not isinstance(blob.get("content"), str) or type(blob.get("size")) is not int
                    or not 0 <= blob["size"] <= 1_048_576):
                raise invalid()
            try:
                data = base64.b64decode(blob["content"].replace("\n", "").replace("\r", ""), validate=True)
            except (ValueError, binascii.Error):
                raise invalid() from None
            if len(data) != blob["size"]:
                raise invalid()
            return data
        raise invalid()

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
