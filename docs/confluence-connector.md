# Confluence connector — bundle setup and team handoff

Confluence supplies Risk Assessment. The integrated workflow is on
person3-bounded-memory, which includes the Confluence connector and the account,
connection, product and bundle pages. The implementation is also on
codex/confluence-integration. Use the integrated branch for bundle testing.

Cora's remaining setup action is to invite the tester's Atlassian account to
the Confluence site. The tester then opens the page while signed in to confirm
access and creates their own scoped Confluence API token with
read:attachment:confluence. The connection email must belong to that token's
account. App login and Atlassian login may use different email addresses.

- Site — https://whcora.atlassian.net/wiki
- Cloud ID — 43c0f648-dc39-4501-a7ca-bc8bf841ad48
- Baseline page — https://whcora.atlassian.net/wiki/spaces/T2CT/pages/622599/Phase+5+Risk+Assessment+Retrieval+Suite
- Expanded page — https://whcora.atlassian.net/wiki/spaces/T2CT/pages/2457601/Risk+Assessment+Live+Acceptance+Fixtures
- Create a token — https://id.atlassian.com/manage-profile/security/api-tokens

## Test through the existing shared bundle

test_account.json already configures prd-88 / v1.6-all-sources with this pin:

| Field | Value |
|---|---|
| Confluence connection ID | 022f2e41-fa54-450c-8354-6ac0fd30f428 |
| Organization ID | 30653a13-cd0f-418c-8ec3-a8f813be7dbc |
| cloud_id | 43c0f648-dc39-4501-a7ca-bc8bf841ad48 |
| page_id | 622599 |
| filename | risk_assessment.md |
| version | 1 |

1. In that connection's settings in test_account.json, replace
   replace-with-token-owner@example.invalid with the tester's Atlassian email.
2. Store the tester's token for that existing connection. On an AWS-configured
   Bash terminal, from the repository root, run:

       bash deploy/put_connection_token.sh confluence

   The prompt hides the token. The script uses us-east-2 and the /team22-cra
   prefix by default. For development resources, set PREFIX=/team22-cra-dev
   when invoking it, matching the running server's CRA_SECRET_PREFIX.
   Check stored-token presence with:

       bash deploy/put_connection_token.sh status

   The production parameter path is:

       /team22-cra/orgs/30653a13-cd0f-418c-8ec3-a8f813be7dbc/conn/022f2e41-fa54-450c-8354-6ac0fd30f428

   Tokens are SSM SecureString values. The server role needs ssm:GetParameter.
   The email change must reach the running app's test_account.json. A token
   replacement is read on the next bundle.
3. Request the shared pod-test bundle with POST /bundles and this body.
   Use the deployed system base URL or the local Flask base URL as appropriate.

       {"product_id":"prd-88","release_version":"v1.6-all-sources"}

   The deployed endpoint is /system/bundles. The signed-in company workflow
   selects the active organization instead, so use the shared pod-test flow
   for this built-in account.
4. Poll the returned bundle ID using GET /bundles/<bundle_id> and download
   through GET /bundles/<bundle_id>/download, retaining the /system prefix
   on the deployed host.

Expected Confluence result is Risk Assessment PRESENT with original filename
risk_assessment.md. Its downloaded bytes have SHA-256
83540a9d4ffa67ac2ab287dd7a4be80f79bf5705d24d6ca2cbb4985992895958.
GitHub and Jenkins must also be configured for the entire bundle to be COMPLETED.
Inspect the Risk Assessment entry separately if another source fails.

### Local bundle development without SSM

The existing local secret store supports a private *.secrets.json file:

    {"connections":{"022f2e41-fa54-450c-8354-6ac0fd30f428":"ENTER_TOKEN_LOCALLY"}}

Set CRA_LOCAL_SECRETS to that file's path in the server environment. Keep it
out of Git and restrict access to your OS account. Include the other connection
IDs and tokens if testing all sources. Use the team's normal local app startup
configuration and the same email replacement in test_account.json.

## Test through the signed-in organization UI

1. Sign in to the app, select your organization and use an organization admin
   account to configure sources.
2. Open Connections at /app/connections. Add a Confluence connection with
   cloud_id from above, your Atlassian email and your token. On the deployed
   host, these pages sit below /system/app.
3. Open Products at /app/products. Create or select the product, pin the desired
   release and configure its Risk Assessment entry with that connection.
4. For a baseline that avoids a model call, use page_id 622599, filename
   risk_assessment.md and version 1. Save the entry.
5. Request the product/release bundle through the product page. Inspect status
   and download from the resulting bundle page.

Company connections have their own generated IDs and organization-scoped secrets.
The shared pod-test connection IDs above apply to the built-in shared account.
The UI handles saving company secrets through the shared SecretStore.

## Product releases and attachment versions

The product release string (for example v1.6-all-sources) selects the app's release
configuration. The Confluence attachment version is a positive integer that pins
the source bytes. Reuploading the same filename creates another attachment
revision. Both firmware revisions in the expanded suite have been live-tested.

The reader returns original bytes for any downloadable file, including invalid
content. The pipeline then performs format and category checks. A missing pinned
file/version is NOT_FOUND at the connector and INACCESSIBLE in the bundle manifest.
A category without a configured source is MISSING. Nonstandard filenames can need
Bedrock, so use the standard baseline name above for the first complete bundle.

## Verification evidence and optional diagnostics

On November 18, 2026, Cora ran all eleven expanded live cases successfully with
her scoped token. They checked original bytes, both firmware revisions, format
validation, the heading check, missing filenames and missing versions.
Automated results at connector commit 4e07ff0 were 905 passed and 2 skipped.
A teammate's own credentials and the integrated bundle still need their own run.

Optional connector-only checks from a project-local Python environment:

    python -m confluence_live_check --cloud-id 43c0f648-dc39-4501-a7ca-bc8bf841ad48 --email YOUR_ATLASSIAN_EMAIL --version 1
    python -m confluence_live_suite --email YOUR_ATLASSIAN_EMAIL
    python -m pytest -q tests/test_connector_confluence.py tests/test_confluence_live_suite.py

The expanded fixture manifest is tests/fixtures/confluence/live-suite/manifest.json.
It contains the exact filename, version, reference bytes and expected outcome
for each case. The suite tests retrieval and deterministic validation. Bedrock
classification and full bundle integration are covered separately.

## If Risk Assessment fails

| Result | Check |
|---|---|
| AUTH_FAILED | The token belongs to the configured email, has the attachment read scope, and its account can open the page |
| Missing credentials | The correct connection ID is provisioned in the selected secret store and prefix |
| SSM AccessDenied | The running backend's IAM role can read the parameter path |
| NOT_FOUND | The exact filename, page ID and attachment version match the selected page |
| SOURCE_ERROR after download HTTP 400 | The integrated connector includes fix 4e07ff0, which confirms absent versions through the version endpoint |
| RETRIEVED but INVALID | Inspect format/heading/classification results separately from authentication |
| Different risk assessment in the bundle | Check whether the request used pod-test or the signed-in active organization and its release pin |

The latest integration code was reviewed for this guide. Its deployed environment
and integrated bundle were not exercised by the author during this documentation
update.
