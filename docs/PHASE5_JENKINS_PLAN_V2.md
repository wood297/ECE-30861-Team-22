# Phase 5: Jenkins connector, v2 (on the shared connector kit)

**Replaces** the code and credentials parts of [PHASE5_JENKINS_PLAN.md](PHASE5_JENKINS_PLAN.md) (v1). Still valid from v1: §1 (where Test Logs comes from in the CRA), §2.2–2.3 (the Jenkins folder layout and how a test log gets into Jenkins), and §5 (the `test_logs.md` template).

**Why v1 is replaced.** v1 had our app read Jenkins through its own `urllib` client, with credentials in environment variables and releases in `source_catalog.json`. Since then the team has built a **connector kit** (`person3-bounded-memory`, see `CONNECTORS.md`). Every source is now a small `connectors/<type>.py` class that fetches through `safe_http`, takes its credentials from a per-company connection, and returns one of five outcomes. GitHub (`feature/github-shared-connector`) and Confluence (`codex/confluence-integration`) are already on it. Jenkins is the commented-out line in `REGISTRY`.

---

## 0. Where things stand (checked 2026-11-19)

| Item | Status |
|---|---|
| Jenkins job | `pod/cra-test-logs` exists. Build #1 FAILURE (parameter registration). #2, #3, #4 SUCCESS, all **kept forever**. |
| Pinned builds | #2 `prd-88/v1.5-jenkins` archives `test_logs.md`, sha256 `f5cf3fd8…58173`. #3 `-missing` archives nothing. #4 `-empty` archives a 0-byte file (`e3b0c442…b855`). Both sha256 values **match** `tests/fixtures/jenkins/`. |
| `team22/` folder | Not created. Only needed for the optional team-specific cases (v1 §6 B). |
| Cloudflare | **No longer a blocker, as far as we can tell.** It blocks only Python's default `User-Agent: Python-urllib/3.x`. With `cra-documentation-service`, which `safe_http` always sends, requests reach Jenkins (responses carry `X-Jenkins`). Tested from a laptop through `safe_http` itself. **Not yet tested from EC2** (§5). |
| Connector kit | Contract, `safe_http`, contract tests and `try_fetch` are on `person3-bounded-memory`. |
| Pipeline | **Doesn't call connectors yet** (kit step 2.1). `test_account.json` / the `pod-test` account don't exist yet (step 2.2). Until then a connector is exercised only by its tests, `try_fetch` and its live check. |

---

## 1. What changes from v1

| v1 | v2 |
|---|---|
| `jenkins_client.py` on `urllib`, configured from the environment | `connectors/jenkins.py`, a `BaseConnector` using `ctx.http` only |
| 12 `JenkinsError` codes | The kit's five outcomes: RETRIEVED, NOT_FOUND, AUTH_FAILED, UNAVAILABLE, SOURCE_ERROR |
| `edge_blocked` code for a Cloudflare 403 | AUTH_FAILED (the contract requires every 401/403 to be AUTH_FAILED) with a **different detail line**, so the cause still shows |
| `JENKINS_URL` / `JENKINS_USER` / `JENKINS_API_TOKEN` | Connection settings `url` and `user`, plus a `token` secret field (SSM in production, `CRA_LOCAL_SECRETS` file locally) |
| `/etc/cra/secrets.env` on EC2 | Dropped. The token goes privately to Person 3, who puts it in SSM |
| `source_catalog.json` entries | Release pins on the `pod-test` account (step 2.2). Listed in the PR description until `test_account.json` exists |
| `jenkins_artifact_retrieval.py`, `source_errors.py`, `CONNECTORS` table and `_source_cache` in `artifact_pipeline.py` | Dropped. The pipeline (step 2.1) owns statuses, caching and the INACCESSIBLE mapping |
| `jenkins_auth_check.py` | Dropped. `python -m connectors.try_fetch` does the same job |
| `jenkins_live_check.py` | `connectors/jenkins_live_check.py`, built on the kit like `connectors/github_live_check.py` |
| `/whoAmI` check: "`authenticated` must be true" | Dropped. It was also wrong: Jenkins reports `"authenticated": true` for **anonymous** users too; the real signal is `"anonymous": false` |
| Cloudflare allowlist request to Team 24 | Only if the EC2 check in §5 fails |

**Kept as is:** [jenkins/cra-test-logs.Jenkinsfile](../jenkins/cra-test-logs.Jenkinsfile), the three pinned builds, `tests/fixtures/jenkins/` (with its `-text` `.gitattributes`), and the v1 §5 template.

---

## 2. Housekeeping first

1. **Save the v1 work.** The `jenkins` branch has about 1,300 lines uncommitted, including a merge of the two *older* branches (`feature/dynamic-github-connector`, `codex/confluence-attachment-reader`) that the kit replaces. Commit it as is to a local branch `jenkins-v1-prototype` so nothing is lost. **Don't merge it anywhere.**
2. **New branch** `feature/jenkins-connector` from `origin/person3-bounded-memory`. The PR targets `person3-bounded-memory`, as `CONNECTORS.md` says.
3. **Bring over only** the Jenkinsfile, `tests/fixtures/jenkins/`, and the v1 §5 template as `docs/test-logs-template.md`. Leave out the `fixtures/prd-88/v1.5-jenkins*/` copies: the `fixtures` connector can pin the existing `prd-88/v1.2/*` files directly, so they're settled in step 2.2.

---

## 3. `connectors/jenkins.py`

```python
class JenkinsConnector(BaseConnector):
    type = "jenkins"
    display_name = "Jenkins"
    settings_fields = (
        Field("url", "Jenkins address", kind=URL, help="e.g. https://jenkins.example.com"),
        Field("user", "User name"),
        Field("token", "API token", kind=SECRET),
    )
    ref_fields = (
        Field("job", "Job path", pattern=r"SEG(/SEG){0,4}", help="folders and job, e.g. pod/cra-test-logs"),
        Field("build", "Build number", kind=INTEGER),
        Field("paths", "Artifact paths, tried in order", kind=LIST,
              help="e.g. artifacts/{product}/{release}/test_logs.md"),
    )
```
`SEG` is `[A-Za-z0-9][A-Za-z0-9._-]*`, the same as GitHub's path segment.

**Validation** (call `super()` first in each):
- `url`: https, port 443 only, no user info, query or fragment. A path prefix such as `/jenkins` is allowed. A trailing `/` is removed.
- `build`: a whole number ≥ 1 once placeholders are filled. A string such as `lastSuccessfulBuild` or `{release}` is refused, which is rule 8 (a ref pins something that doesn't change).
- `job` and `paths`: no `..`.

**`allowed_hosts`**: exactly the host of `url`. Nothing else, so a redirect to anywhere is refused.

**`fetch`**: two GETs, each with `Authorization: Basic base64(user:token)` and `Accept: application/json`. Call `ctx.masker.add()` on the encoded header value. `job` `a/b` becomes `/job/a/job/b`, with each segment URL-quoted.

1. `GET {url}/job/…/{build}/api/json?tree=building,result,artifacts%5BrelativePath%5D`
2. Pick the first ref path present in `artifacts[].relativePath`. The listing is complete, so absence is confirmed.
3. `GET {url}/job/…/{build}/artifact/{quoted path}`

| What happens | Outcome | Detail (one line, ours) |
|---|---|---|
| Step 3 returns 200 | RETRIEVED, filename = last path segment, `version = "{job}#{build}"` | n/a |
| Build `result` is FAILURE but the file was archived | RETRIEVED | We collect evidence and don't judge test results |
| Step 1 returns 404 | NOT_FOUND | "Jenkins has no build {n} of {job}, or this account can't see the job." |
| No ref path in the listing (build #3) | NOT_FOUND | "Jenkins build {n} of {job} has none of the pinned artifact paths." |
| Step 3 returns 404 (deleted between the two calls) | NOT_FOUND | as above |
| `building: true` | UNAVAILABLE | "Jenkins build {n} is still running." |
| 403 with `server: cloudflare` and no `x-jenkins` header | AUTH_FAILED | "Cloudflare blocked the request before it reached Jenkins (403)." |
| Any other 401/403, 429, 5xx, 3xx | `result_for_status()` | the kit's wording |
| `SafeHttpError` (timeout, DNS, TLS, too large) | `result_for_http_error()` | the kit's wording |
| Listing isn't JSON, or has the wrong shape | SOURCE_ERROR | "Jenkins returned an invalid build listing." |

An empty file (build #4) comes back RETRIEVED with `b""`. The pipeline, not the connector, turns it into INVALID.

---

## 4. Tests: `tests/test_connector_jenkins.py`

**Contract** (about 20 tests, free): `TestJenkinsContract(HttpConnectorContract)` with
- settings `{"url": "https://jenkins.example.com", "user": "team22"}`
- ref `{"job": "pod/cra-test-logs", "build": 2, "paths": ["artifacts/{product}/{release}/test_logs.md"]}`
- `serve_file`: the listing JSON for `/api/json`, the bytes for `/artifact/`
- `serve_missing`: a listing with `"artifacts": []`

**Our own:**
- folder job paths become `/job/pod/job/cra-test-logs/2/`, and odd characters are quoted
- `build` refuses 0, `-1`, `lastSuccessfulBuild` and `{release}`; `url` refuses `http://`, `:8080`, `user@`, `?q`
- `building: true` → UNAVAILABLE; FAILURE build with the artifact → RETRIEVED
- the first matching candidate path wins; none matching → NOT_FOUND
- a Cloudflare-style 403 gets the Cloudflare detail; a Jenkins 403 gets the normal one
- the Basic header value is masked in logs and details
- a 0-byte artifact → RETRIEVED with `b""`
- `version` is `"pod/cra-test-logs#2"`
- a listing missing `artifacts`, or with non-string paths → SOURCE_ERROR, never an exception

Run `ruff`, `mypy`, `bandit` and `pytest` locally with Python 3.12, as CI does.

---

## 5. Against the real server

1. **Laptop, `try_fetch`**: a `jenkins.local.json` connection plus a `jenkins.secrets.json` holding a laptop token (both git-ignored, `chmod 600` the secrets). Run it for builds 2, 3 and 4. Expect RETRIEVED `f5cf3fd8…`, NOT_FOUND, and RETRIEVED `e3b0c442…`.
2. **`connectors/jenkins_live_check.py`**: the same three cases in one command, comparing outcomes and sha256 against `tests/fixtures/jenkins/`. Exit 0 if all match, 1 on a mismatch, 2 on bad setup.
3. **EC2, Cloudflare check (no token needed)**: from the server, an anonymous `safe_http` GET of `/whoAmI/api/json`. If the response has an `x-jenkins` header, Cloudflare lets our server through and the allowlist request is unnecessary. If it's a Cloudflare 403, send Team 24 [TEAM24_CLOUDFLARE_ALLOWLIST.md](TEAM24_CLOUDFLARE_ALLOWLIST.md), updated to say the block is by User-Agent or by datacenter IP. Do this early: AWS addresses can be scored differently from a home connection.

---

## 6. Hand-off

PR to `person3-bounded-memory` containing:
- `connectors/jenkins.py` and its `REGISTRY` line. The GitHub and Confluence PRs edit the same lines, so whoever merges last resolves a small conflict in `connectors/__init__.py`.
- `tests/test_connector_jenkins.py`
- `connectors/jenkins_live_check.py`, and how to run it
- the Jenkinsfile, `tests/fixtures/jenkins/` and the template

**For `pod-test`**, listed in the PR description:

| | Value |
|---|---|
| Jenkins connection | `{"url": "https://jenk-30861.jscarfo.com", "user": "team22"}`, secret sent privately |
| `prd-88/v1.5-jenkins` Test Logs | `{"job": "pod/cra-test-logs", "build": 2, "paths": ["artifacts/{product}/{release}/test_logs.md"]}` |
| `prd-88/v1.5-jenkins-missing` Test Logs | same, `build: 3` |
| `prd-88/v1.5-jenkins-empty` Test Logs | same, `build: 4` |
| Other three categories, all three releases | `fixtures` connector, pinned to the `prd-88/v1.2/` risk, SBOM and instructions files (byte-identical on both teams' systems) |

**Token:** make a new Jenkins API token named `cra-service` for the deployed service and give it to Person 3 privately. Never put it in the PR, a commit or chat. Keep your laptop token separate so either can be revoked alone.

---

## 7. After the connector (not planned in detail here)

- **Bundler (steps 2.1 / 2.2): ours.** Nobody else was assigned to it. Until the pipeline calls connectors, no bundle shows Jenkins data, so cross-team testing waits on this. It gets its own plan after the connector PR.
- **Cross-team.** The releases and expected statuses in v1 §6 A don't change; only the plumbing does. From v1 §2.1: item 2 (folders) and item 3 (keep forever) are done, item 1 (Cloudflare) waits on §5.3, and items 4 (other teams editing our folders), 5 (read-only account), 6 (uptime) and 7 (shared status rules) are still to ask.

---

## 8. Questions for the team before building

1. The kit cites "the Phase 5 plan" (§2, §7.3, §11, steps 1.0–2.3), but that document isn't in the repo. Can Person 3 commit it?
2. We're taking step 2.1 (pipeline calls connectors) and step 2.2 (`test_account.json`, `pod-test`). Tell the team, so nobody else starts on them, and ask that the GitHub and Confluence PRs go into `person3-bounded-memory` first, since the pipeline work needs all three connectors.
3. With organizations and logins, do Team 24 and the graders still call `POST /bundles` without logging in (served from `pod-test`)? Cross-team testing depends on the answer.

## Verification

1. `pytest`, `ruff`, `mypy` and `bandit` pass on `feature/jenkins-connector` (Python 3.12).
2. `jenkins_live_check` exits 0 on a laptop.
3. The anonymous EC2 probe reaches Jenkins (or Team 24 has applied the allowlist).
4. A wrong token gives AUTH_FAILED, and the token appears nowhere in the output or logs.
