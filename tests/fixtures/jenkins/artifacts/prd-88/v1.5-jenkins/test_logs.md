# Test Logs: prd-88 v1.5-jenkins

Synthetic test report for the Team 22 / Team 24 pod fixtures. It follows the CRA Annex VII(6)
layout in Team 22's docs/PHASE5_JENKINS_PLAN.md section 5.

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
| 10 | 9 | 0 | 1 |

Overall result: PASS

## Coverage of essential requirements (Annex I)
| Requirement | What was tested | Tests | Result |
|---|---|---|---|
| Part I (2)(a) no known exploitable vulnerabilities | dependency scan against the SBOM | SEC-01 | PASS |
| Part I (2)(b) secure by default | no default passwords; factory reset restores secure state | SEC-02, SEC-03 | PASS |
| Part I (2)(c) security updates | signed update accepted, bad signature rejected | SEC-04 | PASS |
| Part I (2)(d) protection from unauthorised access | invalid token returns 401 | SEC-05 | PASS |
| Part I (2)(e) confidentiality | TLS 1.2 or newer only on the API | SEC-06 | PASS |
| Part I (2)(f) integrity | tampered configuration rejected | SEC-07 | PASS |
| Part I (2)(h) availability and DoS resilience | rate limit returns 429 | SEC-08 | PASS |
| Part I (2)(m) secure data removal | delete-all wipes user data | SEC-09 | SKIPPED (see Deviations) |
| Part I (2)(g)(i)(j)(k)(l) | not applicable: see risk assessment section 3 | - | N/A |
| Part II (1) SBOM produced | CycloneDX SBOM generated for this build | SBOM-01 | PASS |
| Part II (3) regular security testing | this pipeline runs on every release tag | - | evidence: this report |

## Test results
| ID | Test | Outcome | Duration |
|---|---|---|---|
| SEC-01 | test_no_known_vulnerable_dependencies | PASS | 4.10s |
| SEC-02 | test_no_default_credentials | PASS | 0.08s |
| SEC-03 | test_factory_reset_restores_secure_defaults | PASS | 1.42s |
| SEC-04 | test_signed_update_rejects_bad_signature | PASS | 0.12s |
| SEC-05 | test_invalid_token_rejected | PASS | 0.03s |
| SEC-06 | test_tls_minimum_version | PASS | 0.51s |
| SEC-07 | test_tampered_config_rejected | PASS | 0.09s |
| SEC-08 | test_rate_limit_returns_429 | PASS | 2.33s |
| SEC-09 | test_delete_all_wipes_user_data | SKIPPED | 0.00s |
| SBOM-01 | test_cyclonedx_sbom_generated | PASS | 0.64s |

## Security scans
- Dependency scan: pip-audit 2.7, advisory database dated 2026-11-09. 0 known vulnerabilities.

## Deviations and failures
- SEC-09 skipped: the data-wipe endpoint is not built yet. The risk is accepted in risk
  assessment R-07, and the fix is planned for v1.6.

## Raw output (excerpt)
    [2026-11-10 14:02:15] [PASS] SEC-04 test_signed_update_rejects_bad_signature (0.12s)
    [2026-11-10 14:02:15] [PASS] SEC-05 test_invalid_token_rejected (0.03s)
    [2026-11-10 14:03:46] [SKIP] SEC-09 test_delete_all_wipes_user_data (endpoint not built)
    10 tests: 9 passed, 0 failed, 1 skipped
