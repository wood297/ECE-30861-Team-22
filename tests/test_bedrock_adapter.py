import json
from unittest.mock import Mock, patch

import pytest
from botocore.exceptions import ClientError, ConnectTimeoutError, ReadTimeoutError

from artifact_classifier import CATEGORIES, RESPONSE_SCHEMA, classify_artifact
from artifact_formats import validate_artifact
from bedrock_adapter import BedrockProvider, BedrockProviderError


@pytest.fixture
def provider(monkeypatch):
    monkeypatch.setenv("BEDROCK_MODEL_ID", "configured-haiku-profile")
    monkeypatch.setenv("AWS_REGION", "configured-region")
    with patch("bedrock_adapter.boto3.client") as factory:
        instance = BedrockProvider()
    args = factory.call_args.kwargs
    assert args["region_name"] == "configured-region"
    assert args["config"].connect_timeout == 5
    assert args["config"].read_timeout == 30
    assert args["config"].retries["total_max_attempts"] == 1
    return instance


def envelope(raw):
    return {"stopReason": "end_turn", "output": {"message": {
        "role": "assistant", "content": [{"text": raw}]}}}


def classify(provider, expected="SBOM"):
    return classify_artifact(validate_artifact(b"Components", "markdown"), expected, provider)


@pytest.mark.parametrize("category", CATEGORIES)
def test_success_and_mismatch(provider, category):
    raw = json.dumps({"actual_category": category, "ambiguous": False, "evidence": "Components"})
    provider._client.converse.return_value = envelope(raw)
    result = classify(provider)
    assert result.decision == ("MATCH" if category == "SBOM" else "INVALID")
    args = provider._client.converse.call_args.kwargs
    assert args["modelId"] == "configured-haiku-profile"
    assert args["messages"] == [{"role": "user", "content": [{"text": '{"document": "Components"}'}]}]
    assert json.dumps(RESPONSE_SCHEMA) in args["system"][0]["text"]
    assert "toolConfig" not in args


@pytest.mark.parametrize("response", [
    envelope("not json"), envelope('{}'), envelope('```json\n{}\n```'),
    {}, None, {"stopReason": "max_tokens", **{"output": envelope('{}')["output"]}},
    envelope("x" * 16385),
    {"stopReason": "end_turn", "output": {"message": {"role": "assistant", "content": [{"toolUse": {}}]}}},
])
def test_malformed(provider, response):
    provider._client.converse.return_value = response
    assert classify(provider).error_code == "malformed_response"


@pytest.mark.parametrize("code,expected", [
    ("ThrottlingException", "provider_error"), ("AccessDeniedException", "provider_error"),
    ("ValidationException", "provider_error"), ("ModelTimeoutException", "timeout"),
])
def test_service_errors(provider, code, expected, caplog):
    provider._client.converse.side_effect = ClientError(
        {"Error": {"Code": code, "Message": "private-document-secret"}}, "Converse")
    assert classify(provider).error_code == expected
    assert provider._client.converse.call_count == 1
    assert "private-document-secret" not in caplog.text
    with pytest.raises((BedrockProviderError, TimeoutError)) as error:
        provider.classify(system_prompt="system", artifact_json="private", response_schema={})
    assert "private-document-secret" not in str(error.value)


@pytest.mark.parametrize("error", [ReadTimeoutError(endpoint_url="private"), ConnectTimeoutError(endpoint_url="private")])
def test_network_timeouts(provider, error):
    provider._client.converse.side_effect = error
    assert classify(provider).error_code == "timeout"


def test_configuration_required(monkeypatch):
    for name in ("BEDROCK_MODEL_ID", "AWS_REGION", "AWS_DEFAULT_REGION"):
        monkeypatch.delenv(name, raising=False)
    with patch("bedrock_adapter.boto3.client") as factory:
        with pytest.raises(ValueError):
            BedrockProvider()
        factory.assert_not_called()


def test_default_region_and_safe_initialization(monkeypatch):
    monkeypatch.setenv("BEDROCK_MODEL_ID", "team-profile")
    monkeypatch.delenv("AWS_REGION", raising=False)
    monkeypatch.setenv("AWS_DEFAULT_REGION", "fallback-region")
    with patch("bedrock_adapter.boto3.client", side_effect=RuntimeError("secret")) as factory:
        with pytest.raises(BedrockProviderError, match="^Bedrock client initialization failed$"):
            BedrockProvider()
    assert factory.call_args.kwargs["region_name"] == "fallback-region"
