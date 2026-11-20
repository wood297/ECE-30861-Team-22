# Phase 5 cross-team testing: notes from Team 22 for Team 24

**From:** Team 22 · **For:** Team 24 · **Date:** 2026-11-19

## 1. The short version

- **Nothing you rely on has changed.** Our bundle API is the same as in Phase 4, and every Phase 4 shared release returns exactly what it did in our 2026-11-01 runs (15/15 and 14/14 both ways). That includes `v1.2-github-inaccessible` for AC-05.
- **Your Phase 4 tester and expected results should pass against our Phase 5 system unchanged.** We'll run ours against yours the same way.
- **We set up a small amount of test data on your Jenkins** (§2). Please leave it in place.
- **Shared Jenkins test cases are optional** (§4). If you've built a Jenkins connector and want AC-05 or AC-06 checked with a real Jenkins source, the data is ready for both teams. If not, skip it.

## 2. What we did on your Jenkins server

Server: `https://jenk-30861.jscarfo.com`, using the `team22` account you gave us.

| What | Details |
|---|---|
| Folder | `pod` (top level) |
| Job | `pod/cra-test-logs`, a Pipeline job. It downloads one `test_logs.md` from the public GitHub repo `prithika-sundar/cra-artifact-test` at a fixed commit and archives it. It uses only `curl` and `sha256sum`, with no plugins or credentials |
| Build #1 | Failed on purpose: it only registered the job's parameters |
| Build #2 | Archives `artifacts/prd-88/v1.5-jenkins/test_logs.md` (3232 bytes). **Kept forever** |
| Build #3 | Archives nothing (the "file missing" case). **Kept forever** |
| Build #4 | Archives an empty `artifacts/prd-88/v1.5-jenkins-empty/test_logs.md`. **Kept forever** |

How our deployed system uses it:
- **Only GET requests**, to read build #2–#4 listings and artifacts, with an API token for `team22` and `User-Agent: cra-documentation-service`.
- It never starts builds or changes anything.
- **About 2 requests per bundle**, cached for 10 minutes. Only bundles for the `v1.5-jenkins*` releases call Jenkins at all.

**Please:**
- Don't delete `pod/` or rebuild or delete builds 2–4.
- Tell us before restarting Jenkins or changing Cloudflare near a test window.
- **Cloudflare:** your Cloudflare blocks Python's default `User-Agent: Python-urllib/3.x` with a 403. Our requests send their own User-Agent and get through, at least from a laptop; we'll confirm from our server, `13.58.35.224`. If your Jenkins connector is written in Python, set a User-Agent or it'll be blocked too.
- **You're welcome to use these builds** to test your own Jenkins connector.

## 3. What we built (for context)

Our system now gets documents through **connectors**: Jenkins (a pinned build's archived artifact), GitHub (a file at a pinned commit), Confluence (a pinned page attachment version) and our local files. Each category of a release is pinned to one file in one source. If a source fails, only that category is INACCESSIBLE, and the bundle is still PARTIAL_SUCCESS with the other files.

The only visible API change: `external_source` in the manifest can now be `fixtures`, `GitHub`, `Jenkins`, `Confluence`, or `none` (no source configured for the category, reported as MISSING). Neither team's tester checks this field.

## 4. Optional: shared Jenkins test cases

Only if you want them. Both systems would serve these three releases identically (product `prd-88`). Test Logs come from the builds in §2; the other three categories are byte-identical copies of your existing `v1.2` files.

| Release | Test Logs from | Test Logs status | Other three | Bundle |
|---|---|---|---|---|
| `v1.5-jenkins` | build #2 | PRESENT, sha256 `f5cf3fd835fdbbe957222dcc18a4fa12d3d192936e20a7eeee7cea22b1658173` | PRESENT | COMPLETED |
| `v1.5-jenkins-missing` | build #3 | INACCESSIBLE, with a `source_failure_detail` | PRESENT | PARTIAL_SUCCESS |
| `v1.5-jenkins-empty` | build #4 | INVALID (empty file), not in the download | PRESENT | PARTIAL_SUCCESS |

The rules are the ones we already share from Phase 4:
- a configured file the source can't supply is INACCESSIBLE, not MISSING
- an empty file is INVALID
- bytes are returned unchanged
- use the pinned build number, never `lastSuccessfulBuild`

Our system already serves these. If you want them, tell us, and both teams add them to their testers' expected results before the test window.

## 5. Running cross-team testing

The same as Phase 4 (calls in our Phase 4 doc §3–§4):

| | |
|---|---|
| Our system | `https://team22.13-58-35-224.sslip.io/system` (no auth) |
| Our tester | `https://team22.13-58-35-224.sslip.io/tester` |
| Polling | Bundles that read Jenkins usually finish in 1–2 s, at most about 20 s. Phase 4 releases still finish in under a second |
| Phase 5 report field | Our tester adds `coverage.model` only when it tests our own system. Cross-team reports leave it out, as the spec says |

## 6. What we need from you

1. **Your registry details**, if anything changed since Phase 4: system URL, tester URL, token, and which tester profile we should run (Phase 4: `team24`).
2. **A test window.** We'll pause our automatic deploys during it; please keep Jenkins up.
3. **Yes or no on the optional Jenkins cases in §4.**
4. **Only if our server gets a Cloudflare 403** (we'll tell you): a skip rule for `13.58.35.224` on `jenk-30861.jscarfo.com`. Steps are in `TEAM24_CLOUDFLARE_ALLOWLIST.md`.
