# Phase 5 cross-team testing: Team 22 ↔ Team 24

Pod specification: Phase 2 Sections 3–5. Team 22's API description is
[`spec.yaml`](https://github.com/wood297/ECE-30861-Team-22/blob/main/spec.yaml), version
**1.4.0**. The shared operations (`POST /bundles`, `GET /bundles/{bundle_id}`) and their
required fields are unchanged since 1.2.0. 1.4.0 adds three shared releases whose Test Logs come
from pinned builds on Team 24's Jenkins (§5). Both teams' systems serve them, and both testers
check them.

## 1. Results

All runs on 2026-11-22 between 22:05 and 22:06 UTC (17:05–17:06 EST), against the deployed
services. Team 22's system was running `main@6b0ae3f`.

| Direction | Date | Run / passed | Coverage (code · component · system) | Main problem and owner |
|---|---|---|---|---|
| Team 22 tests → Team 24 service (registered tester, `main@6b0ae3f`) | 2026-11-22 | **15 / 15** (0 fail, 0 error, 0 skip) | 209/763 · 7/7 · 8/8 | None |
| Team 22 tests, with the Phase 5 Jenkins cases (`feature/tester-jenkins-cases`, run from the command line) → Team 24 service | 2026-11-22 | **15 / 15**, including **35 / 35 Jenkins checks** | 285/763 · 7/7 · 8/8 | None. Our registered tester gets these checks once this branch is deployed (§10) |
| Team 24 tests → Team 22 service (`team24` profile) | 2026-11-22 | **14 / 14** (0 fail, 0 error, 0 skip) | null · 7/7 · 8/8 | None. Their suite includes the three Jenkins releases (§6) |

Reference runs of each tester against its own team's system:

| Run | Session | Result | Coverage (code · component · system · model) |
|---|---|---|---|
| Team 22 tests (with Jenkins cases) → Team 22 service | `d0aafa7f-4291-47d8-b442-7d6afd287788` | 15 / 15 | null · 7/7 · 8/8 · **8/8** |
| Team 24 tests (`team24` profile) → Team 24 service | `8b7599e3-1014-4dfd-ba4e-4e029a4aa856` | 14 / 14 | 287/763 · 7/7 · 8/8 · 8/8 |

**Coverage notes:**
- **Code.** Our system reports `null` with a reason, because per-session instrumentation isn't implemented; the interface spec allows this. Team 24's system measures source lines (209–287 of 763 in these runs).
- **Model.** Our tester adds `coverage.model` only when it tests our own system (REQ-01..REQ-08; mapping in [DESIGN_REQUIREMENTS_MAPPING.md](DESIGN_REQUIREMENTS_MAPPING.md)). Team 24's tester also includes `model` (8/8) in its report on **our** system; see §8, issue 1.

**Earlier the same day (21:56 UTC)** our tester got 1 / 15 against Team 24, because we had been given the outdated Phase 3 token. Every project call returned 401, and Team 24's own tester failed the same way with that token. With the current token, both runs above pass. Those reports are kept with an `-old-token` suffix (§9).

## 2. Our services (registry fields)

| Field | Value |
|---|---|
| System base URL | `https://team22.13-58-35-224.sslip.io/system` |
| Tester base URL | `https://team22.13-58-35-224.sslip.io/tester` |
| Intra-pod specification | Phase 2 Sections 3–5; our API: `spec.yaml` 1.4.0 (link above) |
| Authentication | None for the pod API. Optional company logins (Cognito) exist for `/orgs/...` routes only; requests without our own login token get the shared test data, and other bearer tokens are ignored |
| Time limits | Tester setup 70 s · tester run 150 s · system setup 5 s · each project call 10 s, no retries. Bundles that read Jenkins usually finish in 1–2 s, at most about 20 s |
| Rate limit | 120 bundle requests a minute per IP address (429 above it); a full tester run uses about 30 |
| Deployed version | `GET /system/version` returns `branch@commit` |

**Spec conformance of our deployed system.** `python -m test_runner.spec_check --url https://team22.13-58-35-224.sslip.io/system` checks every documented operation's responses against the `spec.yaml` schemas, and every shared release's statuses, sources and bytes. It passed **50 / 50** against `main@6b0ae3f`, including the Jenkins releases fetched live from EC2.

## 3. How to run our tester against your system

Unchanged since Phase 4:

```text
POST https://team22.13-58-35-224.sslip.io/tester/test/setup
     {"target_url": "https://30861-p3.jscarfo.com", "token": "<your token>"}
POST https://team22.13-58-35-224.sslip.io/tester/test/run   {"session_id": "<id>"}
GET  https://team22.13-58-35-224.sslip.io/tester/test/report?session_id=<id>
```

From a clone: `python -m test_runner --url <system base url> --token <token>` (`--json` for the raw report).

## 4. How we run your tester against our system

```text
POST https://30861-p3.jscarfo.com/tester/test/setup
     {"target_url": "https://team22.13-58-35-224.sslip.io/system", "profile": "team24"}
POST https://30861-p3.jscarfo.com/tester/test/run   {"session_id": "<id>"}
```

Your tester also takes an optional `own_system` flag. We didn't send it.

## 5. Shared fixtures

All `prd-88`. The Phase 4 releases are unchanged; their sha256 values are in the Phase 4 doc §5. Phase 5 adds the three Jenkins releases in `spec.yaml` 1.4.0.

| Release | Expected | Source of Test Logs |
|---|---|---|
| `v1.2`, `v2.0` | COMPLETED, all PRESENT | local |
| `v1.3` | PARTIAL_SUCCESS, Test Logs MISSING | none |
| `v1.4` | PARTIAL_SUCCESS, SBOM INVALID (empty) | local |
| `v1.2-github-inaccessible` | PARTIAL_SUCCESS, Test Logs INACCESSIBLE | GitHub, file absent at the pinned commit |
| `v1.5-jenkins` | COMPLETED, all PRESENT | Jenkins `pod/cra-test-logs` build 2, sha256 `f5cf3fd835fdbbe957222dcc18a4fa12d3d192936e20a7eeee7cea22b1658173` |
| `v1.5-jenkins-missing` | PARTIAL_SUCCESS, Test Logs INACCESSIBLE | Jenkins build 3 (archives nothing) |
| `v1.5-jenkins-empty` | PARTIAL_SUCCESS, Test Logs INVALID | Jenkins build 4 (empty file) |
| `v0.0-does-not-exist` | FAILED or 4xx, nothing PRESENT | none |

In the Jenkins releases, the other three categories are byte-identical to the `v1.2` files, and Test Logs must report `external_source: "Jenkins"`. Jenkins is `https://jenk-30861.jscarfo.com` (Team 24's server), and the builds are kept forever.

**How each system gets these Test Logs.** Both systems fetch them live from Jenkins with their own account:
- **Ours** says "Jenkins build 3 of pod/cra-test-logs has none of the pinned artifact paths." for the missing case, and "test_logs.md is empty" for the empty one.
- **Team 24's** says "Configured Jenkins artifact was not archived by the pinned build." and "Artifact must contain non-empty text without NUL bytes."

Different wording, same statuses. That's all the testers compare.

## 6. Direction 2: Team 24 tests → Team 22 service

Run 2026-11-22 22:05 UTC, session `184bf9a7-bf2e-4741-a361-8ce1c2a926d3`, `team24` profile,
target `https://team22.13-58-35-224.sslip.io/system` (`main@6b0ae3f`).
**14 pass, 0 fail, 0 error, 0 skip.** An earlier run at 21:56 UTC (session
`2cfe4b7e-24b1-4528-a343-9e478ffc8c57`) gave the same 14 / 14.

| Test | What it checks | Outcome |
|---|---|---|
| API-01 | 202, UUID and PENDING | pass |
| API-02 | Missing required field → 400 | pass |
| API-03 | Field constraints | pass |
| API-04 | Unknown bundle → 404 | pass |
| API-05 | Malformed bundle ID rejected | pass |
| API-06 | Terminal status and BundleStatusResponse conformance | pass |
| AC-01 | A bundle reflects only the requested release | pass |
| AC-02 | Every category PRESENT, COMPLETED | pass |
| AC-03 | Missing artifact reported as MISSING | pass |
| AC-04 | Invalid artifact reported as INVALID, not collected | pass |
| AC-05 | External source failure reported as INACCESSIBLE: `v1.2-github-inaccessible` **and `v1.5-jenkins-missing`** (statuses, PARTIAL_SUCCESS, failed source identified, diagnostic, failed artifact not collected, other three files unchanged) | pass |
| AC-06 | Content byte-for-byte identical to the source | pass |
| AC-07 | Identical inputs give equivalent results | pass |
| AC-08 | No supported category disappears silently | pass |

Their checks also cover `v1.5-jenkins` and `v1.5-jenkins-empty`. So our Jenkins connector is exercised cross-team against a real Jenkins build: every Test Logs file their tester checked was fetched live from their Jenkins by our deployed system.

## 7. Direction 1: Team 22 tests → Team 24 service

**Registered tester** (`https://team22.13-58-35-224.sslip.io/tester`, `main@6b0ae3f`): run 2026-11-22 22:05 UTC, session `d764c18c-e3b7-4de7-bc8b-180ba27fb722`, target `https://30861-p3.jscarfo.com`. **15 pass, 0 fail, 0 error, 0 skip.** This version checks the Phase 4 releases only.

**Tester with the Phase 5 Jenkins cases** (branch `feature/tester-jenkins-cases`, same code our registered tester gets once deployed): run from the command line at 22:05 UTC, session `4146e83c-6c1e-49d3-b6e8-038877914abf`. **15 pass, 0 fail, 0 error, 0 skip.** Of its checks, **35 concern the Jenkins releases, and all 35 pass.**

| Test | What it checks | Jenkins release added in Phase 5 | Outcome |
|---|---|---|---|
| API-01 … API-07 | Interface: 202 / UUID / PENDING, 400 on missing or invalid fields, 404 for unknown bundles, schema conformance, terminal status, unknown release not collected | n/a | pass |
| AC-01 | Artifacts belong to the requested release; no content leaks between releases | n/a | pass |
| AC-02 | `v1.2` all PRESENT, COMPLETED | `v1.5-jenkins`: all PRESENT, COMPLETED, Test Logs from Jenkins | pass |
| AC-03 | `v1.3` Test Logs MISSING, not collected | n/a | pass |
| AC-04 | `v1.4` SBOM INVALID, not collected | `v1.5-jenkins-empty`: Test Logs INVALID, not collected, from Jenkins | pass |
| AC-05 | `v1.2-github-inaccessible`: Test Logs INACCESSIBLE with a `source_failure_detail`, PARTIAL_SUCCESS, `download_url` given, other three files unchanged | `v1.5-jenkins-missing`: the same checks, plus Test Logs names Jenkins | pass |
| AC-06 | `v1.2` content byte-for-byte (sha256) | `v1.5-jenkins`: all four files, including the Jenkins `test_logs.md` | pass |
| AC-07 | Two identical requests give equivalent results | n/a | pass |
| AC-08 | Every category listed once with a valid status | n/a | pass |

## 8. Specification issues to record (Q6.2)

| # | Issue | Effect | Proposed decision |
|---|---|---|---|
| 1 | **`coverage.model` in cross-team reports.** The interface spec says to include it only when tester and system are the same team, and to omit it otherwise. The Phase 5 handout (Q6.1) asks teams to report "code, component, and system-model coverage" for each direction | Team 24's reports on our system include `model`; ours on theirs omit it | `model` stays out of cross-team tester reports, as the interface spec says, and each team states its own model coverage from its own-team run (ours: 8/8). Team 24 to omit it when `own_system` isn't set |
| 2 | **Live external sources in the shared suite.** Phase 2 §4.2 kept every external service out of the repeatable acceptance suite, so AC-05 only ever tested a GitHub failure | Jenkins integration (Phase 4 LIM-02) was untested across teams | Decided: pinned sources (build number, commit SHA, attachment version) count as controlled sources. Three Jenkins releases are shared from `spec.yaml` 1.4.0, and both testers check them (Team 24's already, ours once deployed). Agreement: [date and channel] |
| 3 | **Authentication failures as test outcomes.** With a rejected token, both testers reported every test as `fail`, and our API-07 counted the 401 as a pass | A credential problem looks like 14 behavior defects | Report 401/403 on a project call as `error` with source `tester` (the tester was given a credential the target refuses), and never as a pass |
| 4 | **Token handover.** We first had the Phase 3 token, which Team 24's system no longer accepts | The first direction-1 run evaluated nothing | Share the current token privately before each test window (never in the registry or a doc), and say when it changes |

## 9. Raw reports

In our repository at `docs/crossteam-2026-11-22/`:

| File | Run |
|---|---|
| `us-deployed-tester-vs-team24.json` | §7: our registered tester → your service, 15 / 15 |
| `us-branch-tester-vs-team24.json` | §7: our tester with the Jenkins cases → your service, 15 / 15 (35 Jenkins checks) |
| `team24-tester-vs-us-rerun.json` | §6: your tester → our service, 14 / 14 |
| `team24-tester-vs-us.json` | Your tester → our service, 21:56 UTC, 14 / 14 |
| `us-branch-tester-vs-us.json` | Our tester with the Jenkins cases → our service, 15 / 15, model 8/8 |
| `us-tester-vs-us.json` | Our registered tester → our service, 21:55 UTC, 15 / 15, model 8/8 |
| `team24-tester-vs-team24-rerun.json` | Your tester → your service, 14 / 14 |
| `us-tester-vs-team24-old-token.json` | Our tester → your service with the outdated token, 1 / 15 (all 401) |
| `team24-tester-vs-team24-old-token.json` | Your tester → your service with the outdated token, 0 / 14 (all 401) |

## 10. Still to do

| # | Step | Owner |
|---|---|---|
| 1 | Deploy `feature/tester-jenkins-cases`, so the registered tester includes the Jenkins cases, then re-run direction 1 from it and update §1 and §7 | Team 22 |
| 2 | Fill in the 1.4.0 agreement date and channel in `spec.yaml` and issue 2 | Both |
| 3 | Agree on issues 1, 3 and 4 | Both |
