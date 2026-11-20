# Design requirements → tests (coverage.model)

From Phase 5, the Cross-team Testing Interface asks a tester to add `coverage.model` to its report **when it tests its own team's system**, and to leave it out of cross-team reports:

- `total`: the number of design requirements the team lists for the milestone
- `covered`: how many of them the suite evaluated
- `basis`: the design version, and a link to this mapping

**Design version:** Team 22 design requirements REQ-01 to REQ-08, as listed in our most recent design document, the **Phase 4 report, Q1.1** (page 2, "Demonstrated by"). The same eight requirements, with the same names, have been used since Phase 2.

**Rule:** a requirement is *evaluated* when at least one suite test mapped to it ran and gave `pass` or `fail`. `skip` and `error` don't count. Like the code, component and system measures, this records what the suite exercised, not whether the system behaved correctly; pass or fail is in `results`.

| Requirement | Name | Evaluated by these suite tests (Phase 4 Q1.1) |
|---|---|---|
| REQ-01 | Release Identification | AC-01 |
| REQ-02 | Artifact Retrieval | AC-02, AC-05 |
| REQ-03 | Documentation Category Status | AC-02, AC-03, AC-04, AC-05, AC-08 |
| REQ-04 | Release Association | AC-01, AC-07 |
| REQ-05 | Documentation Bundle Generation | AC-02, AC-05, AC-06, AC-07 |
| REQ-06 | Missing or Invalid Evidence | AC-03, AC-04, AC-05 |
| REQ-07 | External Source Failure | AC-05 |
| REQ-08 | Artifact Content Preservation | AC-05, AC-06, AC-07 |

The Phase 4 report also cites "format-specific archive tests" for REQ-08. Those are unit tests in `tests/`, run by CI rather than by the tester, so they aren't counted here.

**Where it lives in code:**
- `test_runner/design_requirements.json` is the copy the tester reads. `tests/test_model_coverage.py` fails if it and this table ever disagree.
- The tester adds `coverage.model` when the target is our deployed system (`https://team22.13-58-35-224.sslip.io/system`), any base URL listed in `CRA_OWN_SYSTEM_URLS` (comma-separated), or a system on `localhost`. For every other target, such as Team 24's system, the field is left out.

**When the design changes:** update the Phase 5 report's requirement list, this table and the JSON together. The test checks the last two match.
