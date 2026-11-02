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
| GitHub Connector | Retrieves SBOM and User Instructions from an external repository |
| Format Validator | Validates Markdown, HTML, and CycloneDX JSON/XML |
| Claude Haiku Classifier | Uses AWS Bedrock to classify documents based on their contents |
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
       Artifact Retrieval
       /                \
    GitHub         Local Fixtures
       \                /
                v
         Format Validation
                |
                v
     Claude Haiku Classification
                |
                v
       Manifest Generation
                |
                v
        Downloadable ZIP
```

## GitHub Retrieval and Classification

The GitHub connector retrieves existing SBOM and User Instructions files from a configured repository.

Files are expected under:

```text
artifacts/{product_id}/{release_version}/
```

Supported filenames include `sbom.json`, `sbom.xml`, `sbom.md`, `sbom.html`, `user_instructions.md`, and `user_instructions.html`.

The connector uses read-only access, preserves original file contents, and distinguishes missing files from inaccessible ones.

Retrieved documents are validated before semantic classification. Claude Haiku, accessed through AWS Bedrock, is used to determine whether document contents match their expected categories.

## API and Deployment

The Flask application implements the team's common API contract for requesting documentation bundles, checking processing status, and retrieving results.

The intended deployed workflow allows users to access the service through a web interface without interacting directly with GitHub or the source code.

**Deployment details:**

- Hosting platform: [Confirm with deployment owner]
- Service URL: [Insert deployed URL]
- Automated test runner: [Insert link]
- Deployed version: [Insert commit SHA/tag]

The exact API endpoints and response schemas are defined in the team's OpenAPI specification.

## Testing

Automated tests cover GitHub retrieval, supported file formats, classification behavior, error handling, and processing logic.

The Phase 4 retrieval/classification development branch previously reported **404 passing tests and 1 skipped test**. Live GitHub retrieval was also verified using an external test repository.

Claude Haiku classification was initially tested using mocked responses. Real Bedrock calls and end-to-end deployed testing must be verified separately.

## Known Limitations

- GitHub retrieval currently requires a configured repository and predefined artifact paths.
- External retrieval covers SBOM and User Instructions; Risk Assessment and Test Logs use existing fixtures.
- CycloneDX validation does not provide full schema certification.
- GitHub and Bedrock access depend on external service availability and permissions.
- Final API, manifest/ZIP integration, and real Bedrock functionality must be verified against the deployed release.

## References

- [EU Cyber Resilience Act](https://eur-lex.europa.eu/eli/reg/2024/2847/oj)
- [GitHub REST API](https://docs.github.com/en/rest)
- [CycloneDX](https://cyclonedx.org/)
- [AWS Bedrock](https://docs.aws.amazon.com/bedrock/)
- [Flask](https://flask.palletsprojects.com/)

**Repository:** https://github.com/wood297/ECE-30861-Team-22
