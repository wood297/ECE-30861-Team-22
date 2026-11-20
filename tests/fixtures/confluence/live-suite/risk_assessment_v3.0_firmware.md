# Risk Assessment — prd-88 v3.0

## Document control
Synthetic practice assessment. Product prd-88. Release v3.0. Assessment revision 1.
Owner Security Engineering. Review date 2026-11-18. Approval remains pending until the evidence listed below is collected.

## Scope and system description
This assessment covers connected sensor firmware and its interfaces during normal use, deployment, maintenance and recovery. The review concentrates on update signing and rollback. External services, operator workstations and update distribution are outside the trusted device boundary.

## Assets and trust boundaries
Protected assets include release integrity, customer configuration, retained telemetry, availability and incident evidence. Inputs crossing network and administrator boundaries are untrusted. Operators authenticate through the application's identity provider and receive only the permissions assigned to their organization.

## Assumptions and scoring
The device or service operates on a network reachable by other tenants or attackers. Administrators may make mistakes. Credentials can be revoked. Likelihood and impact use a 1–5 scale. Score equals likelihood times impact. Scores 1–4 are low, 5–9 medium, 10–15 high and 16–25 critical. These are synthetic project scoring conventions.

## Risk register
### RA-01 — Primary boundary failure
Scenario — A network attacker substitutes an unsigned firmware image through a compromised update mirror.
Affected assets — integrity, availability and confidential project records.
Inherent likelihood 3, impact 5, score 15, high.
Treatment — Verify the release signature and device model before writing the inactive partition. Boot only signed images. Retain a known-good partition and enforce a monotonic security version.
Verification — Submit a modified image, a wrong-model image and an older vulnerable image. All three must be rejected before activation. Interrupt power during download and activation and verify recovery.
Residual likelihood 1, impact 4, score 4, low, conditional on passing verification.
Owner — Component maintainer. Due before release acceptance.

### RA-02 — Credential disclosure
Scenario — a diagnostic captures an authorization header and exposes the credential to log readers.
Inherent likelihood 3, impact 4, score 12, high.
Treatment — mask raw credentials and derived Basic authorization values. Restrict secret retrieval to the runtime role. Exclude credentials from manifests and exported bundles.
Verification — run error, timeout and malformed-response cases with a unique fake secret and search all captured outputs for the raw and encoded values.
Residual likelihood 1, impact 4, score 4, low.
Owner — Platform security. Evidence must include the test report.

### RA-03 — Incomplete incident evidence
Scenario — a restart erases the current failure state, preventing support from distinguishing a missing document from an upstream outage.
Inherent likelihood 3, impact 3, score 9, medium.
Treatment — persist sanitized job outcomes, source identifiers and timestamps. Keep a bounded audit trail and document its retention.
Verification — trigger a controlled failure, restart the process and confirm the stored outcome remains available to the authorized operator.
Residual likelihood 1, impact 3, score 3, low.
Owner — Operations.

## Release verification and evidence
Record the exact release identifier, source revision and test environment with each result. A missing test result leaves its associated risk open. Review the negative tests as well as successful operation. Re-run affected checks after control changes.

## Incident response and maintenance
The on-call maintainer triages reports, disables affected credentials where necessary, preserves sanitized evidence and coordinates a corrective release. Review this assessment after an incident, material architecture change or newly relevant vulnerability.

## Residual risk acceptance
The release owner reviews all open items before signing acceptance. High and critical residual risks require documented treatment and a named decision owner. This synthetic document records a test plan and does not assert that production controls were verified.
