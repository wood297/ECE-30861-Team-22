"""Opt-in read-only live retrieval check. See GITHUB_LIVE_TEST.md.

Importing this module performs no network activity. main() requires all explicit
configuration and local fixtures before constructing the real client. Output is
restricted to five artifact fields; exit codes communicate comparison/preflight
failures without printing documents, environment values or exception messages.
"""

import json
import os
import re
from pathlib import Path

from artifact_formats import validate_artifact
from github_artifact_retrieval import DEFAULT_CANDIDATES, GitHubArtifactRetriever
from github_client import GitHubClient, GitHubConfig, GitHubError


def _segment(value):
    return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", value)) and ".." not in value


def main():
    """Exit 0: bytes and missing case verified; 1: retrieval/comparison failed; 2: preflight failed."""
    try:
        config = GitHubConfig.from_environment()
        revision = os.environ.get("GITHUB_TEST_REVISION", "")
        product = os.environ.get("GITHUB_TEST_PRODUCT_ID", "")
        release = os.environ.get("GITHUB_TEST_RELEASE_VERSION", "")
        missing = os.environ.get("GITHUB_TEST_MISSING_RELEASE", "")
        root_value = os.environ.get("GITHUB_TEST_EXPECTED_ROOT", "")
        if (not re.fullmatch(r"[0-9a-f]{40}", revision) or not root_value
                or not all(_segment(value) for value in (product, release, missing)) or missing == release):
            return 2
        root = Path(root_value).resolve(strict=True)
        expected = {}
        for category, candidates in DEFAULT_CANDIDATES:
            for name, declared_format in candidates:
                relative = f"artifacts/{product}/{release}/{name}"
                source = root / relative
                if source.is_file():
                    # Local fixtures must remain inside the explicitly selected root.
                    if not source.resolve().is_relative_to(root):
                        return 2
                    data = source.read_bytes()
                    if not validate_artifact(data, declared_format).valid:
                        return 2
                    expected[category] = (relative, data)
                    break
            if category not in expected:
                return 2
            if any((root / "artifacts" / product / missing / name).exists() for name, _ in candidates):
                return 2
    except (GitHubError, OSError, ValueError):
        return 2

    retriever = GitHubArtifactRetriever(GitHubClient(config), revision=revision)
    passed = True
    for selected_release, expect_missing in ((release, False), (missing, True)):
        for result in retriever.retrieve(product, selected_release):
            status = result.outcome
            if result.error_code:
                # Adapter error codes are allowlisted, not upstream text.
                status += ":" + result.error_code
            print(json.dumps({
                "category": result.category,
                "retrieval_status": status,
                "source_path": result.path or f"artifacts/{product}/{selected_release}/",
                "revision": result.revision,
                "byte_count": len(result.original_bytes) if result.original_bytes is not None else 0,
            }))
            if expect_missing:
                passed = passed and result.outcome == "MISSING"
            else:
                relative, data = expected[result.category]
                passed = passed and (result.outcome == "RETRIEVED" and result.path == relative
                                     and result.original_bytes == data)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
