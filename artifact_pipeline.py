"""Finds each category's document in a release and decides its manifest status.

Listing files and deciding are separate on purpose: decide() never touches the filesystem or
knows where files came from, so another source only needs its own version of
list_fixture_files(). Bedrock only proposes a category, for valid files with a non-standard
name or without the proper heading. Code decides every status.

source_catalog.json can point a category at one file in a GitHub repository at a pinned commit.
That file goes through the same rules as a local one. If GitHub can't supply it, the category
is INACCESSIBLE, matching Team 24's Phase 4 behavior.
"""
import hashlib
import json
import logging
import os
import re
import threading
from dataclasses import dataclass
from functools import lru_cache
from html.parser import HTMLParser
from pathlib import Path, PurePath

from artifact_classifier import CATEGORIES, RESPONSE_SCHEMA, SYSTEM_PROMPT, LLMProvider, classify_artifact
from artifact_formats import ValidationResult, validate_artifact
from bedrock_adapter import BedrockProvider, BedrockProviderError
from github_artifact_retrieval import EXTERNAL_SOURCE as GITHUB_SOURCE
from github_artifact_retrieval import RetrievalResult, fetch_configured_artifact
from github_client import GitHubClient, GitHubConfig

logger = logging.getLogger(__name__)

FIXTURES_ROOT = Path(__file__).parent / "fixtures"
CATALOG_PATH = Path(__file__).parent / "source_catalog.json"
# Reported as external_source on manifest items read from the local fixtures folder
SOURCE_NAME = "fixtures"
# Short, so a slow GitHub can't hold up a bundle for long
GITHUB_TIMEOUT = 3.0

# category name -> the Markdown file we expect to find for it
EXPECTED_FILES = {
    "SBOM": "sbom.md",
    "Risk Assessment": "risk_assessment.md",
    "Test Logs": "test_logs.md",
    "User Instructions": "user_instructions.md",
}
FORMATS_BY_EXTENSION = {
    ".md": "markdown",
    ".html": "html",
    ".json": "cyclonedx-json",
    ".xml": "cyclonedx-xml",
}
CYCLONEDX_FORMATS = {"cyclonedx-json", "cyclonedx-xml"}
# category name -> every standard file name for it. Matching is case-sensitive.
STANDARD_NAMES: dict[str, tuple[str, ...]] = {
    category: (filename, f"{PurePath(filename).stem}.html")
    for category, filename in EXPECTED_FILES.items()
}
STANDARD_NAMES["SBOM"] = ("sbom.json", "sbom.xml", "sbom.md", "sbom.html")

MAX_FILES_TO_CLASSIFY = 10
MAX_CLASSIFIED_BYTES = 1_048_576
# Changes whenever the prompt or schema does, so answers from an older prompt aren't reused
PROMPT_VERSION = hashlib.sha256((SYSTEM_PROMPT + json.dumps(RESPONSE_SCHEMA, sort_keys=True)).encode()).hexdigest()


@dataclass(frozen=True)
class SourceFile:
    name: str
    data: bytes | None = None
    read_error: str | None = None


@dataclass(frozen=True)
class Proposal:
    """What Bedrock said about a document. Code decides what to do with it."""
    actual_category: str | None = None
    evidence: str | None = None
    error_code: str | None = None


@dataclass(frozen=True)
class Decision:
    status: str
    source_file: str | None = None
    detail: str | None = None
    classified_by: str | None = None
    evidence: str | None = None
    validation: ValidationResult | None = None


# (content hash, prompt version, model) -> successful Proposal. Keeps repeat runs consistent (AC-07)
_proposals: dict[tuple[str, str, str], Proposal] = {}
_proposals_lock = threading.Lock()


def clear_proposal_cache():
    with _proposals_lock:
        _proposals.clear()


def load_catalog(path: Path = CATALOG_PATH) -> dict:
    """{"<product>/<release>": {category: entry}} for categories that don't come from fixtures."""
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8")).get("releases", {})


CATALOG = load_catalog()

# (repository, commit, path) -> RetrievalResult. A pinned commit never changes, so a found file
# or a confirmed absence is cached; temporary failures (timeouts, rate limits) are not.
_github_cache: dict[tuple[str, str, str], RetrievalResult] = {}
_github_lock = threading.Lock()


def clear_github_cache():
    with _github_lock:
        _github_cache.clear()


def make_github_client(repository: str) -> GitHubClient:
    """Real client for a catalog's repository. Uses GITHUB_TOKEN when set, otherwise reads
    anonymously, which works for public repositories. Tests replace this function."""
    config = GitHubConfig(token=os.environ.get("GITHUB_TOKEN", ""), repository=repository,
                          timeout=GITHUB_TIMEOUT, allow_anonymous=True)
    return GitHubClient(config)


@lru_cache(maxsize=1)
def _bedrock_provider() -> LLMProvider:
    return BedrockProvider()


def get_provider() -> LLMProvider | None:
    """The configured provider, or None when classification is off or can't be set up."""
    if os.environ.get("CRA_BEDROCK_ENABLED", "") != "1":
        return None
    try:
        return _bedrock_provider()
    except (ValueError, BedrockProviderError):
        logger.warning("Bedrock is enabled but its provider couldn't be set up")
        return None


def propose_category(validation: ValidationResult, provider: LLMProvider) -> Proposal:
    """Ask the provider which category a document is. Only successful answers are cached."""
    key = (hashlib.sha256(validation.original_bytes).hexdigest(), PROMPT_VERSION,
           str(getattr(provider, "model_id", "unknown")))
    with _proposals_lock:
        cached = _proposals.get(key)
    if cached is not None:
        return cached
    # The provider never sees the expected category, so any valid one gives the same answer
    result = classify_artifact(validation, CATEGORIES[0], provider)
    if result.error_code == "malformed_response":
        # The model occasionally gives one bad reply for a document it otherwise handles
        result = classify_artifact(validation, CATEGORIES[0], provider)
    if result.decision == "FAILURE":
        return Proposal(error_code=result.error_code)
    proposal = Proposal(actual_category=result.actual_category, evidence=result.evidence)
    with _proposals_lock:
        return _proposals.setdefault(key, proposal)


class _FirstH1(HTMLParser):
    def __init__(self):
        super().__init__()
        self._inside = False
        self._done = False
        self.text = ""

    def handle_starttag(self, tag, attrs):
        if tag == "h1" and not self._done:
            self._inside = True

    def handle_endtag(self, tag):
        if tag == "h1" and self._inside:
            self._inside = False
            self._done = True

    def handle_data(self, data):
        if self._inside:
            self.text += data


def _first_markdown_heading(text: str) -> str:
    in_code_block = False
    for line in text.splitlines():
        if re.match(r" {0,3}(```|~~~)", line):
            in_code_block = not in_code_block
        elif not in_code_block:
            match = re.match(r" {0,3}#[ \t]+(.*)", line)
            if match:
                return match[1]
    return ""


def has_proper_heading(validation: ValidationResult, category: str) -> bool:
    """The first top-level heading names the category. A CycloneDX marker counts as structure."""
    if validation.declared_format in CYCLONEDX_FORMATS:
        return True
    text = validation.text or ""
    if validation.declared_format == "html":
        parser = _FirstH1()
        parser.feed(text)
        parser.close()
        heading = parser.text
    else:
        heading = _first_markdown_heading(text)
    return category.casefold() in heading.casefold()


def _format_of(name: str) -> str | None:
    return FORMATS_BY_EXTENSION.get(PurePath(name).suffix)


def _invalid_format_detail(file: SourceFile, validation: ValidationResult) -> str:
    # Keeps the wording earlier phases and other teams' reports already use
    if validation.code == "empty_content":
        return f"{file.name} is empty"
    if validation.code == "invalid_encoding":
        return f"{file.name} is not valid UTF-8 text"
    return f"{file.name} is not valid {validation.declared_format} content"


def _decide_standard_file(file: SourceFile, category: str, provider: LLMProvider | None) -> Decision:
    if file.read_error is not None or file.data is None:
        return Decision("INACCESSIBLE", file.name, detail=f"Could not read {file.name}: {file.read_error}")
    declared_format = _format_of(file.name)
    assert declared_format is not None  # nosec B101  # standard names all have a known extension
    validation = validate_artifact(file.data, declared_format)
    if not validation.valid:
        return Decision("INVALID", file.name, detail=_invalid_format_detail(file, validation))
    if has_proper_heading(validation, category):
        return Decision("PRESENT", file.name, classified_by="rules", validation=validation)

    if provider is None:
        return Decision("INVALID", file.name, classified_by="rules",
                        detail=f"{file.name} has no {category} heading, and classification is switched off")
    if len(file.data) > MAX_CLASSIFIED_BYTES:
        return Decision("INVALID", file.name, classified_by="rules",
                        detail=f"{file.name} has no {category} heading, and is too large to classify")
    proposal = propose_category(validation, provider)
    if proposal.error_code is not None:
        return Decision("INVALID", file.name, classified_by="bedrock",
                        detail=f"{file.name} has no {category} heading, and classification failed "
                               f"({proposal.error_code})")
    if proposal.actual_category is None:
        return Decision("INVALID", file.name, classified_by="bedrock", evidence=proposal.evidence,
                        detail=f"{file.name} could not be clearly identified as {category}")
    if proposal.actual_category != category:
        return Decision("INVALID", file.name, classified_by="bedrock", evidence=proposal.evidence,
                        detail=f"{file.name} was classified as {proposal.actual_category}, not {category}")
    return Decision("PRESENT", file.name, classified_by="bedrock", evidence=proposal.evidence,
                    validation=validation)


def _decide_standard_category(category: str, files: dict[str, SourceFile],
                              provider: LLMProvider | None) -> Decision | None:
    """The decision from standard-named files, or None when the category has none."""
    supported = [files[name] for name in STANDARD_NAMES[category] if name in files]
    if len(supported) > 1:
        names = " and ".join(sorted(file.name for file in supported))
        return Decision("INVALID", detail=f"{names} both claim {category}")
    if supported:
        return _decide_standard_file(supported[0], category, provider)
    stem = PurePath(STANDARD_NAMES[category][0]).stem
    unsupported = sorted(name for name in files if PurePath(name).stem == stem and _format_of(name) is None)
    if unsupported:
        return Decision("INVALID", unsupported[0], detail=f"Unsupported file format: {' and '.join(unsupported)}")
    return None


def _claims_from_non_standard_files(files: dict[str, SourceFile],
                                    provider: LLMProvider | None) -> dict[str, list[tuple[SourceFile, Decision]]]:
    claims: dict[str, list[tuple[SourceFile, Decision]]] = {}
    classified = 0
    for name in sorted(files):
        file = files[name]
        if any(name in names for names in STANDARD_NAMES.values()):
            continue
        declared_format = _format_of(name)
        if declared_format is None:
            logger.warning("Ignoring %s: unsupported file format", name)
            continue
        if file.data is None:
            logger.warning("Ignoring %s: it could not be read", name)
            continue
        validation = validate_artifact(file.data, declared_format)
        if not validation.valid:
            logger.warning("Ignoring %s: it failed format validation", name)
            continue
        if declared_format in CYCLONEDX_FORMATS:
            decision = Decision("PRESENT", name, classified_by="rules", validation=validation)
            claims.setdefault("SBOM", []).append((file, decision))
            continue
        if provider is None:
            logger.warning("Ignoring %s: classification is switched off", name)
            continue
        if len(file.data) > MAX_CLASSIFIED_BYTES:
            logger.warning("Ignoring %s: it's too large to classify", name)
            continue
        if classified == MAX_FILES_TO_CLASSIFY:
            logger.warning("Ignoring %s: over the limit of %d files per release", name, MAX_FILES_TO_CLASSIFY)
            continue
        classified += 1
        proposal = propose_category(validation, provider)
        if proposal.actual_category is None:
            logger.warning("Ignoring %s: not clearly one category (%s)", name, proposal.error_code or "ambiguous")
            continue
        decision = Decision("PRESENT", name, classified_by="bedrock", evidence=proposal.evidence,
                            validation=validation)
        claims.setdefault(proposal.actual_category, []).append((file, decision))
    return claims


def decide(files: list[SourceFile], provider: LLMProvider | None) -> dict[str, Decision]:
    """Decide all four categories from a release's files, wherever those files came from."""
    by_name = {file.name: file for file in files}
    decisions: dict[str, Decision] = {}
    for category in EXPECTED_FILES:
        decision = _decide_standard_category(category, by_name, provider)
        if decision is not None:
            decisions[category] = decision

    # Non-standard files only cost a call when a category is still empty. A standard file
    # always wins, including an INVALID one, which keeps AC-04 meaningful.
    if len(decisions) < len(EXPECTED_FILES):
        claims = _claims_from_non_standard_files(by_name, provider)
        for category in EXPECTED_FILES:
            if category in decisions or category not in claims:
                continue
            claimants = claims[category]
            if len(claimants) > 1:
                names = " and ".join(file.name for file, _ in claimants)
                decisions[category] = Decision("INVALID", detail=f"{names} both claim {category}")
            else:
                decisions[category] = claimants[0][1]
    return {category: decisions.get(category, Decision("MISSING")) for category in EXPECTED_FILES}


def decide_github(entry: dict, category: str, product: str, release: str,
                  provider: LLMProvider | None) -> Decision:
    """Decide a category that source_catalog.json says lives in a GitHub repository."""
    name = PurePath(entry["path"]).name
    if _format_of(name) is None:
        return Decision("INVALID", name, detail=f"Unsupported file format: {name}")
    key = (entry["repository"], entry["commit"], entry["path"])
    with _github_lock:
        result = _github_cache.get(key)
    if result is None:
        result = fetch_configured_artifact(
            make_github_client(entry["repository"]), category=category, product_id=product,
            release_version=release, path=entry["path"], revision=entry["commit"],
            declared_format=_format_of(name) or "markdown")
        if result.outcome == "RETRIEVED" or result.error_code == "not_found":
            with _github_lock:
                _github_cache[key] = result
    if result.outcome != "RETRIEVED":
        return Decision("INACCESSIBLE", name, detail=result.source_failure_detail)
    return _decide_standard_file(SourceFile(name, data=result.original_bytes), category, provider)


def list_fixture_files(product: str, release: str) -> list[SourceFile] | None:
    """Every file directly in the release's folder, or None when the folder doesn't exist."""
    folder = FIXTURES_ROOT / product / release
    if not folder.is_dir():
        return None
    files = []
    for path in sorted(folder.iterdir()):
        if not path.is_file():
            continue
        try:
            files.append(SourceFile(path.name, data=path.read_bytes()))
        except OSError as exc:
            # REQ-07: the source failed for this artifact; the other categories still go ahead
            files.append(SourceFile(path.name, read_error=str(exc.strerror or exc)))
    return files


def retrieve_and_classify(bundle):
    product = bundle["product_id"]
    release = bundle["release_version"]

    # Safety: don't let someone send "../../something" as a version
    if "/" in product or "/" in release or ".." in product or ".." in release:
        bundle["error"] = "Invalid product_id or release_version"
        bundle["status"] = "FAILED"
        return bundle

    files = list_fixture_files(product, release)
    if files is None:
        bundle["error"] = f"No documents found for {product} {release}"
        bundle["status"] = "FAILED"
        return bundle

    provider = get_provider()
    decisions = decide(files, provider)
    sources = {category: SOURCE_NAME for category in decisions}
    for category, entry in CATALOG.get(f"{product}/{release}", {}).items():
        if entry.get("source") == "github" and category in decisions:
            decisions[category] = decide_github(entry, category, product, release, provider)
            sources[category] = GITHUB_SOURCE

    artifacts = []
    manifest = []
    for category, decision in decisions.items():
        item = {"category": category, "status": decision.status, "external_source": sources[category]}
        if decision.detail:
            item["source_failure_detail"] = decision.detail
        if decision.source_file:
            item["source_file"] = decision.source_file
        if decision.classified_by:
            item["classified_by"] = decision.classified_by
        if decision.evidence:
            item["classification_evidence"] = decision.evidence
        manifest.append(item)
        if decision.status == "PRESENT" and decision.validation is not None:
            artifacts.append({
                "category": category,
                "product_id": product,
                "release_version": release,
                "source_file": decision.source_file,
                # Decoding keeps a BOM and line endings, unlike ValidationResult.text, so
                # encoding this again gives back the original bytes (REQ-05, REQ-08)
                "content": decision.validation.original_bytes.decode("utf-8"),
            })

    bundle["artifacts"] = artifacts
    bundle["manifest"] = manifest
    bundle["status"] = "PROCESSING"
    return bundle
