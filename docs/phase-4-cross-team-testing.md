# Phase 4 cross-team testing: Team 22 ↔ Team 24

Pod specification: Phase 2 Sections 3–5. Team 22's API description is
[`spec.yaml`](https://github.com/wood297/ECE-30861-Team-22/blob/main/spec.yaml), version
**1.3.0**. The shared operations (`POST /bundles`, `GET /bundles/{bundle_id}`) and their
required fields are unchanged since Phase 2.

## 1. Results

| Direction | Date | Run / passed | Coverage (code · component · system) | Main problem and owner |
|---|---|---|---|---|
| Team 22 tests → Team 24 service | 2026-11-01 | **15 / 15** (0 fail, 0 error, 0 skip) | null · 7/7 · 8/8 | None |
| Team 24 tests → Team 22 service (`team24` profile) | 2026-11-01 | **PENDING re-run after our deploy** (see §6) | null · 7/7 · 8/8 | Two missing shared releases; fixed by Team 22, awaiting deploy |

Code coverage is `null` in both directions. Neither system is instrumented per session, and
both explain this in the `basis` field, as the course spec allows.

## 2. Our services (registry fields)

| Field | Value |
|---|---|
| System base URL | `https://team22.13-58-35-224.sslip.io/system` |
| Tester base URL | `https://team22.13-58-35-224.sslip.io/tester` |
| Intra-pod specification | Phase 2 Sections 3–5; our API: `spec.yaml` 1.3.0 (link above) |
| Authentication | None on either service |
| Time limits | Tester setup 70 s · tester run 150 s · system setup 5 s · each project call 10 s, no retries |
| Deployed version | `GET /system/version` returns `branch@commit` |

**Access and run notes:**

- Bundles, archives and sessions live in memory, and a deploy restarts the service. Tell us your
  test window and we'll pause automatic deploys (commit `deploy/PAUSED`).
- `POST /bundles` answers `202 PENDING` straight away and processes in the background. Poll
  `GET /bundles/{id}` until `COMPLETED`, `PARTIAL_SUCCESS` or `FAILED`; it usually takes under
  a second.
- Our system reads the GitHub file for `v1.2-github-inaccessible` anonymously and caches the
  result, so repeated runs don't hit GitHub's rate limit.
- TLS: our deploy smoke test now connects from Linux with certificate verification on after
  every deploy. Please re-run with verification enabled; the Phase 3 certificate error should be
  gone.

## 3. How to run our tester against your system

```
POST https://team22.13-58-35-224.sslip.io/tester/test/setup
     {"target_url": "https://30861-p3.jscarfo.com", "token": "<your token>"}
  -> 201 {"session_id": "<id>"}

POST https://team22.13-58-35-224.sslip.io/tester/test/run
     {"session_id": "<id>"}
  -> 200 with the full report

GET  https://team22.13-58-35-224.sslip.io/tester/test/report?session_id=<id>
  -> 200 with the stored report (409 while it is still running)
```

- **Options:** `"token"` on setup is sent to your system as `Authorization: Bearer <token>`. On
  run, `"poll_timeout"` (0–60 s, default 5) and `"only"` (a list of test IDs) are optional.
- **Sessions:** each session runs once; start a new one to repeat.
- **Errors:** `{"message": ..., "source": "tester" | "system" | "communication" | "unknown"}`.
- **Command line, after cloning:** `python -m test_runner --url <base url> --token <token>`
  (add `-v` for every check, `--json` for the raw report).

## 4. How we run your tester against our system

```
POST https://30861-p3.jscarfo.com/tester/test/setup
     {"target_url": "https://team22.13-58-35-224.sslip.io/system", "profile": "team24"}
POST https://30861-p3.jscarfo.com/tester/test/run   {"session_id": "<id>"}
```

We use the `team24` profile. Your default (`phase3`) skips AC-05, and `phase4` also fails on your
own system (see §7).

## 5. Shared fixtures

Both systems serve these releases with byte-identical files. Both testers check content by
sha256, so the bytes must match exactly.

| Release | Expected result | Files (sha256, first 12) |
|---|---|---|
| `prd-88 v1.2` | COMPLETED, all PRESENT | risk `0c17a18b18d7`, SBOM `4bf92cff3143`, tests `e369c8058f86`, instructions `2201be0bb4a5` |
| `prd-88 v1.3` | PARTIAL_SUCCESS, Test Logs MISSING | (Phase 3 pod files) |
| `prd-88 v1.4` | PARTIAL_SUCCESS, SBOM INVALID (empty) | (Phase 3 pod files) |
| `prd-88 v2.0` | COMPLETED, all PRESENT | risk `777d7aa91bed`, SBOM `0bbad937a987`, tests `569e92b81e43`, instructions `a11f03c92d2f` |
| `prd-88 v1.2-github-inaccessible` | PARTIAL_SUCCESS, Test Logs INACCESSIBLE, other three PRESENT | risk `dcd2a2a4a9db`, SBOM `334e34837cec`, instructions `0686f12eca5f` |
| `prd-88 v0.0-does-not-exist` | FAILED or 4xx, nothing PRESENT | n/a |

**Source failure (`v1.2-github-inaccessible`):** our Test Logs entry points at
`prithika-sundar/cra-artifact-test` at pinned commit
`f6d381dc6ddb716e1603230a85ea300de462d252`, path
`artifacts/prd-88/v1.2-github-inaccessible/test_logs.md`. That file isn't there. We return
`status: INACCESSIBLE`, `external_source: "GitHub"` and
`source_failure_detail: "Configured GitHub artifact was not found at the pinned commit."`,
matching yours.

## 6. Direction 2: Team 24 tests → Team 22 service

**Final run: PENDING.** Our fix (`phase4-crossteam`, merged to `main`) deploys automatically when
CI passes. We'll re-run your `team24` profile against the deployed commit and fill this in. For
reference, the same code running locally passes our own tester 15/15, including the real GitHub
lookup.

| Run | Version | Session | Result |
|---|---|---|---|
| Baseline before the fix | `main@430a16b` | `88541502-fe67-4e0c-96f0-0bc26acda3d2` | 12 pass, 2 fail |
| After the fix | `main@<commit>` | `<session>` | `<to fill>` |

Baseline failures, both caused by us not serving a release yet:

| Test | Cause | Classification | Fix |
|---|---|---|---|
| AC-05 | `prd-88 v1.2-github-inaccessible` didn't exist, so our system answered FAILED | System (missing shared fixture) | Release added with the GitHub source (§5) |
| AC-06 | `prd-88 v2.0` didn't exist, so there was no download for the "second" release | System (missing shared fixture) | Release added, files byte-identical to yours |

## 7. Direction 1: Team 22 tests → Team 24 service

Run 2026-11-02 01:46 UTC (2026-11-01 20:46 EST), session
`b4dd758e-a185-4990-8209-9b66ecf11cf0`, target `https://30861-p3.jscarfo.com`.
**15 pass, 0 fail, 0 error, 0 skip.**

| Test | What it checks | Outcome |
|---|---|---|
| API-01 | 202, UUID `bundle_id`, PENDING | pass |
| API-02 | Missing required field → 400 | pass |
| API-03 | Field constraints (non-empty strings, JSON body) | pass |
| API-04 | Unknown bundle → 404 | pass |
| API-05 | Response conforms to BundleStatusResponse | pass |
| API-06 | Reaches a terminal status | pass |
| API-07 | Unknown release not reported as collected | pass |
| AC-01 | Artifacts belong to the requested release (`v1.2`, `v1.3`, `v2.0`; no files shared across releases) | pass |
| AC-02 | `v1.2` all PRESENT, COMPLETED | pass |
| AC-03 | `v1.3` Test Logs MISSING, not collected | pass |
| AC-04 | `v1.4` SBOM INVALID, not collected | pass |
| AC-05 | `v1.2-github-inaccessible`: Test Logs INACCESSIBLE with a `source_failure_detail`, PARTIAL_SUCCESS, `download_url` given, other three files unchanged | pass |
| AC-06 | `v1.2` content byte-for-byte | pass |
| AC-07 | Two identical requests give equivalent results | pass |
| AC-08 | Every category listed once with a valid status | pass |

That run was from our updated tester code on the command line. After our deploy, we'll repeat
it from the registered tester URL so the session comes from the deployed service.

## 8. Specification issues to record (Q6.2)

| # | Issue | Effect | Proposed decision |
|---|---|---|---|
| 1 | **MISSING vs INACCESSIBLE** for external sources (REQ-03, REQ-07, AC-05) | Without a rule, a file absent from GitHub could be MISSING on one system and INACCESSIBLE on the other | A configured external file not found at the pinned commit is **INACCESSIBLE**; a category with no file and no source configured is **MISSING**. Both systems already do this. Record in the shared spec. |
| 2 | **Contract version** (Q3.2) | Your tester reports `1.0.0`; our Phase 3 report and spec said `1.2.0` | Agree on one number. Ours is now `1.3.0`. The shared operations and required fields are unchanged, so the pod could keep its shared contract at `1.2.0` and treat `1.3.0` as our implementation's version. |
| 3 | **Category list** (REQ-03, REQ-05, AC-08) | Phase 3: you required 5 (Product Description), we required 4 | Your Phase 4 responses list 4. Please confirm the pod list is the 4 categories. |
| 4 | **Tester default profile** | Your default `phase3` skips AC-05, so graders who use the default won't see it run | Make `team24` (or one complete set) the default. |
| 5 | **`phase4` profile** (HTML fixtures, `phase4-fixture`) | Fails on both systems; your system answers "Product/release is not configured in the source catalog" | Publish the HTML fixtures and expected statuses, or drop the profile until Phase 5. |
| 6 | **`v2.0` file content** | All four `v2.0` files say "Release: v1.3" inside | Intended? We kept the bytes unchanged so hashes match. |
| 7 | **Authentication** (Phase 2 §5.3) | You need a bearer token, we need none | Both testers forward a token, so this blocks nothing. Record it as allowed, or agree on one. |
| 8 | **Content check in AC-05/AC-06 relies on shared bytes** | Any change to a shared fixture breaks the other team's tester | Change shared fixtures only together, and list their sha256 in the spec (§5 above). |

## 9. Raw reports

In our repository at `docs/crossteam-2026-11-01/`:
- `final-us-vs-team24.json`: §7.
- `team24-team24-profile-vs-us-main-430a16b.json`: §6 baseline.
- Earlier runs from the same day, including all three of your profiles.
