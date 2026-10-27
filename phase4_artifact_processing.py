"""Internal GitHub artifact processing; deliberately independent of bundle statuses.

Only SBOM and User Instructions are processed here. Risk Assessment and Test Logs
remain supplied by Phase 3 fixtures until the bundle integration combines sources.
No client/provider is constructed here: callers inject a configured retriever and
LLMProvider (for example BedrockProvider). This module performs no public manifest
mapping, fixture reads, archive generation, retries or credential setup.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from artifact_classifier import ClassificationResult, LLMProvider, classify_artifact
from artifact_formats import ValidationResult, validate_artifact
from github_artifact_retrieval import RetrievalResult


class ArtifactRetriever(Protocol):
    def retrieve(self, product_id: str, release_version: str) -> tuple[RetrievalResult, ...]:
        ...


class ProcessingOutcome(str, Enum):
    """Internal stage outcomes, NOT manifest status values."""

    MATCH = "match"
    MISSING = "missing"
    RETRIEVAL_FAILURE = "retrieval_failure"
    FORMAT_FAILURE = "format_failure"
    SEMANTIC_MISMATCH = "semantic_mismatch"
    AMBIGUOUS = "ambiguous"
    CLASSIFICATION_FAILURE = "classification_failure"


@dataclass(frozen=True)
class ProcessedArtifact:
    """Stage results retain bytes, source path/format/revision and release identity.

    Person 3 must apply the agreed inclusion/status policy; even failed validation
    retains source bytes. Classification error_code distinguishes timeout,
    provider_error and malformed_response. Unreached stages are None.
    """

    retrieval: RetrievalResult
    outcome: ProcessingOutcome
    validation: ValidationResult | None = None
    classification: ClassificationResult | None = None

    @property
    def source_filename(self) -> str | None:
        return self.retrieval.path.rsplit("/", 1)[-1] if self.retrieval.path else None

    @property
    def original_bytes(self) -> bytes | None:
        return self.retrieval.original_bytes


def process_github_artifacts(product_id: str, release_version: str, *,
                             retriever: ArtifactRetriever,
                             provider: LLMProvider) -> tuple[ProcessedArtifact, ...]:
    """Retrieve both GitHub categories, validate, then classify valid content only.

    Invalid retriever contracts are caller errors, checked before any LLM call.
    Operational source failures belong in RetrievalResult, as supplied by the
    existing GitHubArtifactRetriever. Provider exceptions are handled by the
    existing classifier without copying exception messages into results.
    """
    retrieved = tuple(retriever.retrieve(product_id, release_version))
    expected = {"SBOM", "User Instructions"}
    if (len(retrieved) != 2 or any(not isinstance(r, RetrievalResult) for r in retrieved)
            or {r.category for r in retrieved} != expected):
        raise ValueError("Retriever must return both GitHub categories exactly once")
    for r in retrieved:
        if (r.product_id != product_id or r.release_version != release_version
                or r.outcome not in {"RETRIEVED", "MISSING", "INACCESSIBLE"}):
            raise ValueError("Invalid retrieval association or outcome")
        if r.outcome == "RETRIEVED" and (
                not isinstance(r.original_bytes, bytes) or not r.path or not r.declared_format):
            raise ValueError("Retrieved artifacts require bytes, path and declared format")

    results = []
    decisions = {
        "MATCH": ProcessingOutcome.MATCH,
        "INVALID": ProcessingOutcome.SEMANTIC_MISMATCH,
        "AMBIGUOUS": ProcessingOutcome.AMBIGUOUS,
        "FAILURE": ProcessingOutcome.CLASSIFICATION_FAILURE,
    }
    for r in retrieved:
        if r.outcome != "RETRIEVED":
            outcome = (ProcessingOutcome.MISSING if r.outcome == "MISSING"
                       else ProcessingOutcome.RETRIEVAL_FAILURE)
            results.append(ProcessedArtifact(r, outcome))
            continue
        validation = validate_artifact(r.original_bytes, r.declared_format)
        if not validation.valid:
            results.append(ProcessedArtifact(r, ProcessingOutcome.FORMAT_FAILURE, validation))
            continue
        classification = classify_artifact(validation, r.category, provider)
        results.append(ProcessedArtifact(r, decisions[classification.decision],
                                         validation, classification))
    return tuple(results)
