# Automated testing

This file covers how the testing works and why it is built this way. For step-by-step setup
and deployment instructions, see [GETTING_STARTED.md](GETTING_STARTED.md).

There are two parts, matching the course's *Cross-team Testing Interface*:

- **Tester** (`test_runner/`): our automated test suite. It runs the pod acceptance suite
  against any team's system. You can use it as a web service or from the command line.
- **System** (`app.py`): our project implementation. Besides the pod API (`POST /bundles`,
  `GET /bundles/{id}`), it exposes `/test/setup` and `/test/report` so any team's tester
  can prepare it and read its coverage.

Install once: `pip install -r requirements.txt`

## How a test session works

1. Someone calls our tester's `POST /test/setup` with `{"target_url": "<system base url>"}`.
2. The tester makes a new session ID (a UUID) and sends `{"session_id": ..., "setup": {}}` to the
   system's `POST /test/setup`. It returns `201 {"session_id": ...}` only after the system answers
   with the same ID.
3. Someone calls our tester's `POST /test/run` with `{"session_id": ...}`. The tester runs every
   test in order through the pod API, reads code coverage from the system's
   `GET /test/report?session_id=...`, and returns one report.
4. `GET /test/report?session_id=...` on the tester returns that stored report again without
   re-running anything. Each session runs once; to repeat the suite, start a new session.

## Running the tester

From the command line (steps 1-3 in one go):

```bash
python app.py                                          # system under test on :5000
python -m test_runner --url http://localhost:5000      # prints PASS/FAIL/ERR/SKIP per test
python -m test_runner --url <url> -v                   # show every check
python -m test_runner --url <url> --json               # raw JSON report
python -m test_runner --url <url> --only AC-02 AC-03   # subset
python -m test_runner --url <url> --no-setup           # system without /test/setup
```

Exit codes: `0` means nothing failed or errored, `1` means a test failed or errored, and `2` means
system setup failed (including an unreachable system).

As a web service:

```bash
python -m test_runner.service --port 5001              # development
gunicorn --workers 1 --threads 8 --timeout 180 --bind 0.0.0.0:8000 wsgi:application   # production
```

In production, `wsgi.py` serves the system at `/system` and the tester at `/tester` on one host.

### Tester endpoints

| Endpoint | Body | Success | Errors |
|---|---|---|---|
| `POST /test/setup` | `{"target_url": "<base url>"}` | `201 {"session_id"}` once the system is ready | `400` bad body or URL. If system setup fails, its own status, `message` and `source` are passed back. `500` with source `communication` if the system can't be reached. |
| `POST /test/run` | `{"session_id": "..."}` | `200` with the full suite report | `400` bad body, `404` unknown session, `409` session already ran or is running, `500` with source `tester` if the tester aborts |
| `GET /test/report` | `?session_id=...` | `200` with the stored report | `400` missing ID, `404` unknown session, `409` suite not finished |

Optional extras: `"token"` on setup (sent to the target as `Authorization: Bearer <token>`), and
`"poll_timeout"` (0-60 s, default 5) and `"only"` (list of test IDs) on run. Every error body is
`{"message": "...", "source": "tester" | "system" | "communication" | "unknown"}`.

### The report

```json
{
  "session_id": "0aec3e90-...",
  "target_url": "https://example.com/system",
  "results": [
    {"test_id": "AC-03", "outcome": "pass", "message": "Missing required artifact is reported as MISSING. All observable checks passed (8).",
     "title": "...", "requirements": ["REQ-03", "REQ-06"], "components": ["classification"], "checks": [...], "duration_ms": 3.1},
    {"test_id": "API-03", "outcome": "error", "source": "system", "message": "The target reported an internal failure: POST /bundles returned HTTP 500: non-JSON body"}
  ],
  "coverage": {
    "code":      {"covered": null, "total": null, "basis": "Source lines; code coverage unavailable: ..."},
    "component": {"covered": 6, "total": 7, "basis": "Components listed in the pod specification ..."},
    "system":    {"covered": 7, "total": 8, "basis": "Acceptance criteria AC-01..AC-08 ..."}
  },
  "summary": {"pass": 11, "fail": 2, "error": 1, "skip": 1, "total": 15},
  "passed": false
}
```

- `outcome` is one of four values:
  - `pass`: behaved as expected.
  - `fail`: the response could be evaluated and was wrong.
  - `error`: could not be evaluated; `source` says why.
  - `skip`: not executed.
- `source` follows the spec:
  - A 5xx response from the target counts as `system`.
  - No response at all (refused, timed out, dropped) counts as `communication`.
  - A problem inside our tester counts as `tester`.
- `title`, `requirements`, `components`, `checks` and `duration_ms` are extra detail. The spec allows
  extra fields.
- **Coverage** counts what the tests exercised, not whether they passed:
  - `code`: copied from the system's `/test/report`. If the system can't provide it, both counts
    are `null` and `basis` says why.
  - `component`: how many of the 7 components in `oracle.json` were exercised by at least one test
    that ran (pass or fail).
  - `system`: how many of the acceptance criteria AC-01 to AC-08 had their test run.
- If the tester aborts, the report has `"aborted": true`. The test that was interrupted is an
  `error` with source `tester`, and every test after it is a `skip`.

## Testing endpoints on our system

| Endpoint | Effect |
|---|---|
| `POST /test/setup` | Body `{"session_id": "...", "setup": {}}`. Registers the session and checks the fixture source exists. `201 {"session_id", "fixtures"}`, `400` bad body, `409` ID already used, `500` fixtures missing. |
| `GET /test/report` | `?session_id=...`. Returns `{"session_id", "coverage": {"code": {...}}}`. Counts are `null` for now (see gaps). `400` / `404` for a missing or unknown ID. |
| `POST /test/selftest` | Not part of the course interface. Runs `pytest tests/` in a subprocess and returns a per-test report. |
| `GET /test/selftest` | Returns the last self-test report. `404` before the first run. |

Locally, `pytest` runs the same suite.

## What the acceptance suite checks

The suite uses only `POST /bundles`, `GET /bundles/{id}` and a bundle's `download_url`. Every
`POST /bundles` body also carries the `session_id`, so a system can tie requests to a session.
Expected results come from `test_runner/oracle.json`, which was written by hand for the fixtures
in `fixtures/`.

| Fixture | Scenario |
|---|---|
| `prd-88 v1.2` | All four categories are present (complete). |
| `prd-88 v1.3` | Test Logs is missing. |
| `prd-88 v1.4` | The SBOM file is empty (invalid). |
| `prd-88 v0.0-does-not-exist` | Unknown release. |

| ID | Checks |
|---|---|
| API-01 to API-07 | Section 5 contract: 202 with a UUID and PENDING, 400 on bad input, 404 on unknown id, response schema, terminal status reached, unknown release not reported as collected |
| AC-01 to AC-08 | Phase 2 section 4.1. AC-05 is skipped until Phase 4 because the single controlled source cannot be made to fail through the API. AC-08 automates only the coverage check; the human inspection still applies. |

Artifact content (AC-06) is read from `artifacts[].content` or from `download_url`, which can be a zip
or JSON. Category names are compared ignoring case and whitespace.

## Time limits to register

| Limit | Value | Why |
|---|---|---|
| Tester setup | 70 s | Waits up to 60 s for the system's setup, which allows for a cold start. |
| Tester run | 150 s | All target calls share a 120 s budget (`RUN_BUDGET` in `suite.py`). Reading coverage takes at most 5 s more. Against a healthy target a run takes about 20 s, because the suite waits up to `poll_timeout` (5 s) for each of the 4 bundles that never finish today. |
| System setup | 5 s | Our setup only registers the session and checks that the fixture folder exists. |

Project calls time out after 10 s each and are never retried. Once the run budget is used up, the
remaining tests are reported as `error` with source `communication` and are not sent.

## Design decisions

### Two roles, one host

The tester and the system are separate Flask apps, so our system can be tested by any tester and
our tester can test any system. `wsgi.py` mounts them at `/system` and `/tester` on one host. The
spec allows that: tester and system URLs only need different paths.

Run a single gunicorn worker with several threads:

- Sessions and reports live in memory, so every request for a session has to reach the same
  process.
- The threads let the tester call the system on the same host while a run is still in progress.

### Sessions follow the spec exactly

- Every tester setup creates a fresh UUID, and a session can run only once.
- The tester rejects a second or concurrent run with 409, even after an abort.
- Invalid run requests (bad `poll_timeout` or `only`) are rejected before they use up the session.
- Reports never change once stored, and are kept until the process stops.
- The system rejects a reused session ID with 409.

### System setup no longer clears anything

The old `/test/setup` wiped every stored bundle. With sessions that is wrong: one team's setup
would erase another team's in-progress run. Bundles are already isolated by their own UUID
(Phase 2 §5.3), so setup only registers the session.

### Error sources are decided in one place

`Context.request()` in `suite.py` wraps every target call. If there is no response, it raises a
`communication` error. If the response is a 5xx, it raises a `system` error. Any other exception
inside a test becomes a `tester` error for that test only, and the remaining tests still run.
Only a problem that stops the tester itself ends the suite early: an unreadable `oracle.json` or
missing fixture files.

### Expected results are fixed in advance

Phase 2 §4.2 requires expected outputs to be decided before any implementation runs.
`oracle.json` was written by hand. If the expected results came from our own code, they would
simply repeat our bugs. The component list used for component coverage also lives there.

### Works with targets that don't fully match

- Where the spec allows more than one valid answer, the suite accepts each:
  - Content can come from `artifacts[].content` or from `download_url`.
  - An unknown release can be rejected with a 4xx or come back as a `FAILED` bundle.
- Checks that can't be seen through a team's API are marked "n/a" rather than failed.
- The spec makes bundle creation asynchronous (202 + `PENDING`), so the tester polls
  `GET /bundles/{id}` until the status is final or `poll_timeout` runs out.

### Our self-test runs pytest in a subprocess

If tests ran inside the live server, they would overwrite its real state. The subprocess sets
`CRA_SELFTEST_RUNNING`. While that variable is set, `/test/selftest` refuses with 409, so a test
can never start another self-test in a loop. The self-test moved from `/test/run` to
`/test/selftest`, because the spec reserves the system's `/test/report` for coverage.

### The tester is tested too

A test runner that always says "pass" is worse than none. `tests/fake_systems.py` contains a
small system that follows the spec exactly, including `/test/setup` and `/test/report`, and the
suite must fully pass against it.

It also has broken variants, and for each one the matching test has to fail or error:

- One serves the wrong release.
- One changes file content.
- One accepts empty files.
- One hides missing categories.
- One always reports `COMPLETED`.
- One skips input validation.
- One crashes with a 500.
- One has no test endpoints.

`tests/spec_checks.py` checks every report against the spec's rules: one result per planned test,
non-empty IDs and messages, a source on every error, and valid coverage counts.

### Known gaps are marked, not hidden

- Failing tests for work that isn't built yet are marked `xfail`.
- The suite stays green so it can block regressions, and the gaps still appear in every report.
- The marker is non-strict: when a teammate fixes a gap, the test shows as XPASS. That tells the
  team to remove the marker.

## Known gaps and limitations

### Defects found by the tests

These tests are marked `xfail` until the defects are fixed:

| Test | Gap | Where |
|---|---|---|
| API-06, AC-02 | Bundle generation is still a TODO, so a bundle never leaves `PROCESSING` or becomes `COMPLETED`/`PARTIAL_SUCCESS`, and there is no `download_url`. | `bundle_service.py` (Person 3) |
| API-03 | `POST /bundles` accepts an empty or blank `product_id`/`release_version` (spec 5.2 says non-empty). A non-string `product_id` crashes with a 500 error, which the tester reports as `error` with source `system`. | `app.py` |
| `test_crlf_line_endings_are_preserved` | `read_text()` turns CRLF line endings into LF, so content is not preserved exactly (REQ-08). | `artifact_pipeline.py` |
| `test_non_utf8_artifact_is_invalid_rather_than_a_crash` | A file that is not valid UTF-8 raises `UnicodeDecodeError` and the request fails with a 500 error. It should be reported as `INVALID`. | `artifact_pipeline.py` |

When a defect is fixed, its test shows as XPASS. Remove its entry from `KNOWN_GAPS` in
`tests/test_acceptance.py`, or the `xfail` marker in `tests/test_pipeline.py`.

### Not covered yet

- **Code coverage** is reported as `null`. Measuring coverage for each session separately needs
  instrumentation we haven't built. The spec allows `null` as long as `basis` explains why.
- **AC-05 (external source failure)** is skipped. Phase 3 uses one controlled source, so a source
  failure can't be triggered through the API. This is planned for Phase 4 (Phase 2 §6.1).
- **AC-08** automates only the category-coverage check. The Annex VII review of the manifest still
  has to be done by a person.
- **Only Markdown** artifacts from local fixtures are tested. HTML, SBOM formats and live sources
  (GitHub, Jenkins) come in Phase 4.

### Decisions the pod still has to confirm

- **Component list:** `oracle.json` defines 7 components for component coverage. The course says
  the pod specification defines this list, so it is our proposal until Team 24 agrees.
- **Session ID in project calls:** we add `session_id` to every `POST /bundles` body, as in the
  course's example. Systems that ignore unknown fields are unaffected. The pod spec should say so
  explicitly.
- **Setup starting data:** we send `"setup": {}` because both teams serve the same static
  fixtures.

### Limitations of the runner

- **Shared test data:** another team's system only gives meaningful results if it serves the same
  fixtures (`prd-88` v1.2 to v1.4) from its controlled source.
- **Reports are kept in memory.** Both services lose sessions and reports when they restart. The
  spec allows this, but a redeploy between setup and run means starting a new session.
- **No access control on `/test/*`.**
  - Anyone who can reach the tester can point it at any URL.
  - Anyone who can reach the system can start a self-test.
  - Before wider exposure, protect these endpoints with the shared token, or disable
    `/test/selftest`.

### Repository housekeeping

- `.pyc` files from `api-setup` are still committed (`__pycache__/`, `tests/__pycache__/`), even
  though `.gitignore` excludes them. Remove them with `git rm -r --cached __pycache__ tests/__pycache__`.
