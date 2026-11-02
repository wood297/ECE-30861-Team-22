# Phase 4 plan: match Team 24's GitHub integration and test suite

> **Superseded.** This was the working plan from 2026-11-01. What was actually built is in
> [phase4-updates.md](phase4-updates.md), and the results are in
> [phase-4-cross-team-testing.md](phase-4-cross-team-testing.md).

Written 2026-11-01 after running both teams' testers against both deployed systems.
Raw reports are in [crossteam-2026-11-01/](crossteam-2026-11-01/).

**Update, same night:** sections 3 and 4 are implemented on `person2-retrieval` (not yet
committed or deployed). `v1.2-github-inaccessible`'s Test Logs is read from
`prithika-sundar/cra-artifact-test` at pinned commit `f6d381d`, set in `source_catalog.json`.
Results:

- Our updated tester → Team 24's deployed system: **15/15 pass**, 0 skipped (session `72f12f24-f43f-4505-8613-483d243ed06b`).
- Our updated tester → our system running locally with real GitHub calls: **15/15 pass**.
- pytest: 435 passed, 0 skipped; ruff, mypy and bandit clean.
- Team 24's tester → our system can only be checked after a deploy.

## 1. Where we stand

Team 24's tester takes a `profile` on `POST /test/setup` (`phase3`, `phase4` or `team24`).

| Run | Session | Result |
|---|---|---|
| Team 24 tester, `phase3` profile → our system | `a72a88f4-7d1e-4258-acb8-d0e5f49339c4` | 13 pass, 1 skip (AC-05) |
| Team 24 tester, **`team24` profile** → our system | `eee04b95-46ab-4862-8b21-a8ba6cf7fdc6` | **12 pass, 2 fail (AC-05, AC-06)** |
| Team 24 tester, `team24` profile → Team 24 system | `c8f957d7-da0e-4b54-a120-036e125c345a` | 14 pass |
| Team 24 tester, `phase4` profile → our system | `b2ffeffc-5793-4d5a-b7b7-6339a664f13a` | 7 pass, 7 fail |
| Team 24 tester, `phase4` profile → Team 24 system | `f0613966-34f9-4167-bc8b-bb290124edd4` | 8 pass, 6 fail (their own system fails it too) |
| Our tester → Team 24 system | `4df21703-284e-4dbd-8227-fd3216e1dbb2` | 14 pass, 1 skip (AC-05) |

The **`team24` profile is the one to target now**: it's the only Phase 4 suite their own system
passes. The `phase4` profile (HTML fixtures under product `phase4-fixture`) isn't working on
their side yet. Their system answers "Product/release is not configured in the source catalog"
for every release, so there's nothing to match yet (see section 7).

## 2. How Team 24's GitHub integration works

What we could see through their API and download zips:

- **A source catalog** maps each (product, release, category) to a source. Most entries are
  local files (`external_source: "Controlled Source"`). Some point at GitHub.
- **GitHub entries name one file at a pinned commit.** If the file isn't at that commit, the
  category is **`INACCESSIBLE`** (not `MISSING`), with
  `source_failure_detail: "Configured GitHub artifact was not found at the pinned commit."` and
  `external_source: "GitHub"`. `MISSING` is only for categories with no catalog entry at all
  (e.g. `v1.3` Test Logs).
- **The only GitHub case their suite exercises is the failure path:**
  `prd-88 v1.2-github-inaccessible` has Risk Assessment, User Instructions and SBOM from local
  files (PRESENT) and Test Logs from GitHub (INACCESSIBLE). The bundle is `PARTIAL_SUCCESS` and
  the three good files are in the download. No test checks a successful GitHub fetch.
- **Their AC-05 checks:** the Test Logs status, `PARTIAL_SUCCESS`, the failed source is named,
  there's a diagnostic, the failed artifact isn't collected, the zip is still downloadable, and
  **sha256 of the three surviving files equals their fixture bytes**.
- **A new release `prd-88 v2.0`** is used by AC-01 (two releases must have different manifests)
  and AC-06 ("second" release hashes). All four categories are PRESENT.

Why we fail today: we don't have `v2.0` or `v1.2-github-inaccessible`, so both come back
`FAILED` with no download. That fails AC-06 (second release) and AC-05.

## 3. Changes to our system (to pass the `team24` profile 14/14)

Order matters: steps 1-3 alone fix AC-06; step 4 fixes AC-05.

1. **Add the `v2.0` fixtures** to `fixtures/prd-88/v2.0/` under our file names, byte for byte
   from Team 24's zip. Don't retype them or let an editor touch line endings.

   | Our file | Team 24 file | sha256 (first 16) | bytes |
   |---|---|---|---|
   | `risk_assessment.md` | `risk.md` | `777d7aa91beda3ce` | 110 |
   | `user_instructions.md` | `instructions.md` | `a11f03c92d2fbb5e` | 121 |
   | `sbom.md` | `sbom.md` | `0bbad937a987d052` | 163 |
   | `test_logs.md` | `tests.md` | `569e92b81e43e5e4` | 111 |

   Their `v2.0` files say "Release: v1.3" inside. That looks like a copy-paste slip on their
   side. Copy the bytes as they are (the hashes must match) and tell them (section 7).
2. **Add the three local files for `v1.2-github-inaccessible`** to
   `fixtures/prd-88/v1.2-github-inaccessible/`: `risk_assessment.md` (`dcd2a2a4a9db1892`, 123 B),
   `user_instructions.md` (`0686f12eca5f649f`, 134 B), `sbom.md` (`334e34837cecbd72`, 176 B).
   Leave out `test_logs.md`.
3. **Add a source catalog** (e.g. `source_catalog.json`). Anything not listed keeps today's
   behavior (read `fixtures/<product>/<release>/<file>`). A GitHub entry looks like:

   ```json
   {"prd-88": {"v1.2-github-inaccessible": {"Test Logs": {
     "source": "github",
     "repository": "wood297/ECE-30861-Team-22",
     "commit": "66c9b53f1cf108b3a7bbb85a7a0035a5bf338c19",
     "path": "fixtures/prd-88/v1.2-github-inaccessible/test_logs.md",
     "format": "markdown"
   }}}}
   ```

   The commit is main's current head, where that path doesn't exist. Commits never change, so
   this always fails the same way (good for AC-07).
4. **Use it in `artifact_pipeline.retrieve_and_classify`.** For a GitHub entry, call
   `github_artifact_retrieval.fetch_configured_artifact(...)` (already written, see section 8),
   then:
   - `RETRIEVED` → run the same UTF-8 and empty checks as `classify_file` on the bytes.
   - `INACCESSIBLE` → manifest item with `status: INACCESSIBLE`, `external_source: "GitHub"` and
     its `source_failure_detail`. No artifact is added.

   `final_status` and `build_archive` already give `PARTIAL_SUCCESS` and leave unsatisfied
   categories out of the zip, so nothing changes in `bundle_service.py`.
5. **Server config:** put a fine-grained read-only `GITHUB_TOKEN` and `GITHUB_REPOSITORY` in the
   systemd service's environment file on EC2. Never put them in git. The repo is public, but
   without a token GitHub allows only 60 requests/hour per IP, and each fetch costs 3-4 requests.
   `GitHubConfig` currently requires a token.
6. **Timing:** `POST /bundles` runs the whole pipeline before it replies, and testers time out
   each call after about 10 s (ours: 10 s). One GitHub fetch is several requests with a 10 s
   timeout each. Lower `GitHubConfig.timeout` to about 3 s now, and do the planned async
   processing (reply `202 PENDING` immediately, finish in the background) before adding more
   GitHub sources.
7. **Tests:** pipeline tests with a fake GitHub client (retrieved, not found, auth failure),
   and an API test that `v1.2-github-inaccessible` gives `PARTIAL_SUCCESS` with Test Logs
   INACCESSIBLE and the other three byte-identical.

## 4. Changes to our tester (so our suite checks the same things as theirs)

1. **Add both new releases to `test_runner/oracle.json`**, written by hand from the hashes above:
   `second` = `prd-88 v2.0` (all PRESENT, COMPLETED), and `source_failure` =
   `prd-88 v1.2-github-inaccessible` (Test Logs INACCESSIBLE, others PRESENT, PARTIAL_SUCCESS).
   Put the four `v2.0` files and three `v1.2-github-inaccessible` files in the tester's fixture
   folder so AC-06 can hash them.
2. **Turn AC-05 from skip into a real test** with the same checks Team 24 uses (section 2).
   This makes component coverage 7/7 and system coverage 8/8.
3. **Extend AC-01 and AC-06** to also use `v2.0`, as theirs do.
4. **Make sure the tester can still fail:** give the reference system in `tests/fake_systems.py`
   a source-failure release, and add broken versions that hide the failure (report PRESENT or
   MISSING, or return COMPLETED). Add matching cases to `tests/test_suite_validity.py`.
5. Update TESTING.md: remove the AC-05 "skipped" gap and list the new fixtures.

## 5. Bedrock: what has to change before it touches bundle statuses

Today the Bedrock classifier isn't called by the API, so it can't affect either test suite.
If it's wired in as-is, it is likely to **break tests that pass now**:

- **Speed.** Up to four Bedrock calls (30 s read timeout each) inside a synchronous
  `POST /bundles` will go past the testers' per-call timeouts. That would make API-01 and every
  AC an `error` with source `communication`.
- **Repeatability (AC-07).** Temperature 0 doesn't guarantee identical answers, and a timeout on
  one run but not the next changes the status. Either one makes two runs disagree.
- **False INVALIDs on the shared fixtures.** The fixtures are tiny (110-200 bytes), Team 24's
  SBOM files literally say "This Markdown fixture is not a CycloneDX SBOM", and their `v2.0`
  files claim to be `v1.3`. An `AMBIGUOUS` or wrong-category answer on any of these, mapped to
  INVALID, fails AC-02, AC-05 and AC-06.
- **Phase 2 §4.2 says** the core acceptance tests "will not depend on nondeterministic GenAI
  behavior."

What to do:

1. Keep Bedrock **off by default** behind an env flag (e.g. `CRA_LLM_CLASSIFICATION=1`).
2. When it's on, only a clear **`INVALID` decision** (it names a different category) may turn a
   format-valid file into `INVALID`. `AMBIGUOUS` and `FAILURE` (timeouts, provider errors) keep
   the file `PRESENT` and add a note in `source_failure_detail`. The pod has to agree on this
   mapping, since it changes what INVALID means (REQ-03/REQ-06).
3. Cache decisions by file sha256 so the same bytes always get the same answer.
4. Only call it after async processing exists (section 3, step 6).
5. EC2 needs an IAM role allowed to call `bedrock:InvokeModel`, plus `BEDROCK_MODEL_ID` and
   `AWS_REGION`. No access keys in files.
6. Team 24's `phase4` profile expects `phase4-fixture html-invalid` to report Risk Assessment
   as INVALID. Ask them why it's invalid (section 7). If it's "wrong content for the category",
   that's the one case that needs the classifier or a fixed rule.

## 6. The urllib / TLS problem

Team 24 reported on 2026-10-18 that Python clients got `CERTIFICATE_VERIFY_FAILED: certificate
has expired` from our server, and they turned verification off to finish.

**It doesn't reproduce today.** On 2026-11-01, plain `urllib` with verification on reached
`/system/health` from macOS system Python (LibreSSL 2.8.3), Homebrew Python 3.12 (OpenSSL 3.6.3)
and the certifi 2026.10.23 bundle. Team 24's deployed tester also ran against us three times
with zero communication errors.

The chain we serve is in date at every level: our certificate (Oct 18 2026 – Jan 16 2027) ←
Let's Encrypt YE1 (to 2028) ← Root YE (to 2032) ← ISRG Root X2 (to 2032) ← ISRG Root X1. Our
certificate was issued at 04:38 UTC on Oct 18, the same day as their run, so the most likely
explanation is a temporary state during issuance, or an old trust store on their machine. We
can't prove which.

Plan:

1. Ask Team 24 to re-run from the machine that failed, with verification on, and send its
   Python, OpenSSL and certifi versions.
2. Add a CI job (Ubuntu, Python 3.12, plain `urllib`, verification on) that calls
   `https://team22.13-58-35-224.sslip.io/system/health` and `/tester/`. That checks from Linux
   on every push, which is what other teams use.
3. Re-check after the certificate renews. It expires Jan 16 2027 and Caddy renews about 30 days
   before.
4. If it comes back, set Caddy's `preferred_chains` to serve a shorter chain to ISRG Root X1,
   then re-run step 2.
5. Make our tester more robust: build its SSL context from certifi
   (`ssl.create_default_context(cafile=certifi.where())`), since python.org macOS builds ship
   without CA certificates. Also send an explicit `User-Agent`, since Team 24 is behind
   Cloudflare, which sometimes challenges the default `Python-urllib` one. TLS failures already
   show up as `error` with source `communication`, which is correct.

## 7. Questions for Team 24

1. **Phase 4 fixtures:** can you publish the `phase4-fixture` files (`html-v1`, `html-v2`,
   `html-missing`, `html-invalid`, `html-inaccessible`) and the expected statuses? Your own
   system doesn't serve them yet. What makes `html-invalid`'s Risk Assessment invalid?
2. **MISSING vs INACCESSIBLE:** please confirm the rule. A GitHub file named in the catalog but
   not at the pinned commit is INACCESSIBLE; a category with no catalog entry is MISSING. Then
   record it in the shared spec.
3. **`v2.0` content says "Release: v1.3".** Intended?
4. **Bedrock/LLM:** do you use one for statuses? Agree that AMBIGUOUS and provider failures
   never turn PRESENT into INVALID.
5. **TLS:** re-run with verification on (section 6).
6. **Category list:** still 4 vs 5 (Product Description). Settle before graded runs.

## 8. Already done on branch `person2-retrieval` (not committed)

- `github_artifact_retrieval.py`: new `fetch_configured_artifact()`. It reads one
  catalog-configured file at a pinned 40-character commit SHA, for any of the four categories,
  and matches Team 24's behavior: a file absent at that commit is INACCESSIBLE with their exact
  message, `external_source` is `"GitHub"`, and errors are cleaned so no tokens or upstream text
  leak. The existing directory search now shares the same failure handling, so its behavior is
  unchanged.
  - Checked live against GitHub (read-only, public repo): the real file at commit `66c9b53`
    came back byte-for-byte (158 bytes), and a missing path came back INACCESSIBLE with the
    expected message.
- `tests/test_github_artifact_retrieval.py`: 19 new tests for the function above (all mocked).
- CI fixes so this branch passes the static job: the 2 mypy errors in
  `phase4_artifact_processing.py` and the 4 ruff errors in `tests/test_bedrock_adapter.py` and
  `tests/test_github_auth.py`.
- Result: 423 passed, 1 skipped (our original 129 + 1 skip unchanged); ruff, mypy and bandit
  all pass.
