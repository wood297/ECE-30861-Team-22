"""Isolated GitHub repository-file ingestion; no pipeline or credential setup.

The provisional directory is artifacts/{product_id}/{release_version}/. Default
candidate names below are provisional, ordered preferences, not discovery rules
from the shared specification. The first retrieved candidate wins, even if its
content later fails validation. Only confirmed absence permits trying another name.

Client contract: get_file(path, ref) returns original bytes, raises
ConfirmedArtifactMissing only for authoritative absence, or GitHubError for source
failures. An HTTP 404 alone is NOT authoritative absence. This module does not call
private client methods, interpret upstream bodies, or perform format validation.
"""

import re
from dataclasses import dataclass
from typing import Protocol

from artifact_formats import SUPPORTED_FORMATS
from github_client import ConfirmedArtifactMissing, GitHubError


DEFAULT_CANDIDATES = (
    ("SBOM", (("sbom.json", "cyclonedx-json"), ("sbom.xml", "cyclonedx-xml"),
              ("sbom.md", "markdown"), ("sbom.html", "html"))),
    ("User Instructions", (("user_instructions.md", "markdown"), ("user_instructions.html", "html"))),
)


class FileClient(Protocol):
    def get_file(self, path: str, ref: str) -> bytes:
        ...


@dataclass(frozen=True)
class RetrievalResult:
    category: str
    product_id: str
    release_version: str
    revision: str
    outcome: str
    path: str | None = None
    declared_format: str | None = None
    original_bytes: bytes | None = None
    error_code: str | None = None
    source_failure_detail: str | None = None
    external_source: str = "github"


def _identifier(value):
    # Conservative single-segment IDs: also excludes encoded separators, query
    # delimiters, backslashes, whitespace and control characters.
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", value) and ".." not in value


class GitHubArtifactRetriever:
    def __init__(self, client: FileClient, *, revision: str, candidates=DEFAULT_CANDIDATES):
        if not isinstance(revision, str) or not revision.strip() or any(
            ord(character) < 32 or ord(character) == 127 for character in revision
        ):
            raise ValueError("An explicit Git revision is required")
        configured = []
        seen = set()
        for category, entries in candidates:
            if category not in {"SBOM", "User Instructions"} or category in seen:
                raise ValueError("Configure only SBOM and User Instructions, once each")
            seen.add(category)
            copied = tuple(entries)
            if not copied or any(not _identifier(name) or fmt not in SUPPORTED_FORMATS for name, fmt in copied):
                raise ValueError("Candidates require safe filenames and supported declared formats")
            configured.append((category, copied))
        if seen != {"SBOM", "User Instructions"}:
            raise ValueError("Both GitHub categories must be configured")
        self.client = client
        self.revision = revision
        self.candidates = tuple(configured)

    def retrieve(self, product_id: str, release_version: str) -> tuple[RetrievalResult, ...]:
        """Return both independent outcomes; RETRIEVED does not imply valid content."""
        if not _identifier(product_id) or not _identifier(release_version):
            raise ValueError("product_id and release_version must be safe single path segments")
        results = []
        for category, entries in self.candidates:
            results.append(self._retrieve_one(product_id, release_version, category, entries))
        return tuple(results)

    def _retrieve_one(self, product, release, category, entries):
        common = dict(category=category, product_id=product, release_version=release, revision=self.revision)
        for filename, declared_format in entries:
            path = f"artifacts/{product}/{release}/{filename}"
            try:
                content = self.client.get_file(path, ref=self.revision)
                if not isinstance(content, bytes):
                    return RetrievalResult(**common, outcome="INACCESSIBLE", path=path,
                                           error_code="upstream_response",
                                           source_failure_detail="GitHub client did not return artifact bytes.")
            except ConfirmedArtifactMissing:
                continue
            except Exception as error:
                # Never surface exception messages, URLs, tokens or upstream bodies.
                code = error.code if isinstance(error, GitHubError) else "source_failure"
                safe_codes = {"configuration", "authentication", "permission", "rate_limited", "timeout",
                              "connection", "repository_access", "redirect", "upstream", "upstream_response"}
                if code not in safe_codes:
                    code = "source_failure"
                return RetrievalResult(**common, outcome="INACCESSIBLE", path=path, error_code=code,
                                       source_failure_detail=f"GitHub artifact retrieval failed ({code}).")
            return RetrievalResult(**common, outcome="RETRIEVED", path=path,
                                   declared_format=declared_format, original_bytes=content)
        return RetrievalResult(**common, outcome="MISSING")
