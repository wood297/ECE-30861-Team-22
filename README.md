# CRA Documentation Aggregation System

**ECE 30861 — Team 22 | Phase 4**

## Overview

The CRA Documentation Aggregation System is a Flask-based web service designed to help software teams collect, validate, and package documentation relevant to the EU Cyber Resilience Act (CRA), Regulation (EU) 2024/2847.

The application supports four documentation categories:

- Software Bill of Materials (SBOM)
- Risk Assessment
- Test Logs
- User Instructions

Given a product ID and release version, the system processes available documentation, generates a manifest identifying missing or invalid artifacts, and packages results into a downloadable ZIP file.

## System Architecture

The Phase 4 system consists of the following components:

| Component | Function |
|---|---|
| Flask API | Handles documentation requests and exposes processing results |
| GitHub Connector | Connected through `source_catalog.json`: a category can point at one file in a GitHub repository at a pinned commit. Used today only for the pod's source-failure fixture (`prd-88 v1.2-github-inaccessible`, Test Logs). Pulling real releases from `prithika-sundar/cra-artifact-test` is planned for Phase 5 |
| Format Validator | Validates Markdown, HTML, and CycloneDX JSON/XML |
| Bedrock Classifier | Uses `openai.gpt-oss-20b` on AWS Bedrock to classify documents with a non-standard name or without the proper heading |
| Bundle Generator | Creates the documentation manifest and ZIP bundle |
| Automated Tests | Verify functionality, error handling, and API behavior |

**Processing workflow:**

```text
User Request (Product ID + Release)
                |
                v
            Flask API
                |
                v
         Local Fixtures
                |
                v
         Format Validation
                |
                v
    Heading Rules, then Bedrock if needed
                |
                v
       Manifest Generation
                |
                v
        Downloadable ZIP
```

## Document Classification

Files are read from:

```text
fixtures/{product_id}/{release_version}/
```

A file with a standard name and the category in its first heading is accepted without calling a model. Bedrock checks standard names without that heading, and identifies other files in a supported format when a category is still missing. Bedrock only proposes a category. Code decides every status, and if Bedrock is off or failing, nothing can become `PRESENT` through it. See `GETTING_STARTED.md` §4.4 for the full rules.

We use `openai.gpt-oss-20b` because our AWS Free plan account has no quota for Claude Haiku on Bedrock.

## API and Deployment

The Flask application implements the team's common API contract for requesting documentation bundles, checking processing status, and retrieving results.

The intended deployed workflow allows users to access the service through a web interface without interacting directly with GitHub or the source code.

**Deployment details:**

- Hosting platform: AWS EC2 (`us-east-2`), deployed automatically by GitHub Actions when a commit on `main` passes CI
- Service URL: https://team22.13-58-35-224.sslip.io/system
- Automated test runner: https://team22.13-58-35-224.sslip.io/tester
- Deployed version: `GET /system/version`

The exact API endpoints and response schemas are defined in the team's OpenAPI specification.

## Testing

Automated tests cover GitHub retrieval, supported file formats, classification behavior, error handling, deployment checks, and processing logic. Run them with `python -m pytest`. They use fake responses and need no credentials.

Live checks are separate and call the real model: `bedrock_live_check.py` and `bedrock_accuracy.py`. On 32 labelled documents run 3 times each, no wrong document was accepted in 96 answers, and 2 calls returned malformed replies. The set is small and written by the team, so it shows the approach works, not how it will do on every real document.

## Known Limitations

- The running service reads from local fixtures, except categories listed in `source_catalog.json`. Today that's one GitHub entry, which exists to test source failure (AC-05). Retrieving real releases from GitHub is planned for Phase 5. GitHub reads are anonymous unless `GITHUB_TOKEN` is set (60 requests/hour per IP; results for a pinned commit are cached).
- CycloneDX validation does not provide full schema certification.
- Bedrock access depends on external service availability and our account's limits.
- Deployment uses a stored, tightly scoped AWS key, because our AWS organization's policy doesn't allow GitHub's OIDC sign-in.

## GitHub connector setup

The running service uses the connector only for entries in `source_catalog.json`, and reads public repositories anonymously. The token setup below is for the separate access check, and for private repositories later.

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

## References

- [EU Cyber Resilience Act](https://eur-lex.europa.eu/eli/reg/2024/2847/oj)
- [GitHub REST API](https://docs.github.com/en/rest)
- [CycloneDX](https://cyclonedx.org/)
- [AWS Bedrock](https://docs.aws.amazon.com/bedrock/)
- [Flask](https://flask.palletsprojects.com/)

**Repository:** https://github.com/wood297/ECE-30861-Team-22
