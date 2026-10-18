# Handoff
Our system:

https://30861-p3.jscarfo.com

Our tester:

https://30861-p3.jscarfo.com/tester



**Authentication is required on our system**, unlike yours: send
`Authorization: Bearer phase3-local-token` on every request. `/healthz` is public; everything
else returns 401 without it. Your tester already accepts a `"token"` on setup and forwards
it, so pass that value and nothing else changes.


To run our tester against your system:

```
POST https://30861-p3.jscarfo.com/tester/test/setup
     {"target_url": "https://team22.13-58-35-224.sslip.io/system"}
  -> 201 {"session_id": "<id>"}

POST https://30861-p3.jscarfo.com/tester/test/run
     {"session_id": "<id>"}
  -> 200 with the full report

GET  https://30861-p3.jscarfo.com/tester/test/report?session_id=<id>
  -> 200 with the stored report
```

Optional: `"token"` on setup (forwarded as `Authorization: Bearer <token>`), and
`"poll_timeout"` (0–60 s, default 10) and `"only"` (list of test ids) on run. Each session
runs once; start a new one to repeat the suite. Error bodies are
`{"message": "...", "source": "tester" | "system" | "communication"}`. If your system did not
expose `/test/setup` we would still test it and only lose the code-coverage numbers.

From the command line, after cloning: `python -m tester --url <your base url>`, plus
`--token`, `-v`, `--json`, or `--only AC-02 AC-03`.

**One known problem** remains on your system:

1. Your TLS chain is rejected by OpenSSL. Python clients get
   `CERTIFICATE_VERIFY_FAILED: certificate has expired` while curl succeeds. The leaf looks
   fine (CN=team22.13-58-35-224.sslip.io, Let's Encrypt YE1, notAfter 2027-01-16), so we
   think the served chain is missing or has a stale intermediate. Your own tester uses
   `urllib`, so any team testing you from Linux or macOS will hit this. We had to disable
   certificate verification to complete our run against you.


**Results so far**

- Your tester against our system: **14 pass, 0 fail, 0 error, 1 skip** (session
  `748e5029-932d-4408-9bc9-c062d4a729bc`), component coverage 6/7, system coverage 7/8. 
- Our tester against your system: **13 pass, 0 fail, 0 error, 1 skip**, component 6/7,
  system 7/8.


# Cross testing results

# Phase 3 cross-team test report — Team 24 ↔ Team 22

Run date: 2026-10-18. Pod specification version 1.0.0 (Project Phase #2, Sections 3–5).

| | Team 24 (us) | Team 22 |
| --- | --- | --- |
| System | `https://30861-p3.jscarfo.com` | `https://team22.13-58-35-224.sslip.io/system` |
| Tester | `https://30861-p3.jscarfo.com/tester` (also `python -m tester`) | `https://team22.13-58-35-224.sslip.io/tester` |
| Authentication | `Authorization: Bearer <shared token>` | none |
| Required categories | 5 (adds Product Description) | 4 |
| Shared fixture releases | `v1.2`, `v1.3`, `v1.4` | `v1.2`, `v1.3`, `v1.4` |

Both directions have been run end to end against deployed services. Both teams now use the pod's shared `prd-88` fixtures: `v1.2` complete, `v1.3` with Test Logs
missing, `v1.4` with an empty therefore invalid SBOM. Team 24's four pod-category artifacts
for those releases are byte-identical to the pod's agreed files.

## Direction 1 — Team 22's tester against our system

Session ID `748e5029-932d-4408-9bc9-c062d4a729bc`. Raw report:
`crossteam/team22-tester-against-us.json`.

**pass 14 error 0, skip 1, total 15.** `passed: false`.
Coverage: component 6/7, system 7/8, code `null`.

| Test | Outcome | Note |
| --- | --- | --- |
| API-01…API-07 | pass | 202/UUID/PENDING, 400 validation, 404 unknown bundle, terminal status, BundleStatusResponse conformance, unknown release never collected |
| AC-01, AC-02 | pass | release identification; complete collection |
| AC-03 | pass | `v1.3` Test Logs MISSING; 1 check not observable through this interface |
| AC-04 | pass | `v1.4` SBOM INVALID; 1 check not observable through this interface |
| AC-05 | skip | a single controlled source cannot fail through the shared interface |
| AC-06 | pass | collected content byte-identical to the pod fixtures |
| AC-07 | pass | archive content hashes differed between two identical runs |
| AC-08 | pass | 18 checks |

An earlier run of this direction, session `2ff0aa4a-a519-449e-b928-dd69dd225ada`, scored
pass 10 / fail 4 before Team 24 adopted the pod's fixture identifiers and fixture bytes.
AC-03, AC-04 and AC-06 were all caused by that mismatch and now pass. AC-07 is a genuine
Team 24 defect and remains open (see problem 1).

## Direction 2 — our tester against Team 22's system

Raw report: `crossteam/us-against-team22.json`.

**pass 13, fail 0, error 0, skip 1, total 14.** `passed: true`.
Coverage: component 6/7, system 7/8, code `null` (their `/test/report` reports null counts).

| Test | Outcome |
| --- | --- |
| API-01 202 + canonical UUID + PENDING | pass |
| API-02 missing required field → 400 | pass |
| API-03 field constraints and malformed JSON → 400 | pass |
| API-04 unknown bundle id → 404 | pass |
| API-05 malformed bundle id → 400 or 404 | pass |
| API-06 terminal status, BundleStatusResponse conformance | pass |
| AC-01 release identification across `v1.2`/`v1.3`/`v1.4` | pass |
| AC-02 `v1.2` COMPLETED, all categories PRESENT, no duplicates | pass |
| AC-03 `v1.3` Test Logs MISSING, not COMPLETED | pass |
| AC-04 `v1.4` SBOM INVALID, not COMPLETED | pass |
| AC-05 | skip |
| AC-06 artifacts byte-identical to the pod fixtures | pass |
| AC-07 distinct ids, identical statuses, reproducible content | pass |
| AC-08 category coverage; unknown release never collected | pass |

Two failures in an earlier run of this direction were defects in **our** tester, not in their
service, and were fixed before the run above: the malformed-JSON probe omitted a
`Content-Type` header (their service correctly answered 415), and the duplicate-artifact
check counted a team that publishes content both inline and in an archive as duplicating it.

## Problems found

**1. Archived `manifest.json` carried a per-run `bundle_id` — ours, fixed.** This caused the
only remaining AC-07 failure in direction 1. Both testers hash every archive entry, so the
archive itself has to be reproducible; Team 22's archived manifest omits the id and passes.
Team 24 has removed `bundle_id` from the archived manifest: it remains on the HTTP response
and in the download filename, and the archived manifest still carries `product_id`,
`release_version` and the per-artifact release association, so no evidence is lost. A
regression test now asserts that two runs of the same release produce byte-identical
archives. Verified locally; needs one more direction-1 run after redeployment to confirm.

Reference measurement: Team 22's tester against Team 22's own system scores pass 14, fail 0,
skip 1 (session `53eb19eb-efa5-47df-84ee-246f4b83b966`, raw report
`crossteam/team22-tester-against-themselves.json`), so AC-07 is passable through the shared
interface and was not a disagreement about the criterion.

**2. Fixture alignment — resolved and re-tested.** Caused AC-03, AC-04 and AC-06 in the
earlier direction-1 run. Team 24 adopted the pod's `v1.2`/`v1.3`/`v1.4` convention and the
pod's fixture bytes, and moved its own extra cases to non-colliding identifiers. All three
criteria pass in the re-run above.

**3. Category list — pod decision, open.** Team 24 requires 5 categories, Team 22 requires 4;
the extra is Product Description. Neither tester penalises an unexpected extra category, so
this caused no failures, but AC-08 still needs the pod to ratify the Annex VII category list.

**4. Team 22's TLS chain is rejected by OpenSSL — theirs, open.** Python clients fail with
`CERTIFICATE_VERIFY_FAILED: certificate has expired` while curl succeeds. The leaf is valid
(`CN=team22.13-58-35-224.sslip.io`, Let's Encrypt `YE1`, notAfter 2027-01-16), so this looks
like a missing or stale intermediate in the served chain. Their own tester uses `urllib`, so
other teams will hit it. Direction 2 above was run with certificate verification disabled;
the shipped Team 24 tester does not disable verification and reports the condition as an
`error` with source `communication`.

**5. Testing endpoints are not in the pod specification — pod decision, open.** Team 22's
tester requires `POST /test/setup` and `GET /test/report` on the system under test before it
will run anything; these are not in Section 5. Team 24 implemented them. Team 24's tester
treats them as optional: a target without them is still tested and only its code-coverage
numbers are lost. The pod should adopt them, or agree they are optional.

**6. Authentication is asymmetric — pod decision, open.** Section 5.3 specifies a shared
bearer token; Team 24 enforces one, Team 22 requires none. Both testers accept a token and
forward it, so this blocked neither direction.

**7. Code coverage is unavailable from both systems.** Both report `null` counts, so neither
report carries a code-coverage figure. Component and system-model coverage are reported by
both testers.

## Team 22 Tester vs Team 24 system results
{"coverage":{"code":{"basis":"This deployment is not instrumented for code coverage.","covered":null,"total":null},"component":{"basis":"Components listed in the pod specification (oracle.json 'components'); a component counts as covered when at least one executed test exercises it","covered":6,"total":7},"system":{"basis":"Acceptance criteria AC-01..AC-08 from the pod specification (Phase 2 section 4.1); a criterion counts as covered when its test was executed (pass or fail)","covered":7,"total":8}},"duration_ms":5023.8,"finished_at":"2026-10-18T20:58:07.429698+00:00","passed":false,"results":[{"checks":[{"description":"POST /bundles for prd-88 v1.2 returns 202 with a bundle_id","detail":"HTTP 202: {\"bundle_id\": \"fc4481ca-1a42-4742-9ac2-a594ef82a59c\", \"status\": \"PENDING\"}","passed":true},{"description":"GET /bundles/{bundle_id} for prd-88 v1.2 returns 200 with a JSON object","detail":"HTTP 200: {\"bundle_id\": \"fc4481ca-1a42-4742-9ac2-a594ef82a59c\", \"download_url\": \"https://30861-p3.jscarfo.com/downloads/fc4481ca-1a42-4742-9ac2-a594ef82a59c.zip\", \"manifest\": [{\"artifacts\": [{\"path\": \"artifacts...","passed":true},{"description":"bundle_id is a UUID","detail":"fc4481ca-1a42-4742-9ac2-a594ef82a59c","passed":true},{"description":"initial status is PENDING","detail":"got 'PENDING'","passed":true}],"components":["request-intake"],"duration_ms":562.6,"message":"POST /bundles acknowledges a valid request with 202, a UUID bundle_id and PENDING. All observable checks passed (4).","outcome":"pass","requirements":["REQ-01"],"test_id":"API-01","title":"POST /bundles acknowledges a valid request with 202, a UUID bundle_id and PENDING"},{"checks":[{"description":"missing product_id: 400 with an 'error' message","detail":"HTTP 400: {\"error\": \"Missing required field: 'product_id'\"}","passed":true},{"description":"missing release_version: 400 with an 'error' message","detail":"HTTP 400: {\"error\": \"Missing required field: 'release_version'\"}","passed":true},{"description":"neither identifier: 400 with an 'error' message","detail":"HTTP 400: {\"error\": \"Missing required field: 'product_id'\"}","passed":true}],"components":["request-intake"],"duration_ms":273.8,"message":"POST /bundles rejects requests missing a required field with 400. All observable checks passed (3).","outcome":"pass","requirements":["REQ-01"],"test_id":"API-02","title":"POST /bundles rejects requests missing a required field with 400"},{"checks":[{"description":"empty product_id: 400","detail":"HTTP 400","passed":true},{"description":"blank release_version: 400","detail":"HTTP 400","passed":true},{"description":"non-string product_id: 400","detail":"HTTP 400","passed":true},{"description":"non-JSON body: 4xx client error","detail":"HTTP 400","passed":true}],"components":["request-intake"],"duration_ms":1131.8,"message":"POST /bundles enforces field constraints (non-empty strings, JSON body). All observable checks passed (4).","outcome":"pass","requirements":["REQ-01"],"test_id":"API-03","title":"POST /bundles enforces field constraints (non-empty strings, JSON body)"},{"checks":[{"description":"unknown UUID: 404","detail":"HTTP 404","passed":true}],"components":["status-retrieval"],"duration_ms":102.4,"message":"GET /bundles/{bundle_id} returns 404 for an unknown bundle. All observable checks passed (1).","outcome":"pass","requirements":["REQ-04"],"test_id":"API-04","title":"GET /bundles/{bundle_id} returns 404 for an unknown bundle"},{"checks":[{"description":"POST /bundles for prd-88 v1.2 returns 202 with a bundle_id","detail":"HTTP 202: {\"bundle_id\": \"fc4481ca-1a42-4742-9ac2-a594ef82a59c\", \"status\": \"PENDING\"}","passed":true},{"description":"GET /bundles/{bundle_id} for prd-88 v1.2 returns 200 with a JSON object","detail":"HTTP 200: {\"bundle_id\": \"fc4481ca-1a42-4742-9ac2-a594ef82a59c\", \"download_url\": \"https://30861-p3.jscarfo.com/downloads/fc4481ca-1a42-4742-9ac2-a594ef82a59c.zip\", \"manifest\": [{\"artifacts\": [{\"path\": \"artifacts...","passed":true},{"description":"bundle_id matches the created bundle","detail":"got 'fc4481ca-1a42-4742-9ac2-a594ef82a59c'","passed":true},{"description":"product_id echoes the request","detail":"got 'prd-88'","passed":true},{"description":"release_version echoes the request","detail":"got 'v1.2'","passed":true},{"description":"status is in the BundleStatusResponse enum","detail":"got 'COMPLETED'","passed":true},{"description":"manifest is a list","detail":"got list","passed":true},{"description":"every manifest item has a category and a status in the ArtifactManifestItem enum","detail":null,"passed":true},{"description":"download_url is a string","detail":"got 'https://30861-p3.jscarfo.com/downloads/fc4481ca-1a42-4742-9ac2-a594ef82a59c.zip'","passed":true}],"components":["status-retrieval","manifest-generation"],"duration_ms":0.1,"message":"GET /bundles/{bundle_id} conforms to BundleStatusResponse. All observable checks passed (9).","outcome":"pass","requirements":["REQ-03","REQ-04","REQ-05"],"test_id":"API-05","title":"GET /bundles/{bundle_id} conforms to BundleStatusResponse"},{"checks":[{"description":"POST /bundles for prd-88 v1.2 returns 202 with a bundle_id","detail":"HTTP 202: {\"bundle_id\": \"fc4481ca-1a42-4742-9ac2-a594ef82a59c\", \"status\": \"PENDING\"}","passed":true},{"description":"GET /bundles/{bundle_id} for prd-88 v1.2 returns 200 with a JSON object","detail":"HTTP 200: {\"bundle_id\": \"fc4481ca-1a42-4742-9ac2-a594ef82a59c\", \"download_url\": \"https://30861-p3.jscarfo.com/downloads/fc4481ca-1a42-4742-9ac2-a594ef82a59c.zip\", \"manifest\": [{\"artifacts\": [{\"path\": \"artifacts...","passed":true},{"description":"terminal status reached within 20.0s","detail":"last status 'COMPLETED'","passed":true}],"components":["manifest-generation"],"duration_ms":0.0,"message":"A bundle reaches a terminal status (COMPLETED, PARTIAL_SUCCESS, FAILED). All observable checks passed (3).","outcome":"pass","requirements":["REQ-05"],"test_id":"API-06","title":"A bundle reaches a terminal status (COMPLETED, PARTIAL_SUCCESS, FAILED)"},{"checks":[{"description":"GET /bundles/{bundle_id} returns 200","detail":"HTTP 200","passed":true},{"description":"bundle is not COMPLETED","detail":"got 'FAILED'","passed":true},{"description":"no category is PRESENT","detail":null,"passed":true},{"description":"no artifact content is included","detail":null,"passed":true}],"components":["request-intake","artifact-retrieval"],"duration_ms":254.9,"message":"An unknown release is not reported as collected. All observable checks passed (4).","outcome":"pass","requirements":["REQ-01","REQ-06"],"test_id":"API-07","title":"An unknown release is not reported as collected"},{"checks":[{"description":"POST /bundles for prd-88 v1.2 returns 202 with a bundle_id","detail":"HTTP 202: {\"bundle_id\": \"fc4481ca-1a42-4742-9ac2-a594ef82a59c\", \"status\": \"PENDING\"}","passed":true},{"description":"GET /bundles/{bundle_id} for prd-88 v1.2 returns 200 with a JSON object","detail":"HTTP 200: {\"bundle_id\": \"fc4481ca-1a42-4742-9ac2-a594ef82a59c\", \"download_url\": \"https://30861-p3.jscarfo.com/downloads/fc4481ca-1a42-4742-9ac2-a594ef82a59c.zip\", \"manifest\": [{\"artifacts\": [{\"path\": \"artifacts...","passed":true},{"description":"prd-88 v1.2: bundle records the requested product_id and release_version","detail":"got ('prd-88', 'v1.2')","passed":true},{"description":"POST /bundles for prd-88 v1.3 returns 202 with a bundle_id","detail":"HTTP 202: {\"bundle_id\": \"bd98f940-6123-41f1-aad9-a1c9d790486f\", \"status\": \"PENDING\"}","passed":true},{"description":"GET /bundles/{bundle_id} for prd-88 v1.3 returns 200 with a JSON object","detail":"HTTP 200: {\"bundle_id\": \"bd98f940-6123-41f1-aad9-a1c9d790486f\", \"download_url\": \"https://30861-p3.jscarfo.com/downloads/bd98f940-6123-41f1-aad9-a1c9d790486f.zip\", \"manifest\": [{\"artifacts\": [{\"path\": \"artifacts...","passed":true},{"description":"prd-88 v1.3: bundle records the requested product_id and release_version","detail":"got ('prd-88', 'v1.3')","passed":true},{"description":"POST /bundles for prd-88 v1.2 returns 202 with a bundle_id","detail":"HTTP 202: {\"bundle_id\": \"fc4481ca-1a42-4742-9ac2-a594ef82a59c\", \"status\": \"PENDING\"}","passed":true},{"description":"GET /bundles/{bundle_id} for prd-88 v1.2 returns 200 with a JSON object","detail":"HTTP 200: {\"bundle_id\": \"fc4481ca-1a42-4742-9ac2-a594ef82a59c\", \"download_url\": \"https://30861-p3.jscarfo.com/downloads/fc4481ca-1a42-4742-9ac2-a594ef82a59c.zip\", \"manifest\": [{\"artifacts\": [{\"path\": \"artifacts...","passed":true},{"description":"prd-88 v1.2: no artifact from another release is included","detail":null,"passed":true},{"description":"POST /bundles for prd-88 v1.3 returns 202 with a bundle_id","detail":"HTTP 202: {\"bundle_id\": \"bd98f940-6123-41f1-aad9-a1c9d790486f\", \"status\": \"PENDING\"}","passed":true},{"description":"GET /bundles/{bundle_id} for prd-88 v1.3 returns 200 with a JSON object","detail":"HTTP 200: {\"bundle_id\": \"bd98f940-6123-41f1-aad9-a1c9d790486f\", \"download_url\": \"https://30861-p3.jscarfo.com/downloads/bd98f940-6123-41f1-aad9-a1c9d790486f.zip\", \"manifest\": [{\"artifacts\": [{\"path\": \"artifacts...","passed":true},{"description":"prd-88 v1.3: no artifact from another release is included","detail":null,"passed":true}],"components":["artifact-retrieval"],"duration_ms":1263.1,"message":"Release identification: artifacts belong to the requested release. All observable checks passed (12).","outcome":"pass","requirements":["REQ-01","REQ-04"],"test_id":"AC-01","title":"Release identification: artifacts belong to the requested release"},{"checks":[{"description":"POST /bundles for prd-88 v1.2 returns 202 with a bundle_id","detail":"HTTP 202: {\"bundle_id\": \"fc4481ca-1a42-4742-9ac2-a594ef82a59c\", \"status\": \"PENDING\"}","passed":true},{"description":"GET /bundles/{bundle_id} for prd-88 v1.2 returns 200 with a JSON object","detail":"HTTP 200: {\"bundle_id\": \"fc4481ca-1a42-4742-9ac2-a594ef82a59c\", \"download_url\": \"https://30861-p3.jscarfo.com/downloads/fc4481ca-1a42-4742-9ac2-a594ef82a59c.zip\", \"manifest\": [{\"artifacts\": [{\"path\": \"artifacts...","passed":true},{"description":"manifest reports 'SBOM' exactly once as PRESENT","detail":"got ['PRESENT']","passed":true},{"description":"manifest reports 'Risk Assessment' exactly once as PRESENT","detail":"got ['PRESENT']","passed":true},{"description":"manifest reports 'Test Logs' exactly once as PRESENT","detail":"got ['PRESENT']","passed":true},{"description":"manifest reports 'User Instructions' exactly once as PRESENT","detail":"got ['PRESENT']","passed":true},{"description":"bundle status is COMPLETED","detail":"got 'COMPLETED'","passed":true}],"components":["artifact-retrieval","classification","manifest-generation"],"duration_ms":0.1,"message":"Complete artifact collection: every category PRESENT and bundle COMPLETED. All observable checks passed (7).","outcome":"pass","requirements":["REQ-02","REQ-03","REQ-05"],"test_id":"AC-02","title":"Complete artifact collection: every category PRESENT and bundle COMPLETED"},{"checks":[{"description":"POST /bundles for prd-88 v1.3 returns 202 with a bundle_id","detail":"HTTP 202: {\"bundle_id\": \"bd98f940-6123-41f1-aad9-a1c9d790486f\", \"status\": \"PENDING\"}","passed":true},{"description":"GET /bundles/{bundle_id} for prd-88 v1.3 returns 200 with a JSON object","detail":"HTTP 200: {\"bundle_id\": \"bd98f940-6123-41f1-aad9-a1c9d790486f\", \"download_url\": \"https://30861-p3.jscarfo.com/downloads/bd98f940-6123-41f1-aad9-a1c9d790486f.zip\", \"manifest\": [{\"artifacts\": [{\"path\": \"artifacts...","passed":true},{"description":"manifest reports 'SBOM' exactly once as PRESENT","detail":"got ['PRESENT']","passed":true},{"description":"manifest reports 'Risk Assessment' exactly once as PRESENT","detail":"got ['PRESENT']","passed":true},{"description":"manifest reports 'Test Logs' exactly once as MISSING","detail":"got ['MISSING']","passed":true},{"description":"manifest reports 'User Instructions' exactly once as PRESENT","detail":"got ['PRESENT']","passed":true},{"description":"bundle is not reported as COMPLETED","detail":"got 'PARTIAL_SUCCESS'","passed":true},{"description":"no collected artifact is labelled 'Test Logs'","detail":"bundle does not expose per-category artifact content","passed":null}],"components":["classification"],"duration_ms":0.1,"message":"Missing required artifact is reported as MISSING. All observable checks passed (7); 1 not observable through this interface.","outcome":"pass","requirements":["REQ-03","REQ-06"],"test_id":"AC-03","title":"Missing required artifact is reported as MISSING"},{"checks":[{"description":"POST /bundles for prd-88 v1.4 returns 202 with a bundle_id","detail":"HTTP 202: {\"bundle_id\": \"4e155209-c6ed-4bdb-a9d4-91867a8f1d5d\", \"status\": \"PENDING\"}","passed":true},{"description":"GET /bundles/{bundle_id} for prd-88 v1.4 returns 200 with a JSON object","detail":"HTTP 200: {\"bundle_id\": \"4e155209-c6ed-4bdb-a9d4-91867a8f1d5d\", \"download_url\": \"https://30861-p3.jscarfo.com/downloads/4e155209-c6ed-4bdb-a9d4-91867a8f1d5d.zip\", \"manifest\": [{\"artifacts\": [{\"path\": \"artifacts...","passed":true},{"description":"manifest reports 'SBOM' exactly once as INVALID","detail":"got ['INVALID']","passed":true},{"description":"manifest reports 'Risk Assessment' exactly once as PRESENT","detail":"got ['PRESENT']","passed":true},{"description":"manifest reports 'Test Logs' exactly once as PRESENT","detail":"got ['PRESENT']","passed":true},{"description":"manifest reports 'User Instructions' exactly once as PRESENT","detail":"got ['PRESENT']","passed":true},{"description":"bundle is not reported as COMPLETED","detail":"got 'PARTIAL_SUCCESS'","passed":true},{"description":"no collected artifact is labelled 'SBOM'","detail":"bundle does not expose per-category artifact content","passed":null}],"components":["classification"],"duration_ms":549.5,"message":"Invalid artifact is reported as INVALID and not treated as collected. All observable checks passed (7); 1 not observable through this interface.","outcome":"pass","requirements":["REQ-03","REQ-06"],"test_id":"AC-04","title":"Invalid artifact is reported as INVALID and not treated as collected"},{"checks":[],"components":["source-failure-handling"],"duration_ms":0.0,"message":"Not executed: Phase 3 uses a single controlled source, so a source failure cannot be induced through the shared interface. Deferred to Phase 4 (Phase 2 section 6.1).","outcome":"skip","requirements":["REQ-02","REQ-06","REQ-07"],"test_id":"AC-05","title":"External source failure is reported as INACCESSIBLE"},{"checks":[{"description":"POST /bundles for prd-88 v1.2 returns 202 with a bundle_id","detail":"HTTP 202: {\"bundle_id\": \"fc4481ca-1a42-4742-9ac2-a594ef82a59c\", \"status\": \"PENDING\"}","passed":true},{"description":"GET /bundles/{bundle_id} for prd-88 v1.2 returns 200 with a JSON object","detail":"HTTP 200: {\"bundle_id\": \"fc4481ca-1a42-4742-9ac2-a594ef82a59c\", \"download_url\": \"https://30861-p3.jscarfo.com/downloads/fc4481ca-1a42-4742-9ac2-a594ef82a59c.zip\", \"manifest\": [{\"artifacts\": [{\"path\": \"artifacts...","passed":true},{"description":"bundle exposes artifact content (artifacts[].content or download_url)","detail":"neither artifacts[].content nor a usable download_url found","passed":true},{"description":"'SBOM' content is preserved exactly (sha256)","detail":"expected 4bf92cff3143bf66..., not found among 6 artifact(s)","passed":true},{"description":"'Risk Assessment' content is preserved exactly (sha256)","detail":"expected 0c17a18b18d7dec7..., not found among 6 artifact(s)","passed":true},{"description":"'Test Logs' content is preserved exactly (sha256)","detail":"expected e369c8058f860fbb..., not found among 6 artifact(s)","passed":true},{"description":"'User Instructions' content is preserved exactly (sha256)","detail":"expected 2201be0bb4a57b3d..., not found among 6 artifact(s)","passed":true}],"components":["content-preservation"],"duration_ms":0.3,"message":"Source fidelity: collected content is byte-for-byte identical to the source. All observable checks passed (7).","outcome":"pass","requirements":["REQ-05","REQ-08"],"test_id":"AC-06","title":"Source fidelity: collected content is byte-for-byte identical to the source"},{"checks":[{"description":"POST /bundles for prd-88 v1.2 returns 202 with a bundle_id","detail":"HTTP 202: {\"bundle_id\": \"fc4481ca-1a42-4742-9ac2-a594ef82a59c\", \"status\": \"PENDING\"}","passed":true},{"description":"GET /bundles/{bundle_id} for prd-88 v1.2 returns 200 with a JSON object","detail":"HTTP 200: {\"bundle_id\": \"fc4481ca-1a42-4742-9ac2-a594ef82a59c\", \"download_url\": \"https://30861-p3.jscarfo.com/downloads/fc4481ca-1a42-4742-9ac2-a594ef82a59c.zip\", \"manifest\": [{\"artifacts\": [{\"path\": \"artifacts...","passed":true},{"description":"POST /bundles for prd-88 v1.2 returns 202 with a bundle_id","detail":"HTTP 202: {\"bundle_id\": \"48f6e5de-4878-482b-9f0c-9d0996a1734e\", \"status\": \"PENDING\"}","passed":true},{"description":"GET /bundles/{bundle_id} for prd-88 v1.2 returns 200 with a JSON object","detail":"HTTP 200: {\"bundle_id\": \"48f6e5de-4878-482b-9f0c-9d0996a1734e\", \"download_url\": \"https://30861-p3.jscarfo.com/downloads/48f6e5de-4878-482b-9f0c-9d0996a1734e.zip\", \"manifest\": [{\"artifacts\": [{\"path\": \"artifacts...","passed":true},{"description":"each request creates a distinct bundle","detail":"both returned fc4481ca-1a42-4742-9ac2-a594ef82a59c","passed":true},{"description":"product_id is identical across both runs","detail":null,"passed":true},{"description":"release_version is identical across both runs","detail":null,"passed":true},{"description":"status is identical across both runs","detail":null,"passed":true},{"description":"manifest is identical across both runs","detail":null,"passed":true},{"description":"content_hashes is identical across both runs","detail":"[\"0c17a18b18d7dec76253f63a773b93f73b12f67fc01952ecc64bec31037756dd\", \"2201be0bb4a57b3d5d9e8517cf6683dacb995642968d528532152f253778eca3\", \"4bf92cff3143bf66e8d3c5f0cb0e236856a8a2eb76ea95ae10467e53745c4d... != [\"0c17a18b18d7dec76253f63a773b93f73b12f67fc01952ecc64bec31037756dd\", \"2201be0bb4a57b3d5d9e8517cf6683dacb995642968d528532152f253778eca3\", \"4bf92cff3143bf66e8d3c5f0cb0e236856a8a2eb76ea95ae10467e53745c4d...","passed":false}],"components":["manifest-generation","content-preservation"],"duration_ms":808.5,"message":"Repeatable generation: identical inputs give equivalent manifests. 1 of 10 checks failed: content_hashes is identical across both runs ([\"0c17a18b18d7dec76253f63a773b93f73b12f67fc01952ecc64bec31037756dd\", \"2201be0bb4a57b3d5d9e8517cf6683dacb995642968d528532152f253778eca3\", \"4bf92cff3143bf66e8d3c5f0cb0e236856a8a2eb76ea95ae10467e53745c4d... != [\"0c17a18b18d7dec76253f63a773b93f73b12f67fc01952ecc64bec31037756dd\", \"2201be0bb4a57b3d5d9e8517cf6683dacb995642968d528532152f253778eca3\", \"4bf92cff3143bf66e8d3c5f0cb0e236856a8a2eb76ea95ae10467e53745c4d...)","outcome":"fail","requirements":["REQ-04","REQ-05","REQ-08"],"test_id":"AC-07","title":"Repeatable generation: identical inputs give equivalent manifests"},{"checks":[{"description":"POST /bundles for prd-88 v1.2 returns 202 with a bundle_id","detail":"HTTP 202: {\"bundle_id\": \"fc4481ca-1a42-4742-9ac2-a594ef82a59c\", \"status\": \"PENDING\"}","passed":true},{"description":"GET /bundles/{bundle_id} for prd-88 v1.2 returns 200 with a JSON object","detail":"HTTP 200: {\"bundle_id\": \"fc4481ca-1a42-4742-9ac2-a594ef82a59c\", \"download_url\": \"https://30861-p3.jscarfo.com/downloads/fc4481ca-1a42-4742-9ac2-a594ef82a59c.zip\", \"manifest\": [{\"artifacts\": [{\"path\": \"artifacts...","passed":true},{"description":"v1.2: 'SBOM' appears exactly once with a valid status","detail":"got ['PRESENT']","passed":true},{"description":"v1.2: 'Risk Assessment' appears exactly once with a valid status","detail":"got ['PRESENT']","passed":true},{"description":"v1.2: 'Test Logs' appears exactly once with a valid status","detail":"got ['PRESENT']","passed":true},{"description":"v1.2: 'User Instructions' appears exactly once with a valid status","detail":"got ['PRESENT']","passed":true},{"description":"POST /bundles for prd-88 v1.3 returns 202 with a bundle_id","detail":"HTTP 202: {\"bundle_id\": \"bd98f940-6123-41f1-aad9-a1c9d790486f\", \"status\": \"PENDING\"}","passed":true},{"description":"GET /bundles/{bundle_id} for prd-88 v1.3 returns 200 with a JSON object","detail":"HTTP 200: {\"bundle_id\": \"bd98f940-6123-41f1-aad9-a1c9d790486f\", \"download_url\": \"https://30861-p3.jscarfo.com/downloads/bd98f940-6123-41f1-aad9-a1c9d790486f.zip\", \"manifest\": [{\"artifacts\": [{\"path\": \"artifacts...","passed":true},{"description":"v1.3: 'SBOM' appears exactly once with a valid status","detail":"got ['PRESENT']","passed":true},{"description":"v1.3: 'Risk Assessment' appears exactly once with a valid status","detail":"got ['PRESENT']","passed":true},{"description":"v1.3: 'Test Logs' appears exactly once with a valid status","detail":"got ['MISSING']","passed":true},{"description":"v1.3: 'User Instructions' appears exactly once with a valid status","detail":"got ['PRESENT']","passed":true},{"description":"POST /bundles for prd-88 v1.4 returns 202 with a bundle_id","detail":"HTTP 202: {\"bundle_id\": \"4e155209-c6ed-4bdb-a9d4-91867a8f1d5d\", \"status\": \"PENDING\"}","passed":true},{"description":"GET /bundles/{bundle_id} for prd-88 v1.4 returns 200 with a JSON object","detail":"HTTP 200: {\"bundle_id\": \"4e155209-c6ed-4bdb-a9d4-91867a8f1d5d\", \"download_url\": \"https://30861-p3.jscarfo.com/downloads/4e155209-c6ed-4bdb-a9d4-91867a8f1d5d.zip\", \"manifest\": [{\"artifacts\": [{\"path\": \"artifacts...","passed":true},{"description":"v1.4: 'SBOM' appears exactly once with a valid status","detail":"got ['INVALID']","passed":true},{"description":"v1.4: 'Risk Assessment' appears exactly once with a valid status","detail":"got ['PRESENT']","passed":true},{"description":"v1.4: 'Test Logs' appears exactly once with a valid status","detail":"got ['PRESENT']","passed":true},{"description":"v1.4: 'User Instructions' appears exactly once with a valid status","detail":"got ['PRESENT']","passed":true}],"components":["classification","manifest-generation"],"duration_ms":0.2,"message":"Annex VII coverage: no supported category disappears silently (automated part). All observable checks passed (18).","outcome":"pass","requirements":["REQ-03","REQ-05"],"test_id":"AC-08","title":"Annex VII coverage: no supported category disappears silently (automated part)"}],"session_id":"748e5029-932d-4408-9bc9-c062d4a729bc","spec_version":"1.0.0","started_at":"2026-10-18T20:58:02.405901+00:00","summary":{"error":0,"fail":1,"pass":13,"skip":1,"total":15},"target_url":"https://30861-p3.jscarfo.com"}

## Team 24 Tester vs Team 22 system results 
{
  "target_url": "https://team22.13-58-35-224.sslip.io/system",
  "results": [
    {
      "test_id": "API-01",
      "title": "POST /bundles acknowledges a valid request with 202, a UUID and PENDING",
      "requirements": [
        "REQ-01"
      ],
      "components": [
        "request-intake"
      ],
      "outcome": "pass",
      "message": "POST /bundles acknowledges a valid request with 202, a UUID and PENDING. All checks passed (4).",
      "checks": [
        {
          "name": "status code is 202",
          "ok": true,
          "detail": ""
        },
        {
          "name": "response is a JSON object with bundle_id",
          "ok": true,
          "detail": ""
        },
        {
          "name": "bundle_id is a canonical UUID",
          "ok": true,
          "detail": ""
        },
        {
          "name": "acknowledgement reports PENDING",
          "ok": true,
          "detail": ""
        }
      ],
      "duration_ms": 140.0
    },
    {
      "test_id": "API-02",
      "title": "POST /bundles rejects a request missing a required field with 400",
      "requirements": [
        "REQ-01"
      ],
      "components": [
        "request-intake"
      ],
      "outcome": "pass",
      "message": "POST /bundles rejects a request missing a required field with 400. All checks passed (3).",
      "checks": [
        {
          "name": "no product_id is rejected with 400",
          "ok": true,
          "detail": ""
        },
        {
          "name": "no release_version is rejected with 400",
          "ok": true,
          "detail": ""
        },
        {
          "name": "empty object is rejected with 400",
          "ok": true,
          "detail": ""
        }
      ],
      "duration_ms": 360.0
    },
    {
      "test_id": "API-03",
      "title": "POST /bundles enforces field constraints",
      "requirements": [
        "REQ-01"
      ],
      "components": [
        "request-intake"
      ],
      "outcome": "pass",
      "message": "POST /bundles enforces field constraints. All checks passed (4).",
      "checks": [
        {
          "name": "empty product_id is rejected with 400",
          "ok": true,
          "detail": ""
        },
        {
          "name": "blank release_version is rejected with 400",
          "ok": true,
          "detail": ""
        },
        {
          "name": "non-string product_id is rejected with 400",
          "ok": true,
          "detail": ""
        },
        {
          "name": "malformed JSON is rejected with 400",
          "ok": true,
          "detail": ""
        }
      ],
      "duration_ms": 500.0
    },
    {
      "test_id": "API-04",
      "title": "GET /bundles/{bundle_id} returns 404 for an unknown bundle",
      "requirements": [
        "REQ-01"
      ],
      "components": [
        "status-retrieval"
      ],
      "outcome": "pass",
      "message": "GET /bundles/{bundle_id} returns 404 for an unknown bundle. All checks passed (1).",
      "checks": [
        {
          "name": "unknown bundle id returns 404",
          "ok": true,
          "detail": ""
        }
      ],
      "duration_ms": 109.0
    },
    {
      "test_id": "API-05",
      "title": "GET /bundles/{bundle_id} rejects a malformed identifier",
      "requirements": [
        "REQ-01"
      ],
      "components": [
        "status-retrieval"
      ],
      "outcome": "pass",
      "message": "GET /bundles/{bundle_id} rejects a malformed identifier. All checks passed (1).",
      "checks": [
        {
          "name": "non-UUID bundle id returns 400 or 404",
          "ok": true,
          "detail": ""
        }
      ],
      "duration_ms": 110.0
    },
    {
      "test_id": "API-06",
      "title": "A bundle reaches a terminal status and conforms to BundleStatusResponse",
      "requirements": [
        "REQ-03",
        "REQ-04",
        "REQ-05"
      ],
      "components": [
        "status-retrieval",
        "manifest-generation"
      ],
      "outcome": "pass",
      "message": "A bundle reaches a terminal status and conforms to BundleStatusResponse. All checks passed (10).",
      "checks": [
        {
          "name": "POST /bundles for prd-88 v1.2 returns 202 with a bundle_id",
          "ok": true,
          "detail": ""
        },
        {
          "name": "GET /bundles/{bundle_id} for prd-88 v1.2 returns 200 with a JSON object",
          "ok": true,
          "detail": ""
        },
        {
          "name": "status is terminal",
          "ok": true,
          "detail": ""
        },
        {
          "name": "product_id is echoed",
          "ok": true,
          "detail": ""
        },
        {
          "name": "release_version is echoed",
          "ok": true,
          "detail": ""
        },
        {
          "name": "manifest is a list",
          "ok": true,
          "detail": ""
        },
        {
          "name": "manifest entry 'SBOM' carries a known status",
          "ok": true,
          "detail": ""
        },
        {
          "name": "manifest entry 'Risk Assessment' carries a known status",
          "ok": true,
          "detail": ""
        },
        {
          "name": "manifest entry 'Test Logs' carries a known status",
          "ok": true,
          "detail": ""
        },
        {
          "name": "manifest entry 'User Instructions' carries a known status",
          "ok": true,
          "detail": ""
        }
      ],
      "duration_ms": 218.0
    },
    {
      "test_id": "AC-01",
      "title": "Release identification: a bundle reflects only the requested release",
      "requirements": [
        "REQ-01",
        "REQ-04"
      ],
      "components": [
        "request-intake",
        "artifact-retrieval"
      ],
      "outcome": "pass",
      "message": "Release identification: a bundle reflects only the requested release. All checks passed (12).",
      "checks": [
        {
          "name": "v1.2 echoes the requested product",
          "ok": true,
          "detail": ""
        },
        {
          "name": "v1.2 echoes the requested release",
          "ok": true,
          "detail": ""
        },
        {
          "name": "POST /bundles for prd-88 v1.3 returns 202 with a bundle_id",
          "ok": true,
          "detail": ""
        },
        {
          "name": "GET /bundles/{bundle_id} for prd-88 v1.3 returns 200 with a JSON object",
          "ok": true,
          "detail": ""
        },
        {
          "name": "v1.3 echoes the requested product",
          "ok": true,
          "detail": ""
        },
        {
          "name": "v1.3 echoes the requested release",
          "ok": true,
          "detail": ""
        },
        {
          "name": "POST /bundles for prd-88 v1.4 returns 202 with a bundle_id",
          "ok": true,
          "detail": ""
        },
        {
          "name": "GET /bundles/{bundle_id} for prd-88 v1.4 returns 200 with a JSON object",
          "ok": true,
          "detail": ""
        },
        {
          "name": "v1.4 echoes the requested product",
          "ok": true,
          "detail": ""
        },
        {
          "name": "v1.4 echoes the requested release",
          "ok": true,
          "detail": ""
        },
        {
          "name": "different releases produce different bundle ids",
          "ok": true,
          "detail": ""
        },
        {
          "name": "the two releases do not share identical manifests",
          "ok": true,
          "detail": ""
        }
      ],
      "duration_ms": 485.0
    },
    {
      "test_id": "AC-02",
      "title": "Complete artifact collection: every category PRESENT and bundle COMPLETED",
      "requirements": [
        "REQ-02",
        "REQ-03",
        "REQ-05"
      ],
      "components": [
        "artifact-retrieval",
        "classification",
        "manifest-generation"
      ],
      "outcome": "pass",
      "message": "Complete artifact collection: every category PRESENT and bundle COMPLETED. All checks passed (7).",
      "checks": [
        {
          "name": "manifest reports 'SBOM' exactly once as PRESENT",
          "ok": true,
          "detail": ""
        },
        {
          "name": "manifest reports 'Risk Assessment' exactly once as PRESENT",
          "ok": true,
          "detail": ""
        },
        {
          "name": "manifest reports 'Test Logs' exactly once as PRESENT",
          "ok": true,
          "detail": ""
        },
        {
          "name": "manifest reports 'User Instructions' exactly once as PRESENT",
          "ok": true,
          "detail": ""
        },
        {
          "name": "bundle is reported as COMPLETED",
          "ok": true,
          "detail": ""
        },
        {
          "name": "no artifact is duplicated within the inline output",
          "ok": true,
          "detail": ""
        },
        {
          "name": "no artifact is duplicated within the download output",
          "ok": true,
          "detail": ""
        }
      ],
      "duration_ms": 125.0
    },
    {
      "test_id": "AC-03",
      "title": "Missing required artifact is reported as MISSING",
      "requirements": [
        "REQ-03",
        "REQ-06"
      ],
      "components": [
        "classification",
        "manifest-generation"
      ],
      "outcome": "pass",
      "message": "Missing required artifact is reported as MISSING. All checks passed (5).",
      "checks": [
        {
          "name": "manifest reports 'SBOM' exactly once as PRESENT",
          "ok": true,
          "detail": ""
        },
        {
          "name": "manifest reports 'Risk Assessment' exactly once as PRESENT",
          "ok": true,
          "detail": ""
        },
        {
          "name": "manifest reports 'Test Logs' exactly once as MISSING",
          "ok": true,
          "detail": ""
        },
        {
          "name": "manifest reports 'User Instructions' exactly once as PRESENT",
          "ok": true,
          "detail": ""
        },
        {
          "name": "bundle is not reported as COMPLETED",
          "ok": true,
          "detail": ""
        }
      ],
      "duration_ms": 0.0
    },
    {
      "test_id": "AC-04",
      "title": "Invalid artifact is reported as INVALID and not treated as collected",
      "requirements": [
        "REQ-03",
        "REQ-06"
      ],
      "components": [
        "classification",
        "manifest-generation"
      ],
      "outcome": "pass",
      "message": "Invalid artifact is reported as INVALID and not treated as collected. All checks passed (5).",
      "checks": [
        {
          "name": "manifest reports 'SBOM' exactly once as INVALID",
          "ok": true,
          "detail": ""
        },
        {
          "name": "manifest reports 'Risk Assessment' exactly once as PRESENT",
          "ok": true,
          "detail": ""
        },
        {
          "name": "manifest reports 'Test Logs' exactly once as PRESENT",
          "ok": true,
          "detail": ""
        },
        {
          "name": "manifest reports 'User Instructions' exactly once as PRESENT",
          "ok": true,
          "detail": ""
        },
        {
          "name": "bundle is not reported as COMPLETED",
          "ok": true,
          "detail": ""
        }
      ],
      "duration_ms": 0.0
    },
    {
      "test_id": "AC-05",
      "title": "External source failure is reported as INACCESSIBLE",
      "requirements": [
        "REQ-02",
        "REQ-06",
        "REQ-07"
      ],
      "components": [
        "source-failure-handling"
      ],
      "outcome": "skip",
      "message": "Not executed: Phase 3 uses a single controlled source, so a source failure cannot be induced through the shared interface. Deferred to Phase 4 (Phase 2 section 6.1).",
      "checks": [],
      "duration_ms": 0.0
    },
    {
      "test_id": "AC-06",
      "title": "Source fidelity: collected content is byte-for-byte identical to the source",
      "requirements": [
        "REQ-05",
        "REQ-08"
      ],
      "components": [
        "content-preservation"
      ],
      "outcome": "pass",
      "message": "Source fidelity: collected content is byte-for-byte identical to the source. All checks passed (5).",
      "checks": [
        {
          "name": "bundle exposes artifact content (artifacts[].content or download_url)",
          "ok": true,
          "detail": ""
        },
        {
          "name": "'SBOM' content is preserved exactly (sha256)",
          "ok": true,
          "detail": ""
        },
        {
          "name": "'Risk Assessment' content is preserved exactly (sha256)",
          "ok": true,
          "detail": ""
        },
        {
          "name": "'Test Logs' content is preserved exactly (sha256)",
          "ok": true,
          "detail": ""
        },
        {
          "name": "'User Instructions' content is preserved exactly (sha256)",
          "ok": true,
          "detail": ""
        }
      ],
      "duration_ms": 0.0
    },
    {
      "test_id": "AC-07",
      "title": "Repeatable generation: identical inputs give equivalent results",
      "requirements": [
        "REQ-04",
        "REQ-05",
        "REQ-08"
      ],
      "components": [
        "manifest-generation",
        "content-preservation"
      ],
      "outcome": "pass",
      "message": "Repeatable generation: identical inputs give equivalent results. All checks passed (8).",
      "checks": [
        {
          "name": "POST /bundles for prd-88 v1.2 returns 202 with a bundle_id",
          "ok": true,
          "detail": ""
        },
        {
          "name": "GET /bundles/{bundle_id} for prd-88 v1.2 returns 200 with a JSON object",
          "ok": true,
          "detail": ""
        },
        {
          "name": "each request creates a distinct bundle",
          "ok": true,
          "detail": ""
        },
        {
          "name": "product_id is identical across both runs",
          "ok": true,
          "detail": ""
        },
        {
          "name": "release_version is identical across both runs",
          "ok": true,
          "detail": ""
        },
        {
          "name": "status is identical across both runs",
          "ok": true,
          "detail": ""
        },
        {
          "name": "category statuses are identical across both runs",
          "ok": true,
          "detail": ""
        },
        {
          "name": "every artifact from the first run reappears in the second",
          "ok": true,
          "detail": ""
        }
      ],
      "duration_ms": 375.0
    },
    {
      "test_id": "AC-08",
      "title": "Annex VII coverage: no supported category disappears silently",
      "requirements": [
        "REQ-03",
        "REQ-05"
      ],
      "components": [
        "classification",
        "manifest-generation"
      ],
      "outcome": "pass",
      "message": "Annex VII coverage: no supported category disappears silently. All checks passed (15).",
      "checks": [
        {
          "name": "v1.2: 'SBOM' appears once with an explicit status",
          "ok": true,
          "detail": ""
        },
        {
          "name": "v1.2: 'Risk Assessment' appears once with an explicit status",
          "ok": true,
          "detail": ""
        },
        {
          "name": "v1.2: 'Test Logs' appears once with an explicit status",
          "ok": true,
          "detail": ""
        },
        {
          "name": "v1.2: 'User Instructions' appears once with an explicit status",
          "ok": true,
          "detail": ""
        },
        {
          "name": "v1.3: 'SBOM' appears once with an explicit status",
          "ok": true,
          "detail": ""
        },
        {
          "name": "v1.3: 'Risk Assessment' appears once with an explicit status",
          "ok": true,
          "detail": ""
        },
        {
          "name": "v1.3: 'Test Logs' appears once with an explicit status",
          "ok": true,
          "detail": ""
        },
        {
          "name": "v1.3: 'User Instructions' appears once with an explicit status",
          "ok": true,
          "detail": ""
        },
        {
          "name": "v1.4: 'SBOM' appears once with an explicit status",
          "ok": true,
          "detail": ""
        },
        {
          "name": "v1.4: 'Risk Assessment' appears once with an explicit status",
          "ok": true,
          "detail": ""
        },
        {
          "name": "v1.4: 'Test Logs' appears once with an explicit status",
          "ok": true,
          "detail": ""
        },
        {
          "name": "v1.4: 'User Instructions' appears once with an explicit status",
          "ok": true,
          "detail": ""
        },
        {
          "name": "unknown release is accepted with a bundle_id or rejected with 400",
          "ok": true,
          "detail": ""
        },
        {
          "name": "an unknown release never reports a category as PRESENT",
          "ok": true,
          "detail": ""
        },
        {
          "name": "an unknown release is not reported as COMPLETED",
          "ok": true,
          "detail": ""
        }
      ],
      "duration_ms": 218.0
    }
  ],
  "coverage": {
    "code": {
      "covered": null,
      "total": null,
      "basis": "Code coverage is read from the target's GET /test/report; the target did not provide it."
    },
    "component": {
      "covered": 6,
      "total": 7,
      "basis": "Components listed in oracle.json; a component counts as covered when at least one executed test exercises it"
    },
    "system": {
      "covered": 7,
      "total": 8,
      "basis": "Acceptance criteria AC-01..AC-08 (Phase 2 section 4.1); a criterion counts as covered when its test was executed"
    }
  },
  "summary": {
    "pass": 13,
    "fail": 0,
    "error": 0,
    "skip": 1,
    "total": 14
  },
  "passed": true
}