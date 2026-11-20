"""Check fixture expectations locally before running against Confluence."""
import json

import pytest

from confluence_live_suite import ROOT, check_case
from connectors.base import FetchResult

CASES = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))["cases"]


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_reference_expectations(case):
    if case["outcome"] == "NOT_FOUND":
        result = FetchResult.not_found("Pinned attachment is absent.")
    else:
        result = FetchResult.retrieved((ROOT / case["fixture"]).read_bytes(),
                                       case["filename"], str(case["version"]))
    assert check_case(case, result) is None


def test_different_bytes_fail():
    case = next(case for case in CASES if case["outcome"] == "RETRIEVED")
    assert check_case(case, FetchResult.retrieved(b"changed", case["filename"], "1")) is not None
