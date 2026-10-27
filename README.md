# CRA documentation service

## Run and test

Install requirements with `python -m pip install -r requirements.txt`.
Run the existing API with `python app.py` and tests with `python -m pytest`.

## Phase 4 Bedrock classification

`bedrock_adapter.BedrockProvider` implements the existing classifier provider
interface using boto3's Bedrock Converse API. Set `BEDROCK_MODEL_ID` to the team's
Claude Haiku model ID or inference profile and `AWS_REGION` (with
`AWS_DEFAULT_REGION` as a fallback). There is no default model version.
Use the standard AWS credential chain; do not put credentials in source code.
The AWS identity needs permission to invoke the selected model.

```python
from artifact_classifier import classify_artifact
from bedrock_adapter import BedrockProvider

# artifact is an existing successful artifact_formats validation result.
result = classify_artifact(artifact, "SBOM", BedrockProvider())
```

The existing classifier validates strict JSON, the four categories, grounded
evidence, and semantic mismatches. The adapter uses 5-second connection and
30-second read timeouts, with one total attempt and no automatic retry. These
are network timeouts, not a total wall-clock deadline. Timeout and provider
failures remain internal classifier failures; no bundle-status mapping is added.
Incomplete or unexpected response envelopes fail validation. The adapter emits
no logs and sanitizes AWS exceptions; do not enable boto3/botocore wire debug
logging for private artifacts. API and Phase 3 paths are unchanged.

Run mocked tests with `python -m pytest tests/test_bedrock_adapter.py
tests/test_artifact_classifier.py`. No AWS credentials or calls are needed.
API reference: [AWS Converse documentation](https://docs.aws.amazon.com/boto3/latest/reference/services/bedrock-runtime/client/converse.html).

## GitHub authentication increment

This increment provides a reusable read-only GitHub client and a local verification
command. It authenticates a server-side personal access token through `GET /user`,
then checks the configured repository through `GET /repos/{owner}/{repo}`. It does
not retrieve artifacts or change the shared bundle API. Authentication is optional
for starting the existing API; only the verification command requires configuration.

Create a fine-grained personal access token in GitHub Settings > Developer settings >
Personal access tokens. Select the correct resource owner, restrict repository access
to the intended repository, and set an expiration. Repository Metadata read access
is sufficient for this check. Organization approval or SSO policies may also apply.
Later artifact retrieval will need its own permission review, such as Contents read;
a successful metadata check does not prove that file downloads are authorized.

In PowerShell, set the repository and enter the token without displaying it:

```powershell
$env:GITHUB_REPOSITORY = 'wood297/ECE-30861-Team-22'
$githubSecret = Read-Host 'GitHub token' -AsSecureString
$env:GITHUB_TOKEN = [System.Net.NetworkCredential]::new('', $githubSecret).Password
python -m github_auth_check
Remove-Item Env:GITHUB_TOKEN
```

The command outputs JSON and exits with 0 on success or 1 on failure. Success example:

```json
{"status": "connected", "user": "your-login", "repository": "wood297/ECE-30861-Team-22"}
```

Errors distinguish missing configuration, rejected credentials, denied permissions,
unknown/inaccessible repositories, rate limits, timeouts, network failures, redirects,
and malformed upstream responses. A 404 cannot distinguish a nonexistent repository
from a private repository hidden from the token. Requests time out after 10 seconds;
the client does not automatically retry. Credential values and upstream error bodies
are excluded from diagnostic output. Keep tokens in environment variables, use the
hosting platform's secret store for deployment, and never paste them into source code,
chat, screenshots, or test fixtures. `.env.example` documents variable names only;
`.env` files are ignored and are not automatically loaded by this implementation.

Tests use fake HTTP responses and require neither credentials nor a live connection.
Run the command above locally for the separate live authentication check.

References:
- https://docs.github.com/en/rest/authentication/authenticating-to-the-rest-api
- https://docs.github.com/en/rest/authentication/permissions-required-for-fine-grained-personal-access-tokens
