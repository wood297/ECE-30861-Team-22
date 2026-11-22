# CRA Documentation Aggregation System

**ECE 30861 — Team 22 | Phase 5**

## Overview

The CRA Documentation Aggregation System is a Flask-based web service designed to help software teams collect, validate, and package documentation relevant to the EU Cyber Resilience Act (CRA), Regulation (EU) 2024/2847.

The application supports four documentation categories:

- Software Bill of Materials (SBOM)
- Risk Assessment
- Test Logs
- User Instructions

Given a product ID and release version, the system processes available documentation, generates a manifest identifying missing or invalid artifacts, and packages results into a downloadable ZIP file.

## System Architecture

The Phase 5 system consists of the following integrated components:

| Component | Function |
|---|---|
| Flask API | Handles documentation requests and exposes processing results |
| Connectors | `connectors/`: Jenkins, GitHub, Confluence, and local fixtures, implemented behind a shared connector contract (`CONNECTORS.md`). The pod-test account, `test_account.json`, maps each documentation category for a product release to a configured source. For example, `prd-88 v1.5-jenkins` retrieves Test Logs from a pinned Jenkins build, while `v1.6-all-sources` uses all four sources. Credentials are retrieved from AWS SSM (`CRA_SECRET_PREFIX`) or a local secrets file (`CRA_LOCAL_SECRETS`), rather than stored in the repository. Live source verification is available through `python -m connectors.account_live_check`. |
| GitHub Connector (Phase 5) | Retrieves documentation artifacts from GitHub repositories using the shared connector framework. Supports pinned commit SHAs, configurable artifact paths, secure token retrieval through the secret store, and standardized error handling. Includes automated tests and live GitHub verification. |
| Format Validator | Validates Markdown, HTML, and CycloneDX JSON/XML |
| Bedrock Classifier | Uses `openai.gpt-oss-20b` on AWS Bedrock to classify documents with a non-standard name or without the proper heading |
| Bundle Generator | Creates documentation manifests and downloadable ZIP bundles containing retrieved artifacts |
| Automated Tests | Verify connector functionality, artifact retrieval, classification, error handling, aggregation, bundle generation, and API behavior |

**Processing workflow:**

```text
User Request (Product ID + Release)
                |
                v
             Flask API
                |
                v
      Shared Connector Framework
                |
       +--------+--------+--------+
       |        |        |        |
       v        v        v        v
     GitHub  Confluence Jenkins Local Fixtures
       |        |        |        |
       +--------+--------+--------+
                |
                v
       Artifact Aggregation
                |
                v
        Format Validation
                |
                v
    Heading Rules / Bedrock Classification
                |
                v
       Manifest Generation
                |
                v
        Downloadable ZIP Bundle
```

## Document Classification

Documentation artifacts are retrieved through the shared connector framework from GitHub, Confluence, Jenkins, or local fixtures, depending on the product and release configuration in `test_account.json`.

Local documentation fixtures are stored at:

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

The project includes automated unit, integration, and connector tests covering the shared connector framework, external artifact retrieval, supported file formats, document classification, error handling, aggregation, and bundle generation.

GitHub connector tests verify pinned commit retrieval, authentication handling, candidate artifact paths, missing files, and upstream errors. Confluence and Jenkins connectors are also tested through the shared connector interface.

The automated test suite can be run using:

```bash
python -m pytest
```

Tests use mocked responses where appropriate and do not require live external credentials.

Live verification scripts are available for checking configured external connections and source retrieval. These include `connectors.account_live_check` and the GitHub-specific verification instructions in `GITHUB_LIVE_TEST.md`.

The Bedrock classification approach was evaluated using 32 labelled documents across three runs. No incorrect document was accepted in 96 answers, although two calls returned malformed responses. This dataset is relatively small and team-generated, so results should not be interpreted as a guarantee of performance on all real-world documents.

The team also ran the integrated test suite following connector and bundle pipeline integration, with all reported test cases passing.

## Known Limitations

- External documentation retrieval is supported through the GitHub, Confluence, and Jenkins connectors, alongside local fixtures. Availability depends on valid source configuration, access permissions, network connectivity, and the availability of external services. Missing or inaccessible artifacts are reported through the connector and bundle status handling.
- CycloneDX validation does not provide full schema certification.
- Bedrock access depends on external service availability and our account's limits.
- Deployment uses a stored, tightly scoped AWS key, because our AWS organization's policy doesn't allow GitHub's OIDC sign-in.

## GitHub Connector Setup (Phase 5)

The Phase 5 GitHub connector is implemented in `connectors/github.py` and follows the shared connector interface defined in `CONNECTORS.md`.

The connector supports:

- Read-only retrieval of documentation artifacts from GitHub repositories.
- Pinned 40-character commit SHAs for reproducible retrieval.
- Configurable candidate artifact paths.
- Secure authentication through the shared secret store.
- Controlled GitHub API requests using the shared HTTP client.
- Standardized handling of missing artifacts, authentication failures, rate limits, and source errors.

Connector configuration specifies the GitHub repository, pinned revision, and candidate artifact paths. Authentication tokens are retrieved through the connector context rather than embedded in source code.

### Verification and Testing

Automated GitHub connector tests are located in `tests/test_connector_github.py`.

Live verification is available through:

`python -m connectors.github_live_check`

Detailed configuration and verification instructions are provided in `GITHUB_LIVE_TEST.md`.

The connector is integrated with the shared connector framework and can be used by the aggregation pipeline to retrieve documentation for configured product releases.

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
