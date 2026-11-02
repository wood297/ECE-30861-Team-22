import json
from unittest.mock import Mock, patch

import pytest
from botocore.credentials import Credentials
from botocore.exceptions import (
    ConnectTimeoutError,
    EndpointConnectionError,
    HTTPClientError,
    ReadTimeoutError,
)

from artifact_classifier import CATEGORIES, RESPONSE_SCHEMA, classify_artifact
from artifact_formats import validate_artifact
from bedrock_adapter import BedrockProvider, BedrockProviderError


@pytest.fixture
def provider(monkeypatch):
    monkeypatch.setenv("BEDROCK_MODEL_ID", "configured-model")
    monkeypatch.setenv("AWS_REGION", "configured-region")
    with patch("bedrock_adapter.URLLib3Session") as factory:
        instance = BedrockProvider()
    factory.assert_called_once_with(timeout=(5, 30))
    instance._session = Mock()
    instance._session.get_credentials.return_value = Credentials("fake-access", "fake-secret", "fake-token")
    return instance


def http_response(content, status_code=200):
    return Mock(status_code=status_code, content=content)


def envelope(raw, finish_reason="stop", **message):
    return json.dumps({"choices": [{"finish_reason": finish_reason, "message": {
        "role": "assistant", "content": raw, "reasoning": "private-reasoning", "refusal": None, **message}}]}).encode()


def classify(provider, expected="SBOM"):
    return classify_artifact(validate_artifact(b"Components", "markdown"), expected, provider)


def sent_request(provider):
    return provider._http.send.call_args.args[0]


@pytest.mark.parametrize("category", CATEGORIES)
def test_success_and_mismatch(provider, category):
    raw = json.dumps({"actual_category": category, "ambiguous": False, "evidence": "Components"})
    provider._http.send.return_value = http_response(envelope(raw))
    result = classify(provider)
    assert result.decision == ("MATCH" if category == "SBOM" else "INVALID")
    assert result.actual_category == category

    request = sent_request(provider)
    assert request.method == "POST"
    assert request.url == "https://bedrock-mantle.configured-region.api.aws/v1/chat/completions"
    body = json.loads(request.body)
    assert body["model"] == "configured-model"
    assert [message["role"] for message in body["messages"]] == ["system", "user"]
    assert json.dumps(RESPONSE_SCHEMA) in body["messages"][0]["content"]
    assert "Components" not in body["messages"][0]["content"]
    assert body["messages"][1]["content"] == '{"document": "Components"}'
    assert set(body) == {"model", "max_completion_tokens", "messages"}


def test_request_is_signed_for_bedrock_mantle(provider):
    provider._http.send.return_value = http_response(envelope("{}"))
    provider.classify(system_prompt="system", artifact_json="document", response_schema={})
    headers = sent_request(provider).headers
    assert "/configured-region/bedrock-mantle/aws4_request" in headers["Authorization"]
    assert headers["X-Amz-Security-Token"] == "fake-token"
    assert "fake-secret" not in headers["Authorization"]


def test_credentials_are_read_on_every_call(provider):
    # Instance role credentials rotate, so they can't be kept from an earlier call
    provider._http.send.return_value = http_response(envelope("{}"))
    for _ in range(2):
        provider.classify(system_prompt="system", artifact_json="document", response_schema={})
    assert provider._session.get_credentials.call_count == 2


def test_returns_content_without_reasoning(provider):
    provider._http.send.return_value = http_response(envelope("the-answer"))
    assert provider.classify(system_prompt="system", artifact_json="document", response_schema={}) == "the-answer"


@pytest.mark.parametrize("content", [
    envelope("not json"), envelope("{}"), envelope("```json\n{}\n```"), envelope('{"{"actual_category":"SBOM"}'),
    envelope(None), envelope(5), envelope("x" * 16385), envelope('{"ambiguous": true}', finish_reason="length"),
    b"", b"not json", b"\xff", b"[]", b"null", b"{}", b'{"choices": []}', b'{"choices": [{}]}',
    b'{"choices": [{"finish_reason": "stop"}]}', b'{"choices": [{"finish_reason": "stop", "message": "text"}]}',
])
def test_malformed(provider, content):
    provider._http.send.return_value = http_response(content)
    assert classify(provider).error_code == "malformed_response"


@pytest.mark.parametrize("finish_reason", ["length", "content_filter", "tool_calls", None])
def test_cut_off_reply_never_counts_even_if_it_looks_complete(provider, finish_reason):
    raw = json.dumps({"actual_category": "SBOM", "ambiguous": False, "evidence": "Components"})
    provider._http.send.return_value = http_response(envelope(raw, finish_reason=finish_reason))
    assert classify(provider).error_code == "malformed_response"


def test_refusal_is_a_provider_failure(provider):
    provider._http.send.return_value = http_response(envelope(None, refusal="private-refusal-text"))
    assert classify(provider).error_code == "provider_error"
    with pytest.raises(BedrockProviderError) as error:
        provider.classify(system_prompt="system", artifact_json="document", response_schema={})
    assert "private-refusal-text" not in str(error.value)


@pytest.mark.parametrize("status_code", [400, 401, 403, 404, 429, 500, 503])
def test_error_status_is_a_provider_failure(provider, status_code, caplog):
    provider._http.send.return_value = http_response(b"private-document-secret", status_code)
    assert classify(provider).error_code == "provider_error"
    assert provider._http.send.call_count == 1
    assert "private-document-secret" not in caplog.text
    with pytest.raises(BedrockProviderError) as error:
        provider.classify(system_prompt="system", artifact_json="private", response_schema={})
    assert "private-document-secret" not in str(error.value)


@pytest.mark.parametrize("error", [
    EndpointConnectionError(endpoint_url="private-endpoint"),
    HTTPClientError(error="private-endpoint"),
])
def test_network_errors_are_provider_failures(provider, error, caplog):
    provider._http.send.side_effect = error
    assert classify(provider).error_code == "provider_error"
    assert provider._http.send.call_count == 1
    assert "private-endpoint" not in caplog.text
    with pytest.raises(BedrockProviderError) as raised:
        provider.classify(system_prompt="system", artifact_json="private", response_schema={})
    assert "private-endpoint" not in str(raised.value)


@pytest.mark.parametrize("error", [ReadTimeoutError(endpoint_url="private"),
                                   ConnectTimeoutError(endpoint_url="private")])
def test_network_timeouts(provider, error):
    provider._http.send.side_effect = error
    assert classify(provider).error_code == "timeout"
    with pytest.raises(TimeoutError):
        provider.classify(system_prompt="system", artifact_json="private", response_schema={})


def test_missing_credentials_are_a_provider_failure(provider):
    provider._session.get_credentials.return_value = None
    assert classify(provider).error_code == "provider_error"
    provider._http.send.assert_not_called()


@pytest.mark.parametrize("missing", ["BEDROCK_MODEL_ID", "AWS_REGION"])
def test_configuration_required(monkeypatch, missing):
    monkeypatch.setenv("BEDROCK_MODEL_ID", "configured-model")
    monkeypatch.setenv("AWS_REGION", "configured-region")
    monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)
    monkeypatch.delenv(missing)
    with patch("bedrock_adapter.boto3.Session") as factory:
        with pytest.raises(ValueError):
            BedrockProvider()
        factory.assert_not_called()


def test_default_region_and_safe_initialization(monkeypatch):
    monkeypatch.setenv("BEDROCK_MODEL_ID", "configured-model")
    monkeypatch.delenv("AWS_REGION", raising=False)
    monkeypatch.setenv("AWS_DEFAULT_REGION", "fallback-region")
    with patch("bedrock_adapter.URLLib3Session"):
        assert "bedrock-mantle.fallback-region." in BedrockProvider()._url
    with patch("bedrock_adapter.boto3.Session", side_effect=RuntimeError("secret")):
        with pytest.raises(BedrockProviderError, match="^Bedrock client initialization failed$"):
            BedrockProvider()
