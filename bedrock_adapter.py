"""Bedrock Converse provider for artifact_classifier; no manifest integration.

Configure BEDROCK_MODEL_ID with the team's Claude Haiku model/inference profile
and AWS_REGION (or AWS_DEFAULT_REGION). Credentials use boto3's standard chain.
"""

import json
import os

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError, ConnectTimeoutError, ReadTimeoutError


class BedrockProviderError(RuntimeError):
    """Sanitized provider failure; never includes upstream details."""


class BedrockProvider:
    def __init__(self):
        self.model_id = os.environ.get("BEDROCK_MODEL_ID", "").strip()
        region = (os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "").strip()
        if not self.model_id or not region:
            raise ValueError("BEDROCK_MODEL_ID and AWS_REGION (or AWS_DEFAULT_REGION) are required")
        try:
            self._client = boto3.client(
                "bedrock-runtime", region_name=region,
                config=Config(connect_timeout=5, read_timeout=30,
                              retries={"total_max_attempts": 1, "mode": "standard"}),
            )
        except Exception:
            raise BedrockProviderError("Bedrock client initialization failed") from None

    def classify(self, *, system_prompt: str, artifact_json: str, response_schema: dict) -> str:
        try:
            response = self._client.converse(
                modelId=self.model_id,
                system=[{"text": system_prompt + "\nresponse_schema: " + json.dumps(response_schema)}],
                messages=[{"role": "user", "content": [{"text": artifact_json}]}],
                inferenceConfig={"maxTokens": 2048, "temperature": 0},
            )
        except (ConnectTimeoutError, ReadTimeoutError):
            raise TimeoutError("Bedrock request timed out") from None
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") == "ModelTimeoutException":
                raise TimeoutError("Bedrock request timed out") from None
            raise BedrockProviderError("Bedrock request failed") from None
        except BotoCoreError:
            raise BedrockProviderError("Bedrock request failed") from None

        # Fail closed on truncation, filtering, tool requests or unexpected blocks.
        # Return invalid JSON for malformed envelopes so the existing classifier
        # reports malformed_response using its unchanged validation path.
        try:
            message = response["output"]["message"]
            blocks = message["content"]
            if (response["stopReason"] != "end_turn" or message["role"] != "assistant"
                    or not isinstance(blocks, list) or not blocks):
                return ""
            if any(not isinstance(block, dict) or set(block) != {"text"}
                   or not isinstance(block["text"], str) for block in blocks):
                return ""
            text = "".join(block["text"] for block in blocks)
            return text if len(text) <= 16_384 else ""
        except (KeyError, TypeError):
            return ""
