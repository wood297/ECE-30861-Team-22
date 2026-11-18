# Confluence connector integration

This branch targets person3-bounded-memory and implements the CONNECTORS.md contract.
Connection settings are cloud_id and email. The token is supplied by ctx.secret()
from the shared SecretStore. Production stores secrets in SSM. DynamoDB stores
organization membership, connection settings and product/release references.

The reference requires page_id, filename and a positive attachment version.
A product release such as v1.2 is separate from Confluence attachment version 1.
Find the attachment version in Confluence's attachment history. Reuploading a
file creates a new attachment version.

## Live verification

Use your own Atlassian account email and a scoped token with
read:attachment:confluence. Your account must have access to the test page.

From the repo root in a project-local Python environment:

    python -m confluence_live_check --cloud-id 43c0f648-dc39-4501-a7ca-bc8bf841ad48 --email YOUR_EMAIL --version 1

The token prompt is hidden. The default page is 622599 and the default fixture
is tests/fixtures/confluence/risk_assessment.md. Success compares the downloaded
original bytes and reports SHA-256
83540a9d4ffa67ac2ab287dd7a4be80f79bf5705d24d6ca2cbb4985992895958.
The version must match the attachment history.

## Server handoff

Registering confluence makes its settings/reference fields available to the
shared connector framework. Resolve membership, organization, connection and
release pin before calling make_context and run_fetch. Only Risk Assessment
should use this connector in the team's product configuration. The connector
returns original bytes and filename for the existing validation/classification
and archive flow.

The base branch still lists pipeline wiring and test_account.json as pending
in CONNECTORS.md. This connector does not establish that login, a deployed
DynamoDB table or the full bundle path is operational.

Tests use a fake network through safe_http. Run:

    python -m pytest -q tests/test_connector_confluence.py

## Expanded live suite

    python -m confluence_live_suite --email YOUR_ATLASSIAN_EMAIL

Enter a scoped token once at the hidden prompt. The runner executes eleven cases
on Confluence page 2457601 and exits nonzero if any expectation fails. A case
passes only if retrieval outcome, exact bytes, pinned filename/version, format
validation code and first risk-assessment heading match the manifest.

The suite includes three substantial synthetic assessments, an HTML assessment,
whitespace-only content (Confluence refuses zero-byte uploads), invalid UTF-8,
an unsupported text file, wrong-category content, two revisions of the firmware
assessment, an absent filename and an absent attachment version.

Files with invalid content should still be RETRIEVED correctly. The next format
or heading check rejects them. This runner exercises deterministic checks.
Bedrock semantic classification, ambiguous multi-file selection, user login and
bundle generation need their own integration tests. Network outages, rejected
credentials and unsafe redirects are covered by the automated connector tests.

The firmware attachment was uploaded twice with distinct bytes. A live run must
verify both pinned versions and will expose any unexpected history behavior.
The expanded suite has not been run with a live token by the agent.
