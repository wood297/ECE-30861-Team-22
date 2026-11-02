# Phase 4 updates

Everything that changed in the Team 22 system between Phase 3 (deployed `main@66c9b53`,
2026-10-18) and Phase 4 (2026-11-01). Grouped by who did the work, then summarized by
requirement for the Phase 4 report.

| Person | Area | Commits |
|---|---|---|
| Cora Wood | GitHub authentication | `daafde9` |
| Prithika Sundar | GitHub file retrieval, format validation, classifier, first Bedrock adapter, README | `b370384`, `0ead42d`, `c1b39fb` |
| Ethan Harris | Bedrock on gpt-oss, classification in the pipeline, background processing, automatic deployment | `a69a84f`, `fab72cc`, `1127a43`, `f8693bd`, `430a16b` |
| Anthony Sharonov | Cross-team testing, AC-05, shared Phase 4 fixtures, GitHub source catalog, spec 1.3.0 | branch `phase4-crossteam` |

## Cora: GitHub authentication (`daafde9`)

- `github_client.py`: a read-only GitHub client restricted to `https://api.github.com`.
  - Signs in with a personal access token (`GITHUB_TOKEN`) and checks the repository
    (`GITHUB_REPOSITORY`) through `GET /user` and `GET /repos/{owner}/{repo}`.
  - Never follows redirects, so the token can't be forwarded to another host.
  - Errors are sorted into safe codes: `configuration`, `authentication`, `permission`,
    `rate_limited`, `timeout`, `connection`, `repository_access`, `redirect`, `upstream`,
    `upstream_response`. Tokens and upstream error bodies never appear in messages.
  - 10 s timeout, no retries, 1 MiB cap on each response.
- `github_auth_check.py`: `python -m github_auth_check` prints `{"status": "connected", ...}`
  and exits 0, or prints the error and exits 1.
- `.env.example` documents the variable names. `.env` files are git-ignored.
- Tests: `tests/test_github_auth.py`, with fake HTTP responses and no network.

## Prithika: retrieval, validation and classification (`b370384`, `c1b39fb`)

- **File retrieval** (`github_client.get_file`, `github_artifact_retrieval.py`):
  - Reads a file at an explicit Git revision by walking Git trees, then the blob. This
    returns the original bytes unchanged (REQ-08).
  - Only absence from a complete tree counts as "confirmed missing". A plain 404 stays an
    access error, because GitHub hides private repositories behind 404s.
  - `GitHubArtifactRetriever` looks for SBOM (`sbom.json`, `.xml`, `.md`, `.html`) and User
    Instructions (`user_instructions.md`, `.html`) in `artifacts/<product>/<release>/`.
- **Format validation** (`artifact_formats.py`): Markdown, HTML, CycloneDX JSON and CycloneDX
  XML. XML is parsed with `defusedxml`. Empty files, bad UTF-8 and malformed structure are
  rejected with a reason code.
- **Classifier** (`artifact_classifier.py`):
  - Provider-independent: the model is asked which of the four categories a document is.
  - It never sees the expected category or the file name.
  - Answers must be strict JSON, and the evidence must be a quote from the document.
  - Document text is passed as data, never as instructions, to resist prompt injection.
- **First Bedrock adapter** (`bedrock_adapter.py`, Claude Haiku through the Converse API).
  Ethan later replaced it (below).
- **Supporting pieces:**
  - `phase4_artifact_processing.py` chains retrieve → validate → classify.
  - `github_live_check.py` and `GITHUB_LIVE_TEST.md` are an opt-in, read-only live check.
- **Test repository:** `prithika-sundar/cra-artifact-test` (public) holds
  `artifacts/test-product/v1.0/sbom.json` and `user_instructions.md`, the release we want to pull
  from GitHub in Phase 5.
- **README:** full system documentation (`c1b39fb`).
- **Tests:** `test_github_client_files.py`, `test_github_artifact_retrieval.py`,
  `test_artifact_formats.py`, `test_artifact_classifier.py`, `test_phase4_artifact_processing.py`,
  `test_github_live_check.py`.

## Ethan: classification, background processing, automatic deployment

### Bedrock on gpt-oss (`a69a84f`, `430a16b`)

- Our free-plan AWS account has a Bedrock quota of 0 for Claude Haiku, so `bedrock_adapter.py`
  now sends signed requests to the OpenAI-compatible `bedrock-mantle` endpoint with
  `openai.gpt-oss-20b`. The schema is in the prompt, because `response_format` gave malformed
  JSON in live tests.
- `bedrock_live_check.py`: an opt-in check against the real model.
- The server's IAM role lets it call Bedrock (`deploy/aws_setup.sh`). No access keys on the
  server.
- `deploy/cra.env` turns classification on in production (`CRA_BEDROCK_ENABLED=1`).

### Classification in the pipeline (`fab72cc`)

- `artifact_pipeline.py` is split into listing files (`list_fixture_files`) and deciding
  (`decide`), so a new source only has to list its files.
- How a category is decided:
  - A standard file name whose first heading names the category → PRESENT by rule
    (`classified_by: rules`).
  - A standard name without that heading → Bedrock is asked. PRESENT only if it names the same
    category; otherwise INVALID.
  - Non-standard file names in a supported format → Bedrock can claim them for a category
    that's still empty. Two files claiming one category → INVALID.
  - Bedrock only proposes; code decides every status. With Bedrock off or failing, nothing
    becomes PRESENT through it.
- Successful answers are cached by content hash, prompt version and model, so repeat runs agree
  (AC-07). A malformed reply is retried once.
- The zip keeps real file names, and manifest items gain `source_file`, `classified_by` and
  `classification_evidence`.
- `fixtures/bedrock-test/`: seven releases (no headings, renamed files, wrong content,
  unsupported format, HTML and CycloneDX, duplicates, prompt injection).
- `bedrock_accuracy.py`: 32 labelled documents; 0 wrong acceptances in 96 live answers.
- Tests: `tests/test_classification.py` (102 tests), `test_bedrock_adapter.py`,
  `test_bedrock_accuracy.py`, `test_bedrock_live_check.py`.

### Background processing (`fab72cc`)

- `POST /bundles` answers `202 PENDING` straight away, and a thread pool processes the bundle.
  `GET` shows `PROCESSING`, then the final status, which is set last so the manifest and zip are
  ready when a client sees it.
- An unexpected error marks the bundle `FAILED` instead of leaving it stuck.

### Automatic deployment (`1127a43`, `f8693bd`)

- CI's new `deploy` job runs when a commit on `main` passes both the static and test jobs.
  - It uploads a release to S3 and runs it on EC2 through AWS SSM (`deploy/github_deploy.sh`,
    `deploy/remote_deploy.sh`, `deploy/build_release.sh`).
  - Each release installs into its own folder, so a failed install leaves the live app alone.
    Rollback just switches a link back.
  - `deploy/smoke_test.py` checks the public URL, with TLS verification on, after each deploy.
    A failure rolls back automatically.
  - **Committing a `deploy/PAUSED` file stops automatic deploys**, for another team's test
    window. Running the workflow by hand still deploys.
- `GET /version` returns the deployed `branch@commit`.
- The deploy uses a tightly scoped IAM user whose key is a secret of the main-only `production`
  environment. OIDC is blocked by our AWS organization's policy.
- GETTING_STARTED.md and README updated for Phase 4's scope.

## Anthony: cross-team testing and the GitHub source (branch `phase4-crossteam`)

### Cross-team runs and matching Team 24

- Ran both teams' testers in both directions. Team 24's tester has three test sets (profiles):
  `phase3`, `team24` and `phase4`. `team24` is the one their own system passes.
- We failed two of their tests only because we didn't serve two releases they test. Both are
  added, byte for byte the same as theirs (the pod's shared fixtures, Phase 2 §5.3):
  - `fixtures/prd-88/v2.0/`: a second complete release.
  - `fixtures/prd-88/v1.2-github-inaccessible/`: three local files. Test Logs comes from GitHub
    and that source fails.

### GitHub source catalog

- `source_catalog.json` points a category at one file in a GitHub repository at a pinned
  commit. Today it has one entry: `prd-88 v1.2-github-inaccessible` Test Logs →
  `prithika-sundar/cra-artifact-test` at `f6d381d`, path
  `artifacts/prd-88/v1.2-github-inaccessible/test_logs.md`, which doesn't exist there.
- `artifact_pipeline.decide_github()` fetches it through Prithika's client and runs it through
  Ethan's same rules. If GitHub can't supply it, the category is INACCESSIBLE with
  `external_source: GitHub` and "Configured GitHub artifact was not found at the pinned commit."
  That's the same behavior as Team 24.
- **Rule this sets:** a configured external file that isn't at the pinned commit is
  INACCESSIBLE; a category with no file and no source configured is MISSING.
- `github_artifact_retrieval.fetch_configured_artifact()`: reads one configured file for any of
  the four categories at a full 40-character commit SHA, with sanitized error codes.
- `github_client.GitHubConfig(allow_anonymous=True)`: reads public repositories without a token
  (60 requests/hour per IP). `verify_access` still needs a token.
- Results for a pinned commit are cached, behind a lock because bundles now process on threads.
  Temporary failures aren't cached.
- `tests/conftest.py` replaces the GitHub client with an offline fake, so tests never touch the
  network.

### Tester

- **AC-05 runs now instead of being skipped.** It follows Phase 2 AC-05 and the REQ-07 note in
  §5.1: the failed category is INACCESSIBLE, the bundle is PARTIAL_SUCCESS, the failed entry has
  `source_failure_detail`, a `download_url` is still given, and the three retrieved files are
  unchanged.
- AC-01 also checks that no `v1.2` file appears in the `v2.0` bundle and the other way round.
- `oracle.json` has two new releases, `source_failure` and `second`, written by hand from the
  shared fixtures.
- The fake reference system gained a failing source and three broken versions
  (`hide_source_failure`, `silent_source_failure`, `drop_on_source_failure`). AC-05 must fail
  each one.
- Coverage is now complete: component 7/7, system 8/8.

### Spec and docs

- `spec.yaml` is now **1.3.0**:
  - Documents background processing, `GET /version`, the GitHub source, the new releases and
    Ethan's new manifest fields.
  - Updates the INACCESSIBLE and MISSING definitions.
  - The shared operations and their required fields are unchanged.
- TESTING.md and README updated. Cross-team reports are in `docs/crossteam-2026-11-01/`.

## Summary by requirement (for Q1.1 and Q7.1)

| Requirement | Phase 4 status | What changed | Shown by |
|---|---|---|---|
| REQ-01 Release identification | Complete | Unchanged | AC-01 |
| REQ-02 Artifact retrieval | Partial | GitHub source connected through the catalog (failure case used today); HTML and CycloneDX files accepted | AC-02, AC-05 |
| REQ-03 Category status | Partial → stronger | Heading rules plus Bedrock for unclear files; GitHub failures are INACCESSIBLE | AC-02, AC-03, AC-04, AC-05, AC-08 |
| REQ-04 Release association | Complete | Isolation now also checked between `v1.2` and `v2.0` | AC-01, AC-07 |
| REQ-05 Bundle generation | Complete | Real file names in the zip; background processing | AC-02, AC-06, AC-07 |
| REQ-06 Missing or invalid evidence | Partial → stronger | Wrong-content files become INVALID through Bedrock | AC-03, AC-04 |
| REQ-07 External source failure | Complete for one source | A failing GitHub source gives INACCESSIBLE, PARTIAL_SUCCESS, and the other files kept | AC-05 (no longer skipped) |
| REQ-08 Content preservation | Complete | GitHub bytes are kept unchanged; file names preserved | AC-05, AC-06 |

**Still incomplete:**

- Pulling real releases from GitHub (`prithika-sundar/cra-artifact-test`) is Phase 5.
- Jenkins and Confluence are Phase 5.
- Persistent storage: bundles are still in memory.
- Code coverage is still `null`.
- The human Annex VII review for AC-08.

## Test and check results (branch `phase4-crossteam`, 2026-11-01)

- pytest: **573 passed**, 0 skipped. Phase 3 had 129 passed and 1 skipped.
- ruff, mypy, bandit, OpenAPI validation: all pass.
- Cross-team results: see [phase-4-cross-team-testing.md](phase-4-cross-team-testing.md).
