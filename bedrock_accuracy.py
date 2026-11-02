"""Opt-in accuracy measurement against the real model; never run in CI, and each call is billed.

Run with BEDROCK_MODEL_ID and AWS_REGION set, and AWS credentials available:

    python bedrock_accuracy.py [repeats]

The number that matters most is how many documents would wrongly become PRESENT, because that
misrepresents compliance evidence. A wrong category or an ambiguous answer on a correct document
only costs a person a look. Exit 0: no wrong acceptances. Exit 1: at least one.
"""
import sys
from collections import Counter

from accuracy_documents import DOCUMENTS
from artifact_classifier import classify_artifact
from artifact_formats import validate_artifact
from artifact_pipeline import CATEGORIES
from bedrock_adapter import BedrockProvider


def evaluate(provider, documents=DOCUMENTS, repeats=1):
    """Score each answer the way the pipeline would use it for a document without a proper heading."""
    wrong_acceptances = []
    missed = []
    failures = []
    correct = Counter()
    total = Counter()
    for document_id, truth, filed_under, text in documents:
        validation = validate_artifact(text.encode(), "markdown")
        if not validation.valid:
            raise ValueError(f"{document_id} isn't valid Markdown")
        for _ in range(repeats):
            result = classify_artifact(validation, filed_under, provider)
            label = truth or "None of them"
            total[label] += 1
            accepted = result.decision == "MATCH"
            if result.decision == "FAILURE":
                failures.append(f"{document_id}: {result.error_code}")
            elif accepted and truth != filed_under:
                wrong_acceptances.append(f"{document_id}: true category {label}, accepted as {filed_under}")
            elif not accepted and truth == filed_under:
                missed.append(f"{document_id}: {result.actual_category or 'ambiguous'}, filed as {filed_under}")
            if result.decision != "FAILURE" and result.actual_category == truth:
                correct[label] += 1
    return {"wrong_acceptances": wrong_acceptances, "missed": missed, "failures": failures,
            "correct": correct, "total": total}


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    repeats = int(args[0]) if args else 1
    try:
        provider = BedrockProvider()
    except ValueError:
        print("Set BEDROCK_MODEL_ID and AWS_REGION first", file=sys.stderr)
        return 2
    results = evaluate(provider, repeats=repeats)
    answers = sum(results["total"].values())
    print(f"Wrongly accepted as PRESENT: {len(results['wrong_acceptances'])} of {answers} answers")
    for line in results["wrong_acceptances"]:
        print(f"  {line}")
    print(f"Correct documents not accepted: {len(results['missed'])}")
    for line in results["missed"]:
        print(f"  {line}")
    print(f"Failed calls: {len(results['failures'])}")
    for line in results["failures"]:
        print(f"  {line}")
    print("Correct category or ambiguous call, per true category:")
    for label in (*CATEGORIES, "None of them"):
        print(f"  {label}: {results['correct'][label]} of {results['total'][label]}")
    return 1 if results["wrong_acceptances"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
