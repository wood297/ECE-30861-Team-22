"""Offline verification of the opt-in script; never contacts AWS."""

import json
from unittest.mock import Mock

import pytest

import bedrock_live_check


def correct_answers(*, system_prompt, artifact_json, response_schema):
    document = json.loads(artifact_json)["document"]
    for _name, category, decision, sample in bedrock_live_check.SAMPLES:
        if sample == document:
            actual = "User Instructions" if decision == "INVALID" else category
            return json.dumps({"actual_category": actual, "ambiguous": False, "evidence": document.split("\n")[0]})
    raise AssertionError("unexpected document")


def test_missing_configuration_makes_no_calls(monkeypatch):
    monkeypatch.setattr(bedrock_live_check, "BedrockProvider", Mock(side_effect=ValueError))
    assert bedrock_live_check.main() == 2


def test_passes_when_every_sample_is_correct(monkeypatch, capsys):
    monkeypatch.setattr(bedrock_live_check, "BedrockProvider", lambda: Mock(classify=correct_answers))
    assert bedrock_live_check.main() == 0
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [line["sample"] for line in lines] == [sample[0] for sample in bedrock_live_check.SAMPLES]
    assert {key for line in lines for key in line} == {"sample", "decision", "actual_category", "error_code"}


@pytest.mark.parametrize("failure", [TimeoutError(), RuntimeError("private-upstream-text")])
def test_fails_without_printing_upstream_text(monkeypatch, capsys, failure):
    monkeypatch.setattr(bedrock_live_check, "BedrockProvider", lambda: Mock(classify=Mock(side_effect=failure)))
    assert bedrock_live_check.main() == 1
    assert "private-upstream-text" not in capsys.readouterr().out


def test_fails_on_a_wrong_answer(monkeypatch):
    # Saying every document is an SBOM has to fail, including for the injection sample
    def always_sbom(*, system_prompt, artifact_json, response_schema):
        evidence = json.loads(artifact_json)["document"].split("\n")[0]
        return json.dumps({"actual_category": "SBOM", "ambiguous": False, "evidence": evidence})

    monkeypatch.setattr(bedrock_live_check, "BedrockProvider", lambda: Mock(classify=always_sbom))
    assert bedrock_live_check.main() == 1
