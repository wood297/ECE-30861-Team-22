"""Amazon Bedrock provider for artifact_classifier; no manifest integration.

Calls the OpenAI-compatible bedrock-mantle endpoint, because that's the one our account
has quota for. Configure BEDROCK_MODEL_ID (for example openai.gpt-oss-20b) and AWS_REGION
(or AWS_DEFAULT_REGION). Credentials use boto3's standard chain, including an instance role.
"""

import json
import logging
import os

import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.exceptions import BotoCoreError, ConnectTimeoutError, HTTPClientError, ReadTimeoutError
from botocore.httpsession import URLLib3Session


logger = logging.getLogger(__name__)


class BedrockProviderError(RuntimeError):
    """Sanitized provider failure; never includes upstream details."""


class BedrockProvider:
    def __init__(self):
        self.model_id = os.environ.get("BEDROCK_MODEL_ID", "").strip()
        self._region = (os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "").strip()
        if not self.model_id or not self._region:
            raise ValueError("BEDROCK_MODEL_ID and AWS_REGION (or AWS_DEFAULT_REGION) are required")
        self._url = f"https://bedrock-mantle.{self._region}.api.aws/v1/chat/completions"
        try:
            self._session = boto3.Session()
            self._http = URLLib3Session(timeout=(5, 30))
        except Exception:
            raise BedrockProviderError("Bedrock client initialization failed") from None

    def _post(self, body: str):
        # Read credentials on every call, because instance role credentials rotate
        credentials = self._session.get_credentials()
        if credentials is None:
            raise BedrockProviderError("No AWS credentials available")
        request = AWSRequest("POST", self._url, data=body, headers={"Content-Type": "application/json"})
        SigV4Auth(credentials.get_frozen_credentials(), "bedrock-mantle", self._region).add_auth(request)
        return self._http.send(request.prepare())

    def classify(self, *, system_prompt: str, artifact_json: str, response_schema: dict) -> str:
        # The schema goes in the prompt: response_format produced malformed JSON from this
        # model in live testing, while asking in the prompt gave valid JSON every time.
        body = json.dumps({
            "model": self.model_id,
            "max_completion_tokens": 2048,
            "messages": [
                {"role": "system", "content": system_prompt + "\nresponse_schema: " + json.dumps(response_schema)},
                {"role": "user", "content": artifact_json},
            ],
        })
        try:
            response = self._post(body)
        except (ConnectTimeoutError, ReadTimeoutError):
            raise TimeoutError("Bedrock request timed out") from None
        except (BotoCoreError, HTTPClientError) as error:
            # Only the type: the message could hold upstream details
            logger.warning("Bedrock request failed: %s", type(error).__name__)
            raise BedrockProviderError("Bedrock request failed") from None
        if response.status_code != 200:
            logger.warning("Bedrock returned status %d", response.status_code)
            raise BedrockProviderError("Bedrock request failed")

        # Fail closed on truncation or anything else unexpected. Returning invalid JSON lets
        # the classifier report malformed_response through its existing validation path.
        try:
            choice = json.loads(response.content)["choices"][0]
            message = choice["message"]
            if message.get("refusal"):
                raise BedrockProviderError("Bedrock refused the request")
            text = message["content"]
            if choice["finish_reason"] != "stop" or not isinstance(text, str) or len(text) > 16_384:
                return ""
            return text
        except (KeyError, IndexError, TypeError, AttributeError, ValueError):
            return ""
