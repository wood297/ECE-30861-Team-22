import json
from dataclasses import replace
from unittest.mock import Mock

import pytest

from artifact_classifier import CATEGORIES
from artifact_pipeline import EXPECTED_FILES
from github_artifact_retrieval import GitHubArtifactRetriever
from github_client import ConfirmedArtifactMissing, GitHubError
from phase4_artifact_processing import ProcessingOutcome as Outcome, process_github_artifacts


SBOM = b'\xef\xbb\xbf{"bomFormat":"CycloneDX","specVersion":"1.6","components":[]}\r\n'
MANUAL = "Install café 日本語\r\n  ".encode()


def setup(files=None, responses=None):
    files = {"sbom.json": SBOM, "user_instructions.md": MANUAL} if files is None else files

    def get_file(path, ref):
        assert path.startswith("artifacts/p/v/")
        assert ref == "fixed-commit"
        value = files.get(path.rsplit("/", 1)[-1], ConfirmedArtifactMissing())
        if isinstance(value, Exception):
            raise value
        return value

    client = Mock(get_file=Mock(side_effect=get_file))
    retriever = GitHubArtifactRetriever(client, revision="fixed-commit")

    def classify(**kwargs):
        document = json.loads(kwargs["artifact_json"])["document"]
        category = "SBOM" if "CycloneDX" in document else "User Instructions"
        return json.dumps({"actual_category": category, "ambiguous": False, "evidence": document.strip()})

    provider = Mock(classify=Mock(side_effect=classify if responses is None else responses))
    return retriever, provider, client


def run(retriever, provider):
    return process_github_artifacts("p", "v", retriever=retriever, provider=provider)


def test_success_preserves_handoff_and_prompt_boundary():
    retriever, provider, _ = setup()
    results = run(retriever, provider)
    assert [r.outcome for r in results] == [Outcome.MATCH, Outcome.MATCH]
    assert [r.source_filename for r in results] == ["sbom.json", "user_instructions.md"]
    assert [r.retrieval.declared_format for r in results] == ["cyclonedx-json", "markdown"]
    for r, original in zip(results, (SBOM, MANUAL)):
        assert r.original_bytes is original
        assert r.validation.original_bytes is original
        assert r.classification.original_bytes is original
        assert (r.retrieval.product_id, r.retrieval.release_version, r.retrieval.revision,
                r.retrieval.external_source) == ("p", "v", "fixed-commit", "github")
    assert provider.classify.call_count == 2
    for call in provider.classify.call_args_list:
        assert set(json.loads(call.kwargs["artifact_json"])) == {"document"}
    assert tuple(EXPECTED_FILES) == CATEGORIES


@pytest.mark.parametrize("actual,outcome", [("Test Logs", Outcome.SEMANTIC_MISMATCH),
                                           (None, Outcome.AMBIGUOUS)])
def test_semantic_outcomes_remain_distinct(actual, outcome):
    raw = json.dumps({"actual_category": actual, "ambiguous": actual is None, "evidence": "CycloneDX"})
    retriever, provider, _ = setup(responses=[raw, json.dumps({
        "actual_category": "User Instructions", "ambiguous": False, "evidence": "Install"})])
    failed, good = run(retriever, provider)
    assert failed.outcome == outcome
    assert failed.classification.actual_category == actual
    assert failed.original_bytes is SBOM
    assert good.outcome == Outcome.MATCH


@pytest.mark.parametrize("content,code", [(b"{bad", "invalid_content"), (b"", "empty_content"),
                                         (b"\xff", "invalid_encoding")])
def test_format_failure_never_reaches_llm(content, code):
    retriever, provider, client = setup({"sbom.json": content, "user_instructions.md": MANUAL})
    failed, good = run(retriever, provider)
    assert failed.outcome == Outcome.FORMAT_FAILURE
    assert failed.validation.code == code
    assert failed.classification is None
    assert failed.original_bytes is content
    assert good.outcome == Outcome.MATCH
    assert provider.classify.call_count == 1
    assert client.get_file.call_count == 2  # No candidate fallback after invalid content.


@pytest.mark.parametrize("files,outcome", [({}, Outcome.MISSING),
    ({"sbom.json": GitHubError("permission", "secret")}, Outcome.RETRIEVAL_FAILURE)])
def test_retrieval_failure_skips_downstream_stages(files, outcome):
    retriever, provider, _ = setup(files)
    first, second = run(retriever, provider)
    assert first.outcome == outcome
    assert second.outcome == Outcome.MISSING
    for r in (first, second):
        assert r.validation is None and r.classification is None
        assert r.original_bytes is None
    if outcome == Outcome.RETRIEVAL_FAILURE:
        assert first.retrieval.error_code == "permission"
        assert "secret" not in repr(first)
    provider.classify.assert_not_called()


@pytest.mark.parametrize("failure,code", [(TimeoutError("secret"), "timeout"),
    (RuntimeError("secret"), "provider_error"), ("bad json", "malformed_response")])
def test_llm_failure_does_not_become_semantic_mismatch(failure, code, caplog):
    good_response = json.dumps({"actual_category": "User Instructions", "ambiguous": False, "evidence": "Install"})
    retriever, provider, _ = setup(responses=[failure, good_response])
    failed, good = run(retriever, provider)
    assert failed.outcome == Outcome.CLASSIFICATION_FAILURE
    assert failed.classification.error_code == code
    assert failed.classification.actual_category is None
    assert failed.original_bytes is SBOM
    assert good.outcome == Outcome.MATCH
    assert "secret" not in repr(failed) + caplog.text


def test_inaccessible_source_does_not_block_other_artifact():
    retriever, provider, _ = setup({"sbom.json": GitHubError("authentication", "secret"),
                                    "user_instructions.md": MANUAL})
    failed, good = run(retriever, provider)
    assert failed.outcome == Outcome.RETRIEVAL_FAILURE
    assert good.outcome == Outcome.MATCH
    assert provider.classify.call_count == 1


@pytest.mark.parametrize("bad_contract", ["duplicate", "wrong_release", "no_bytes", "unknown_outcome"])
def test_bad_retriever_contract_rejected_before_llm(bad_contract):
    retriever, provider, _ = setup()
    first, second = retriever.retrieve("p", "v")
    if bad_contract == "duplicate":
        second = first
    elif bad_contract == "wrong_release":
        second = replace(second, release_version="other")
    elif bad_contract == "no_bytes":
        second = replace(second, original_bytes=None)
    else:
        second = replace(second, outcome="unknown")
    with pytest.raises(ValueError):
        run(Mock(retrieve=Mock(return_value=(first, second))), provider)
    provider.classify.assert_not_called()
