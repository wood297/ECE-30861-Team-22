# Read-only GitHub retrieval check

`github_live_check.py` uses the existing `GitHubConfig.from_environment()`,
`GitHubClient.get_file()` and `GitHubArtifactRetriever`. It makes only read requests.
It does not create fixtures, alter repositories, commit or push. Importing it does
not run the check. Automated tests use mocks, never live credentials.

## Manual configuration

Use the documented project virtual environment and install `requirements.txt`.
Select a repository that **already contains** suitable test fixtures. The example
in `.env.example` is only an authentication example, not proof of available fixtures.
Environment files are not automatically loaded.

Configure these variables in your terminal:

| Variable | Required value |
|---|---|
| `GITHUB_TOKEN` | Existing secret-injected token authorized for the selected repository, with Contents read access. Never put it in command arguments, source, or output. |
| `GITHUB_REPOSITORY` | Explicit `owner/repository` for your chosen test repository. |
| `GITHUB_TEST_REVISION` | Full lowercase 40-character commit SHA containing the fixtures. Copy the commit SHA from an existing checkout or repository UI. Branches/tags are deliberately rejected. |
| `GITHUB_TEST_PRODUCT_ID` | Product directory name, e.g. `prd-88`. |
| `GITHUB_TEST_RELEASE_VERSION` | Existing fixture release directory, e.g. `v1.2`; this is not interpreted as a Git tag. |
| `GITHUB_TEST_MISSING_RELEASE` | A different release directory known to lack all supported candidate files at that commit. |
| `GITHUB_TEST_EXPECTED_ROOT` | Absolute path to a trusted local fixture snapshot with the same `artifacts/` layout and exact source bytes from that commit. |

IDs must be single ASCII path segments starting with a letter/digit, followed by
letters, digits, dots, underscores or hyphens, with no `..`.

The remote commit and local snapshot must contain both positive-case artifacts:

```text
artifacts/<product>/<release>/sbom.json
artifacts/<product>/<release>/user_instructions.md
```

Alternatively SBOM candidates are tried in order: `sbom.json`, `sbom.xml`,
`sbom.md`, `sbom.html`. Instructions candidates are `user_instructions.md`,
`user_instructions.html`. The first existing candidate wins. The local snapshot
must reflect that choice, including absence of higher-priority candidates. Local
positive fixtures must pass format validation before any network request occurs.
CycloneDX validation is structural, not full schema validation. Each GitHub JSON
response is capped at 1 MiB; base64 overhead reduces the usable artifact size.

Do not normalize line endings or reserialize JSON/XML when preparing the local
snapshot. Byte comparison is meaningful only against trusted expected content.
This script does not independently attest that the local snapshot came from the
selected repository/commit; you must establish that before running it.

## Exact command

After configuring all variables, from the repository root in the activated venv:

```sh
python -m github_live_check
```

Exit codes: `0` means both artifacts match the expected paths/bytes and the
negative release produced two confirmed missing outcomes; `1` means retrieval,
byte comparison or missing-case verification failed; `2` means configuration or
local fixtures failed preflight, with no network requests. Preflight errors are
silent to avoid exposing configuration. Shells can inspect the exit code with
`echo $?` (zsh/bash) or `$LASTEXITCODE` (PowerShell).

Output is one JSON row per artifact per case, with only category, retrieval status,
source path, revision and byte count. No content, token, upstream error body or
exception message is printed. Byte mismatch does not alter the retrieval status;
it is reflected by exit code 1.

## Missing versus inaccessible

The negative release demonstrates `MISSING` using successful, complete Git-tree
responses. For missing results, source path identifies the searched directory,
since no single candidate was found. Authentication and permission errors appear
as `INACCESSIBLE:authentication` and `INACCESSIBLE:permission`; an ambiguous 404
appears as `INACCESSIBLE:repository_access`, never `MISSING`. These are diagnostic
script labels, not changes to public manifest statuses.

Do not break working credentials to manufacture live failures. Offline tests in
`tests/test_github_live_check.py` separately demonstrate authentication and permission
failures. GitHub may hide a denied private repository behind a 404, so a live
permission denial cannot always be distinguished from repository unavailability.

## Shared GitHub connector

Create `github.local.json` with a repository, a full lowercase commit SHA, and
the artifact paths in priority order. The live check expands `{product}` and
`{release}` using the command-line arguments before calling the connector.

```json
{
  "type": "github",
  "conn_id": "6f1c1d2e-0000-4000-8000-000000000001",
  "settings": {"repository": "owner/repository"},
  "ref": {
    "revision": "0123456789abcdef0123456789abcdef01234567",
    "paths": ["artifacts/{product}/{release}/sbom.json"]
  }
}
```

Use the same connection ID in `github.secrets.json` and set file permissions
with `chmod 600 github.secrets.json`. Use a fine-grained token with read-only
Contents access to the configured repository.

```json
{"connections": {"6f1c1d2e-0000-4000-8000-000000000001": "<token>"}}
```

Copy the expected artifact bytes from a trusted checkout at the pinned commit.
From the repository root, run:

```sh
CRA_LOCAL_SECRETS=github.secrets.json python -m connectors.github_live_check \
    github.local.json /path/to/expected/sbom.json --product prd-88 --release v5.0
```

Exit code `0` means the SHA-256 digests match, `1` means retrieval failed or
the digests differ, and `2` means preflight failed. For a retrieval-only check,
use `python -m connectors.try_fetch` with the same secrets and connection files.

The verified live fixture is `fixtures/prd-88/v1.2/sbom.md` in
`wood297/ECE-30861-Team-22` at commit `1bffbacd99e7a7f775b7f9073ea7ddec68e3ab1b`.
Use that exact path in `paths` and a local copy from the same commit to
reproduce the check with a token authorized for the repository.

## Aggregation handoff for Anthony

Scope each connection of type `github` to one organization and repository.
Validate `{"repository": "owner/repository"}` with the registered connector
at connection creation. Provision a fine-grained token with read-only Contents
access via `SecretStore.put_connection_secret(org_id, conn_id, token)`. Keep the
token in the secret store. For each artifact category, pin the connection ID
alongside a `revision` containing a full lowercase commit SHA and `paths`
containing the ordered candidate paths.

Once aggregation resolves the tenant's connection and extracts the category's
reference fields, invoke the existing connector kit:

```python
from connectors import REGISTRY, make_context
from connectors.base import fill_ref, run_fetch

connector = REGISTRY[conn.type]
ref = fill_ref(connector.validate_ref(pin_ref), product_id, release_version)
ctx = make_context(connector, conn, secret_store, deadline=deadline, budget=byte_budget)
result = run_fetch(connector, conn, ref, ctx)
```

`pin_ref` contains only `revision` and `paths`. Pass the bundle's deadline
as a monotonic timestamp and reuse its shared byte budget. On `RETRIEVED`,
pass `result.data`, `result.filename`, and `result.version` through the existing
artifact processing, using `connector.display_name` for the external source.
Map other outcomes to `INACCESSIBLE` with `result.detail` as the source failure
detail. Keep `UNAVAILABLE` results out of the cache. GitHub can return an
ambiguous HTTP 404 for an inaccessible repository or Git object, which the
connector reports as `AUTH_FAILED`. `NOT_FOUND` means complete Git trees
confirmed that all candidates were absent.

The current aggregation path still calls the legacy GitHub retriever in
`artifact_pipeline.py`. The registry invocation above is the integration point
for the shared pipeline.
