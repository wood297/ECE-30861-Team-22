"""Offline checks of the accuracy script and its documents; never contacts AWS."""
import json

import bedrock_accuracy
from accuracy_documents import DOCUMENTS
from artifact_classifier import CATEGORIES
from artifact_formats import validate_artifact


class FixedAnswers:
    """Answers each document with a chosen category, keyed by its text."""

    def __init__(self, answer):
        self.answer = answer

    def classify(self, *, system_prompt, artifact_json, response_schema):
        document = json.loads(artifact_json)["document"]
        category = self.answer(document)
        return json.dumps({"actual_category": category, "ambiguous": category is None, "evidence": document.split()[0]})


TRUTH = {text: truth for _, truth, _, text in DOCUMENTS}


def test_documents_are_usable_and_labelled():
    ids = [document[0] for document in DOCUMENTS]
    assert len(ids) == len(set(ids))
    assert 20 <= len(DOCUMENTS) <= 50
    for _, truth, filed_under, text in DOCUMENTS:
        assert truth is None or truth in CATEGORIES
        assert filed_under in CATEGORIES
        assert validate_artifact(text.encode(), "markdown").valid
    assert {truth for _, truth, _, _ in DOCUMENTS} >= set(CATEGORIES)


def test_a_perfect_model_scores_perfectly():
    results = bedrock_accuracy.evaluate(FixedAnswers(TRUTH.get))
    assert results["wrong_acceptances"] == []
    assert results["missed"] == []
    assert results["failures"] == []
    assert results["correct"] == results["total"]


def test_a_model_agreeing_with_every_filing_is_caught():
    filed = {text: filed_under for _, _, filed_under, text in DOCUMENTS}
    results = bedrock_accuracy.evaluate(FixedAnswers(filed.get))
    wrongly_filed = [doc for doc in DOCUMENTS if doc[1] != doc[2]]
    assert len(results["wrong_acceptances"]) == len(wrongly_filed)
    assert results["missed"] == []


def test_failures_are_counted_separately():
    results = bedrock_accuracy.evaluate(type("Broken", (), {"classify": lambda self, **_: "not json"})())
    assert len(results["failures"]) == len(DOCUMENTS)
    assert results["wrong_acceptances"] == []


def test_repeats_multiply_the_answers():
    results = bedrock_accuracy.evaluate(FixedAnswers(TRUTH.get), repeats=2)
    assert sum(results["total"].values()) == 2 * len(DOCUMENTS)
