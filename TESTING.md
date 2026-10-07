# Automated testing

There are two parts:

- **Tester** (`test_runner/`): runs the pod's acceptance suite against any team's base URL.
  You can use it as a web service or from the command line.
- **Validated system** (`app.py`): our service. It exposes the same `/test/*` endpoints so a
  grader can reset it and run our internal test suite.

Install once: `pip install -r requirements.txt`

## Running the tester

From the command line:

```bash
python app.py                                         # system under test on :5000
python -m test_runner --url http://localhost:5000     # prints PASS/FAIL per criterion
python -m test_runner --url <url> -v                  # show every check
python -m test_runner --url <url> --json              # raw JSON report
python -m test_runner --url <url> --only AC-02 AC-03  # subset
```

Exit codes: `0` means nothing failed, `1` means a criterion failed, `2` means the target is
unreachable.

As a web service:

```bash
python -m test_runner.service --port 5001
```

| Endpoint | Body | Effect |
|---|---|---|
| `POST /test/setup` | `{"url": "<base url>"}` | Calls the target's `POST /test/setup` and checks it is reachable. Returns 400 for a bad URL and 502 if the target is unreachable. |
| `POST /test/run` | `{"url": "<base url>", "poll_timeout": 5, "only": ["AC-02"]}` | Runs the suite and returns the report. `poll_timeout` and `only` are optional. |
| `GET /test/report` | `?url=<base url>` (optional) | Returns the latest report for that target, or the latest report overall. Returns 404 if there is none. |

Both the setup and run endpoints also accept an optional `"token"`, which is sent as `Authorization: Bearer <token>`.

## Testing endpoints on our system

| Endpoint | Effect |
|---|---|
| `POST /test/setup` | Clears stored bundles and lists the fixture releases available. |
| `POST /test/run` | Runs `pytest tests/` in a subprocess and returns a per-test report. |
| `GET /test/report` | Returns the last `/test/run` report. Returns 404 before the first run. |

Locally, `pytest` runs the same suite.

## What the acceptance suite checks

The suite uses only `POST /bundles` and `GET /bundles/{id}`. Expected results come from
`test_runner/oracle.json`, which was written by hand for the fixtures in `fixtures/`.

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

## Design decisions

The TAs have not published the OpenAPI spec for the testing endpoints yet. Phase 3 only gives
this outline:

```
TESTER              /test {payload indicates a URL}   setup, run, report
VALIDATED SYSTEM    /test                             setup, run, report
```

The choices below fill in what the outline leaves open. They aim to make the TA's runner work
with us without changes, and to make the runner easy to update once the real spec arrives.

### Two roles, one endpoint shape

The outline lists the same three endpoints for both roles, so I built them as two separate
services:

- The **tester** (`test_runner/service.py`) is the service listed in the registry as our
  automated test runner. It has its own `/test/*` endpoints, and each call names a target system.
- Our **validated system** (`app.py`) exposes the same `/test/*` endpoints, so the TA's runner or
  another team's tester can drive us.

Because the two roles use the same paths, they are separate apps. Our service can be tested by any
tester, and our tester can test any service.

### What each endpoint does

| Endpoint | Tester | Validated system |
|---|---|---|
| `setup` | Points at the target URL, calls the target's own `/test/setup`, and reports whether the target is reachable. | Clears stored bundles so every run starts from the same state, and lists the fixture releases it can serve. |
| `run` | Runs the pod acceptance suite against the target over HTTP. | Runs our full internal `pytest` suite. |
| `report` | Returns the latest report, for a given URL or overall. | Returns the result of the last `run`. |

- `setup` and `run` use POST because they change state. `report` uses GET because it only reads.
- `run` returns the full report immediately, as well as storing it. That way a grader can use
  `run` and skip `report`, or call `report` later.

### The payload is `{"url": ...}`

The outline only says "payload indicates a URL", so the field is named `url`. The tester also
accepts it as a query string on `report`.

The tester checks that the URL is an absolute `http(s)` address. It returns 400 for a bad URL and
502 if the target can't be reached, so a grader can tell "bad request" apart from "target down".

`token` is optional. Phase 2 §5.3 says cross-team tests send a shared token, so if a token is
supplied the tester forwards it as `Authorization: Bearer <token>`.

### Built to be adapted when the real spec arrives

- Each piece lives in one place:
  - Request parsing for the tester: `_target_url()` and the route functions in
    `test_runner/service.py`.
  - Report building: `run_suite()` in `test_runner/suite.py`.
  - Our system's endpoints: `selftest.py`.
- If the TA spec renames a field or changes the report format, only these files change. The test
  logic itself does not.
- Both roles return reports in the same basic shape: `run_id`, `passed`, a `summary` with counts,
  and a list of `results` with an `id` and `outcome`. A grader can therefore read either kind of
  report the same way.

### Works with targets that don't fully match

- If a target has no `/test/setup`, it answers 404 or 405. The tester records `"not_supported"`
  and still runs the suite, because the other team may not have built the testing endpoints yet.
- Results never depend on the target's `/test/*` endpoints. The suite only uses `POST /bundles`
  and `GET /bundles/{id}`, the interface both teams agreed on in Phase 2 §5.
- Where the spec allows more than one valid answer, the suite accepts each of them:
  - Content can come from `artifacts[].content` or from `download_url`.
  - An unknown release can be rejected with an error or come back as a `FAILED` bundle.
- If something can't be seen through a team's API, the check is marked "n/a" rather than failed.
  This way teams aren't penalised for valid design choices.
- The spec makes bundle creation asynchronous (202 + `PENDING`). The tester therefore polls
  `GET /bundles/{id}` until the status is final or `poll_timeout` runs out.

### Our `/test/run` runs pytest in a subprocess

If tests ran inside the live server, they would clear and overwrite its real stored bundles.
Running them in a subprocess keeps the server's state untouched.

The subprocess sets `CRA_SELFTEST_RUNNING`. While that variable is set, `/test/run` refuses with
409, so a test can never start another test run in a loop.

### Expected results are fixed in advance

Phase 2 §4.2 requires the expected outputs to be decided before any implementation runs.
`oracle.json` was written by hand. If the expected results came from our own code, they would
simply repeat our bugs.

### The tester is tested too

A test runner that always says "pass" is worse than none. `tests/fake_systems.py` contains a
small system that follows the spec exactly, and the suite must fully pass it. It also has broken
versions: one serves the wrong release, one changes file content, one accepts empty files, one
hides missing categories, one always reports `COMPLETED`, and one skips input validation. For
each of these, the matching criterion has to fail. That shows each test can actually catch the
mistake it is meant to catch.

### Known gaps are marked, not hidden

- Failing tests for work that isn't built yet are marked `xfail`.
- The suite stays green, so it can block regressions, but the gaps still appear in every report.
- The marker is non-strict: when a teammate fixes a gap, the build doesn't break. The test shows
  as XPASS instead, which tells the team to remove the marker.

### No new dependencies

The tester uses Python's built-in `urllib`, so it runs with the existing `requirements.txt`.

## Known gaps and limitations

### Defects found by the tests

These tests are marked `xfail` until the defects are fixed:

| Test | Gap | Where |
|---|---|---|
| API-06, AC-02 | Bundle generation is still a TODO, so a bundle never leaves `PROCESSING` or becomes `COMPLETED`/`PARTIAL_SUCCESS`, and there is no `download_url`. | `bundle_service.py` (Person 3) |
| API-03 | `POST /bundles` accepts an empty or blank `product_id`/`release_version` (spec 5.2 says non-empty). A non-string `product_id` crashes with a 500 error. | `app.py` |
| `test_crlf_line_endings_are_preserved` | `read_text()` turns CRLF line endings into LF, so content is not preserved exactly (REQ-08). | `artifact_pipeline.py` |
| `test_non_utf8_artifact_is_invalid_rather_than_a_crash` | A file that is not valid UTF-8 raises `UnicodeDecodeError` and the request fails with a 500 error. It should be reported as `INVALID`. | `artifact_pipeline.py` |

When a defect is fixed, its test shows as XPASS. Remove its entry from `KNOWN_GAPS` in
`tests/test_acceptance.py`, or the `xfail` marker in `tests/test_pipeline.py`.

### Not covered yet

- **AC-05 (external source failure)** is skipped. Phase 3 uses one controlled source, so a source
  failure can't be triggered through the API. This is planned for Phase 4 (Phase 2 §6.1).
- **AC-08** automates only the category-coverage check. The Annex VII review of the manifest still
  has to be done by a person.
- **Only Markdown** artifacts from local fixtures are tested. HTML, SBOM formats and live sources
  (GitHub, Jenkins) come in Phase 4.

### Limitations of the runner

- **The TA spec is not final.** Endpoint paths, the `url` field and the report format are our best
  reading of the Phase 3 outline. They may need renaming when the OpenAPI spec is published.
- **Shared test data:** another team's system only gives meaningful results if it serves the same
  fixtures (`prd-88` v1.2 to v1.4) from its controlled source.
- **Reports are kept in memory.** Both services lose their stored reports when they restart.
- **Runs are synchronous.** `/test/run` blocks until the suite finishes. Against a target whose
  bundles never finish, each bundle waits the full `poll_timeout`; a full run against our current
  system took about 4 seconds with a 1 s timeout.
- **No access control on `/test/*`.** Anyone who can reach our service can clear its bundles with
  `/test/setup`. Anyone who can reach the tester can point it at any URL. Before a public
  deployment, these endpoints should require the shared token or be limited to the pod.
- **Development server only.** Both services run on Flask's built-in server. A production server
  (such as gunicorn) and the deployment itself are not set up yet.

### Repository housekeeping

- `.pyc` files from `api-setup` are still committed (`__pycache__/`, `tests/__pycache__/`), even
  though `.gitignore` now excludes them. Remove them with `git rm -r --cached __pycache__ tests/__pycache__`.
- `app.py` was changed so `POST /bundles` returns its 202 response from a copy taken *before*
  processing, which keeps the spec's `PENDING` acknowledgement. Before this change,
  `person2-retrieval` broke `api-setup`'s `test_create_bundle`.
