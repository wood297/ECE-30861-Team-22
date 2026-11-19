# Phase 5: Jenkins connector for Test Logs (using Team 24's Jenkins server)

## Context

Phase 4 left Jenkins and Confluence for Phase 5 (phase-4.pdf p.18–19, LIM-02). Our own design (phase-2.pdf §7.1) says the aggregation engine fetches raw artifacts from external services with credentials, and the bundle compiler packages them. Today only GitHub is wired in, through `source_catalog.json` → `decide_github()` in [artifact_pipeline.py](../artifact_pipeline.py#L342-L361).

Goal: the **Test Logs** category for a release can come from a `test_logs.md` archived in a Jenkins build. It goes through the same classify → manifest → zip path as fixtures and GitHub, shows `external_source: "Jenkins"`, and fails safely (Test Logs INACCESSIBLE, the other categories kept) when Jenkins is down or the file is gone. The connector layer is made generic so Confluence slots in later.

Decisions:
- **Team 24 runs the Jenkins server.** We host nothing.
- Our app reads it with an API token. Other teams call our API and never need our Jenkins credentials.
- The `test_logs.md` files are **hand-written**, committed to a public GitHub repo, and archived by a Jenkins job **that we create** (the server started empty). See §2.3.

**Status (2026-11-12):** the code is written; see the README Jenkins row. What's left is manual: the step-by-step checklist is [JENKINS_GETTING_STARTED.md](JENKINS_GETTING_STARTED.md), Team 24's Cloudflare change is [TEAM24_CLOUDFLARE_ALLOWLIST.md](TEAM24_CLOUDFLARE_ALLOWLIST.md), and how this fits with the Confluence and dynamic-GitHub branches is [PHASE5_CONNECTOR_DISCREPANCIES.md](PHASE5_CONNECTOR_DISCREPANCIES.md).

---

## 0. What we have now (checked 2026-11-12)

| Item | Status |
|---|---|
| URL | `https://jenk-30861.jscarfo.com/`. Real certificate behind **Cloudflare**, so the https requirement is met. Jenkins 2.580.2. |
| Account | One shared login, **`team22`** (password shared privately, never in git). It's a normal user, not an admin (`/manage` is 403). |
| What `team22` can do | Read everything; **create items at the top level** (the New Item page opens). Item types offered: Pipeline, Freestyle, Folder, Multibranch, Organization Folder. |
| Jobs and builds | **None.** The server is empty. We create our own jobs (§2.3). |
| Build machine | Only the built-in node: Linux, 2 executors, 8 GB RAM. Shared by every team on the server, so builds must stay small and quick. |
| Anonymous access | Blocked (Jenkins 403). You must log in. |
| **Blocker** | **Cloudflare returns 403 to Python's default HTTP client**, while browsers and curl get through. Our service calls Jenkins from Python, so Team 24 has to let our server through (§2.1, item 1). We won't try to get around their bot protection ourselves. |
| Unverified | Whether `team22` can tick "Keep this build forever", whether `curl` is installed on the build machine, and whether other teams' accounts can edit our folders. The sandbox job in the getting-started guide (step 4) checks the first two. |

---

## 1. Where the four categories come from in the CRA (Regulation (EU) 2024/2847)

Our Phase 1 and 2 reports picked them from **Article 31 (technical documentation) + Annex VII (its contents)**. Neither course spec mentions them.

| Our category | Annex VII point | Supporting provisions |
|---|---|---|
| **SBOM** | VII(2)(b) vulnerability handling "including the software bill of materials"; VII(8) SBOM on a market surveillance authority's request | Annex I Part II(1): machine-readable SBOM covering at least top-level dependencies; Art 13(24) |
| **User Instructions** | VII(1)(d) "user information and instructions as set out in Annex II" | Annex II items 1–9; Art 13(18) |
| **Risk Assessment** | VII(3) cybersecurity risk assessment, "including how the essential cybersecurity requirements set out in Part I of Annex I are applicable" | Art 13(2)–(4) |
| **Test Logs** | **VII(6) "reports of the tests carried out to verify the conformity… with the applicable essential cybersecurity requirements as set out in Parts I and II of Annex I"** | Annex I Part II(3): "effective and regular tests and reviews" |

The act requires test **reports** that show conformity with Annex I Part I (2)(a)–(m) and Part II (1)–(8). It sets **no file format**. The template in §5 is our design, built so each result traces to an Annex I requirement.

Annex VII items we don't collect: (2)(a) architecture, (4) support period, (5) standards applied, (7) the EU declaration of conformity. Team 24's proposed "Product Description" category would cover (1). These are worth raising with the pod.

---

## 2. Team 24's Jenkins server

### 2.1 Still to ask Team 24

1. **Cloudflare (blocking).** Requests from Python's `urllib` get a Cloudflare 403 page (`Server: cloudflare`, no `X-Jenkins` header). Ask them to let through API calls from our EC2 server (`13.58.35.224`), for example with a Cloudflare rule that skips bot checks for that IP on this hostname. Their own system's Jenkins connector will hit the same wall. Our client will send `User-Agent: cra-documentation-service`, as `github_client` already does. `jenkins_live_check` on EC2 confirms the fix.
2. **Folders.** Confirm the layout in §2.2, and that we may create the top-level folders `pod` and `team22`.
3. **Build retention.** Confirm that `team22` can mark builds "Keep this build forever", and that nobody deletes builds in `pod/` or `team22/`. Also confirm there's no global rule that discards old builds.
4. **Who else has accounts.** Can other teams' accounts edit or delete our folders? If so, ask them to restrict `team22/` to us (Folder → Properties, if they use project-based security), or accept the risk and rely on the sha256 check (§6).
5. **Optional: a read-only account `team22-app`** for the deployed service. Today the EC2 token can also create and delete jobs. If they'd rather not, we use a separate, revocable token on `team22` (§3).
6. **Uptime.** The server stays up during grading and cross-team windows, and they tell us before any restart.
7. **Shared rules for the pod cases** (§6): the same status for an empty file, a missing artifact and an unreachable server on both systems.

### 2.2 Folder layout

```
pod/                     shared by Team 22 and Team 24
  cra-test-logs          Pipeline job: the shared cross-team releases (§6 A)
team22/                  ours only
  cra-test-logs          Pipeline job: our own releases (§6 B)
  sandbox-*              throwaway jobs for learning; delete when done
team24/                  theirs, if they want it
```

Folders keep each team's jobs apart and make the API paths readable: `pod/cra-test-logs` becomes `/job/pod/job/cra-test-logs/`.

### 2.3 How a test log gets into Jenkins

We have to create jobs; nothing exists yet. The design keeps the file's true copy in git and treats Jenkins as the archive the CRA service reads from:

1. **Write** the `test_logs.md` by hand (template in §5). Commit it to the public repo `prithika-sundar/cra-artifact-test` at `artifacts/<product>/<release>/test_logs.md`, the repo the GitHub connector already uses. Note the full commit SHA.
2. **Build** the Jenkins job [jenkins/cra-test-logs.Jenkinsfile](../jenkins/cra-test-logs.Jenkinsfile) with parameters `PRODUCT_ID`, `RELEASE_VERSION`, `SOURCE_REPO` and `SOURCE_COMMIT`. The job downloads that exact file at that exact commit, prints its sha256, and archives it at the same relative path. Ticking off `ARCHIVE_FILE` makes a build that archives nothing, which is the "missing" test case.
3. **Pin** the build. Click "Keep this build forever" and put the build number in `source_catalog.json`. The build number now does the job a pinned commit does for GitHub (AC-07 repeatability).
4. **Keep** the same bytes in our repo at `tests/fixtures/jenkins/<path>` (already there for the shared releases), so offline tests and `jenkins_live_check` can compare sha256 values.

Why this design:
- No hand-copied text in Jenkins, so no line-ending or whitespace drift.
- No GitHub credential on the Jenkins server, because the repo is public.
- Nothing to install. The job uses `curl` and `sha256sum` and is pasted inline, so it doesn't need the Git plugin or a git binary.
- Anyone can check the archived bytes against the commit.

The job's build #1 only registers its parameters and fails on purpose. Real builds start at #2.

---

## 3. Access keys: where they go

**Password vs token.** People use the `team22` password **only to log in through the browser**. Programs (our app, curl, the live checks) use **API tokens**, made under the user menu → *Security* → *API Token*. Each token has a name and can be revoked on its own, so make one per place it's used:

| Token name | Used by | Stored in |
|---|---|---|
| `ec2-cra-service` | the deployed app on EC2 | `/etc/cra/secrets.env` on the server |
| `laptop-<yourname>` | each teammate's curl and live checks | your own git-ignored `.env` |

Tokens are shown **once** when created. Jenkins can't show them again, so copy each one straight to where it's stored.

| Where | What | How |
|---|---|---|
| EC2 server | `JENKINS_URL`, `JENKINS_USER=team22`, `JENKINS_API_TOKEN` (and move `GITHUB_TOKEN` here) | New **`/etc/cra/secrets.env`**: root-owned, `chmod 600`, outside the release folder so deploys and rollbacks don't touch it, never in git. Add `EnvironmentFile=-/etc/cra/secrets.env` under the existing `EnvironmentFile=-$APP_DIR/deploy/cra.env` line in [deploy/server_setup.sh:33](../deploy/server_setup.sh#L33). The committed `deploy/cra.env` holds only settings that aren't secret. |
| Laptops | the same three, with your laptop token | `.env` (already git-ignored). Add the names to [.env.example](../.env.example) with empty values. |
| GitHub Actions CI | nothing | pytest fakes Jenkins and never uses the network |
| Team 24 / graders | nothing | They call our API, and our server holds the token |

**Rotation:** revoke the token in Jenkins, make a new one, put it in `secrets.env`, then `sudo systemctl restart cra`.

**The shared password** goes in a password manager or a private team channel, never in the repo, the shared spreadsheet or a screenshot. Anyone with it can create a token, so if it leaks, ask Team 24 to reset it and revoke all tokens.

---

## 4. Code changes

**New `jenkins_client.py`**, mirroring [github_client.py](../github_client.py): `urllib` only, no new dependency.
- `JenkinsError(code)` codes:
  - `configuration`
  - `authentication` (401)
  - `permission` (403 from Jenkins)
  - **`edge_blocked`**: a 403 with `Server: cloudflare` and no `X-Jenkins` header, so a Cloudflare block reads differently from a Jenkins permission problem
  - `not_found`
  - `rate_limited` (429)
  - `timeout`, `connection`, `redirect`
  - `upstream`, `upstream_response`
  - `build_in_progress`
- `@dataclass(frozen=True) JenkinsConfig(url, user, token (repr=False), timeout=10.0)`.
  - `from_environment()` reads `JENKINS_URL`, `JENKINS_USER` and `JENKINS_API_TOKEN`.
  - The URL must be https, unless the host is `localhost` or `127.0.0.1` (for tests). The URL must also have no credentials, query or fragment.
  - The plain-http fallback (`JENKINS_ALLOW_HTTP`) and `JENKINS_CA_BUNDLE` from the earlier draft are **dropped**: the server has a public certificate.
- `JenkinsClient(config, opener=None)`:
  - Sends HTTP Basic `user:token` plus `User-Agent: cra-documentation-service` and `Accept: application/json`.
  - Uses the no-redirect handler pattern from `github_client`, and a 1 MiB body cap.
  - Only ever sends GET, so it needs no CSRF crumb.
  - The token and upstream bodies never appear in errors.
  - `get_artifact(job, build, path) -> bytes`. `job` may be a folder path such as `pod/cra-test-logs`, which becomes `/job/pod/job/cra-test-logs`. Each segment is checked and URL-quoted.
    1. `GET {job}/{build}/api/json?tree=building,result,artifacts[relativePath]`. A 404 raises `not_found`. If `building` is true, raise `build_in_progress`.
    2. If `path` isn't in `artifacts`, raise `ConfirmedArtifactMissing`, because the build listing was read in full. A build whose `result` is FAILURE but still has the file is fine: the artifact is what we collect.
    3. Otherwise `GET {job}/{build}/artifact/{path}` and return the raw bytes.
  - `verify_access(job)`: `GET /whoAmI/api/json` (`authenticated` must be true and `name` must match), then `GET {job}/api/json`.
- Move `ConfirmedArtifactMissing` into `source_errors.py` and re-export it from `github_client`, so existing imports and the identity check in [tests/test_github_client_files.py:73](../tests/test_github_client_files.py#L73) still pass.

**New `jenkins_artifact_retrieval.py`**:
- `fetch_jenkins_artifact(client, *, category, product_id, release_version, job, build: int, path, declared_format) -> RetrievalResult`.
- It reuses `RetrievalResult` and the safe-identifier and error-sanitising logic in [github_artifact_retrieval.py](../github_artifact_retrieval.py#L49-L121). Rename `_identifier` → `safe_identifier` and give `_fetch` a parameter for the source name and error class, so both connectors share it.
- `build` must be a positive integer, so `lastSuccessfulBuild` is rejected.
- A missing artifact gives INACCESSIBLE: "Configured Jenkins artifact was not found in the pinned build."
- `external_source="Jenkins"`, `revision=f"{job}#{build}"`.

**[artifact_pipeline.py](../artifact_pipeline.py)**: replace the GitHub-only branch at L400-403 with a connector table:
```python
CONNECTORS = {"github": (decide_github, "GitHub"), "jenkins": (decide_jenkins, "Jenkins")}
```
- `decide_jenkins()` copies `decide_github()`: fetch through a cache, map non-RETRIEVED to INACCESSIBLE, otherwise pass the bytes to `_decide_standard_file()`.
- Generalise `_github_cache` to `_source_cache`, keyed `(source, *identity)`, and keep `clear_github_cache` as an alias. Only pinned-build results are cached; outages are not.
- `make_jenkins_client()` is the factory tests patch. `JENKINS_TIMEOUT = 3.0`.
- `load_catalog()` rejects an unknown `source`. Adding Confluence later is one more row.

**[source_catalog.json](../source_catalog.json)**: Jenkins entries carry no host or credentials:
```json
"prd-88/v1.5-jenkins": {
  "Test Logs": {"source": "jenkins", "job": "pod/cra-test-logs", "build": 2,
                "path": "artifacts/prd-88/v1.5-jenkins/test_logs.md", "format": "markdown"}
}
```
Matching `fixtures/prd-88/<release>/` folders hold the other three categories (§6). The build numbers above are placeholders until the builds exist.

**Bundle compiler** ([bundle_service.py](../bundle_service.py)): no change. Jenkins artifacts arrive as normal `artifacts[]` entries with their original bytes.

**Jenkins job**: [jenkins/cra-test-logs.Jenkinsfile](../jenkins/cra-test-logs.Jenkinsfile) (written). It's pasted into both `pod/cra-test-logs` and `team22/cra-test-logs`. If we change it, re-paste it into both jobs.

**Docs and spec:**
- [spec.yaml](../spec.yaml#L368-L371): add `Jenkins` to the `external_source` description, alongside the uncommitted edits already there.
- README: add a Jenkins Connector row.
- GETTING_STARTED: Jenkins access, secrets file, rotation. Link to [docs/JENKINS_GETTING_STARTED.md](JENKINS_GETTING_STARTED.md).
- `.env.example`: `JENKINS_URL=`, `JENKINS_USER=`, `JENKINS_API_TOKEN=`.
- New `jenkins_auth_check.py`, `jenkins_live_check.py` and `JENKINS_LIVE_TEST.md`, modelled on the GitHub ones.

---

## 5. What `test_logs.md` should contain

Rules our pipeline enforces: valid UTF-8 Markdown, and the **first H1 contains "Test Logs"** so it's PRESENT by rule without Bedrock ([artifact_pipeline.py:198-210](../artifact_pipeline.py#L198-L210)). The rest is designed to satisfy Annex VII(6).

The file is written and committed **before** the Jenkins build runs, so it can't contain its own build number. It names the job; the catalog records the build.

```markdown
# Test Logs: prd-88 v1.5-jenkins

## Report details
- Product: prd-88
- Release: v1.5-jenkins
- Source commit: 3f2c9e1a7b4d5e6f80912a3b4c5d6e7f8091a2b3
- Archived by: Jenkins job pod/cra-test-logs (build pinned in the evidence catalog)
- Run started: 2026-11-10T14:02:11Z
- Run finished: 2026-11-10T14:03:47Z
- Environment: Ubuntu 24.04, Python 3.12.3, pytest 8.3
- Executed by: automated pipeline (no manual steps)
- Standards applied: none; tests are mapped directly to Annex I (Annex VII(5))

## Summary
| Total | Passed | Failed | Skipped |
|---|---|---|---|
| 9 | 8 | 0 | 1 |

Overall result: PASS

## Coverage of essential requirements (Annex I)
| Requirement | What was tested | Tests | Result |
|---|---|---|---|
| Part I (2)(a) no known exploitable vulnerabilities | dependency scan against SBOM | SEC-01 | PASS |
| Part I (2)(b) secure by default | no default passwords; factory reset | SEC-02, SEC-03 | PASS |
| Part I (2)(c) security updates | signed update accepted, bad signature rejected | SEC-04 | PASS |
| Part I (2)(d) protection from unauthorised access | invalid token returns 401 | SEC-05 | PASS |
| Part I (2)(e) confidentiality | TLS 1.2+ only on the API | SEC-06 | PASS |
| Part I (2)(f) integrity | tampered config rejected | SEC-07 | PASS |
| Part I (2)(h) availability / DoS resilience | rate limit returns 429 | SEC-08 | PASS |
| Part I (2)(m) secure data removal | delete-all wipes user data | SEC-09 | SKIPPED (see Deviations) |
| Part I (2)(g)(i)(j)(k)(l) | not applicable: see risk assessment section 3 | - | N/A |
| Part II (1) SBOM produced | CycloneDX SBOM generated for this build | SBOM-01 | PASS |
| Part II (3) regular security testing | this pipeline runs on every release tag | - | evidence: this report |

## Test results
| ID | Test | Outcome | Duration |
|---|---|---|---|
| SEC-01 | test_no_known_vulnerable_dependencies | PASS | 4.10s |
| SEC-04 | test_signed_update_rejects_bad_signature | PASS | 0.12s |
| ... | ... | ... | ... |

## Security scans
- Dependency scan: pip-audit 2.7, database dated 2026-11-09. 0 known vulnerabilities.

## Deviations and failures
- SEC-09 skipped: the data-wipe endpoint is not built yet. The risk is accepted in risk assessment R-07, and the fix is planned for v1.6.

## Raw output (excerpt)
    [2026-11-10 14:02:15] [PASS] SEC-04 test_signed_update_rejects_bad_signature (0.12s)
    [2026-11-10 14:02:15] [PASS] SEC-05 test_invalid_token_rejected (0.03s)
    9 tests: 8 passed, 0 failed, 1 skipped
```

Each section has a job:
- **Report details** say which version was tested, when and how.
- **Coverage** traces each test to Annex I. Not-applicable rows point to the risk assessment, which is where VII(3) decides applicability.
- **Deviations** link failures and skips to risk decisions.
- **Raw output** is indented, not fenced, so the H1 rule stays simple.

The template is saved in `docs/` as the reference format for both teams.

---

## 6. Cross-team testing on one shared Jenkins server

The key idea: **a tester never reads Jenkins directly.** A tester asks a target system for a bundle and compares the manifest with its oracle; the target system is the one that talks to Jenkins. Two kinds of test logs keep that fair.

### A. Shared pod cases (`pod/cra-test-logs`): identical for both teams

Both teams' catalogs pin the **same job, build and path**, and each system reads it with its own account. The other three categories reuse the **`prd-88 v1.2` fixture files** (risk, SBOM, instructions), which are already byte-identical on both systems (Phase 4 §5). We publish their sha256 values again for the new releases. Either system should then return the same manifest, so one shared oracle entry tests both directions.

| Release | Pinned build (expected) | What the build holds | Expected Test Logs | Bundle |
|---|---|---|---|---|
| `prd-88/v1.5-jenkins` | #2 | the §5 template, from the pinned commit | PRESENT, bytes equal to the published sha256, `external_source: "Jenkins"` | COMPLETED |
| `prd-88/v1.5-jenkins-missing` | #3 | nothing (`ARCHIVE_FILE` unticked) | INACCESSIBLE, "not found in the pinned build" | PARTIAL_SUCCESS |
| `prd-88/v1.5-jenkins-empty` | #4 | a committed empty `test_logs.md` | INVALID | PARTIAL_SUCCESS |

Build #1 is the parameter-registration build. If the numbers come out different, the catalog takes whatever Jenkins actually assigned.

### B. Team-specific cases (`team22/cra-test-logs`): only in our own tests

These are our own product's reports, for example `prd-88/v1.6-jenkins-failures`, a well-formed log that records real test failures. It is still PRESENT, because we collect evidence and don't judge test outcomes. These cases appear only in **our own tester profile** (the Phase 5 same-team report with `coverage.model`), never in the shared oracle. That way neither team's cross-team result depends on logs the other team didn't write.

### Step-by-step setup (who does what)

| # | Step | Who |
|---|---|---|
| 1 | Settle the §2.1 questions; Cloudflare must let both systems' servers through | Team 24 (we ask) |
| 2 | Agree in the pod doc: the three release names, expected statuses, empty-file rule, and that both systems reuse the v1.2 files | Both |
| 3 | Commit `artifacts/prd-88/v1.5-jenkins/test_logs.md` (template) and `artifacts/prd-88/v1.5-jenkins-empty/test_logs.md` (0 bytes) to `prithika-sundar/cra-artifact-test`; note the commit SHA | Us |
| 4 | Create folder `pod` and job `pod/cra-test-logs` from the Jenkinsfile; run build #1 (registers parameters) | Us |
| 5 | Run builds #2–#4 with the parameters in table A; mark each **Keep this build forever**; copy the sha256 from each console log | Us |
| 6 | Post in the pod doc: job, build numbers, paths, the commit SHA, and the sha256 of every file (Test Logs plus the three reused v1.2 files) | Us |
| 7 | Add the three releases to `source_catalog.json` and `fixtures/prd-88/v1.5-jenkins*/`; add `tests/fixtures/jenkins/...` copies; deploy (done in code; build numbers are placeholders 2–4) | Us |
| 8 | Same on their system, pinned to the same builds | Team 24 |
| 9 | Add the three releases to [test_runner/oracle.json](../test_runner/oracle.json) (and theirs), only after step 2 is agreed | Both |
| 10 | Dry run: each team runs its own tester against its own system on the new releases | Both |
| 11 | Agree a test window. Before it: run `jenkins_live_check` on EC2 (all sha256 match), commit `deploy/PAUSED`, confirm Jenkins won't restart | Both |
| 12 | Run both directions as in [Phase 4 §3–§4](phase-4-cross-team-testing.md); record results in a Phase 5 cross-team doc | Both |

### Things that would break cross-team runs, and their guards

- **Someone rebuilds or deletes a pinned build.** Guard: "keep forever", plus `jenkins_live_check` comparing sha256 values before each test window. Re-running the job only adds new build numbers, so it never changes a pinned one.
- **The server or Cloudflare blocks us during a window.** Only Test Logs goes INACCESSIBLE (REQ-07), with detail `(edge_blocked)` or `(connection)`. Outages aren't cached, so the next request retries. AC-02/AC-06 on `v1.5-jenkins` would then fail, so run the live check right before the window.
- **Team 24's status rules differ** (for example, empty file → INVALID). Settle this in the pod doc before testing (step 2).
- **The shared build machine is busy.** Builds only matter at setup time; reading archived artifacts doesn't need an executor.

---

## 7. Testing

**Unit tests (offline, run in CI):**
- `tests/test_jenkins_client.py`, using a mocked opener like [test_github_auth.py](../tests/test_github_auth.py):
  - a table of HTTP statuses mapped to error codes, including Cloudflare 403 → `edge_blocked` and Jenkins 403 → `permission`
  - Basic auth and User-Agent headers sent; token missing from repr and errors
  - redirect refused; body cap enforced
  - folder job paths quoted correctly
  - artifact absent from the listing → `ConfirmedArtifactMissing`
  - `building` → `build_in_progress`
  - https rule, localhost exception
- `tests/test_jenkins_artifact_retrieval.py`: pinned-build validation and outcome mapping.
- `tests/test_pipeline.py`: a `FakeJenkins` like `FakeGitHub` (L199). Cases:
  - PRESENT byte for byte, with `external_source: "Jenkins"`
  - INACCESSIBLE, with the other categories kept
  - empty file → INVALID
  - outage not cached; pinned result cached
  - unknown `source` rejected
- `tests/conftest.py`: the session-scoped `offline_sources` fixture serves the committed copies in `tests/fixtures/jenkins/<path>` (the bytes whose sha256 the pod publishes) and reports `-missing` as absent.

**Live checks (opt-in):**
- `python -m jenkins_auth_check` confirms the token through `/whoAmI` and that `pod/cra-test-logs` and `team22/cra-test-logs` are readable. It reports `edge_blocked` clearly if Cloudflare still blocks us.
- `python jenkins_live_check.py` fetches each pinned artifact and compares its sha256 with `tests/fixtures/jenkins/...`.
- Run both on a laptop first, then on EC2, the address that matters for grading.

**Deployed smoke test:** extend [deploy/smoke_test.py](../deploy/smoke_test.py). `prd-88/v1.5-jenkins` should be COMPLETED, with Test Logs `external_source == "Jenkins"`.

## Verification
1. `pytest` passes offline, including the new tests and the existing acceptance suite. Ruff, mypy and bandit are clean, as in CI.
2. Fill in `/etc/cra/secrets.env`, restart, then run `jenkins_auth_check` and `jenkins_live_check` on EC2 (exit 0). This is where the Cloudflare fix is proven.
3. Deployed: `POST /system/bundles` for each of the three shared releases gives COMPLETED / PARTIAL_SUCCESS / PARTIAL_SUCCESS with Test Logs PRESENT / INACCESSIBLE / INVALID. The downloaded `test_logs.md` matches the published sha256.
4. Bad token, on a laptop: Test Logs is INACCESSIBLE with detail `(authentication)`, the other categories are PRESENT, and the token doesn't appear anywhere in the output.
5. Cross-team dry run: our tester against Team 24's system on the shared releases, and theirs against ours, both match the shared oracle.
