# Risk Assessment — prd-88 v3.1

## Document control
Synthetic practice assessment. Product prd-88. Release v3.1. Assessment revision 1.
Owner Security Engineering. Review date 2026-11-18. Approval remains pending until the evidence listed below is collected.

## Scope and system description
This assessment covers operator web portal and its interfaces during normal use, deployment, maintenance and recovery. The review concentrates on tenant authorization and session handling. External services, operator workstations and update distribution are outside the trusted device boundary.

## Assets and trust boundaries
Protected assets include release integrity, customer configuration, retained telemetry, availability and incident evidence. Inputs crossing network and administrator boundaries are untrusted. Operators authenticate through the application's identity provider and receive only the permissions assigned to their organization.

## Assumptions and scoring
The device or service operates on a network reachable by other tenants or attackers. Administrators may make mistakes. Credentials can be revoked. Likelihood and impact use a 1–5 scale. Score equals likelihood times impact. Scores 1–4 are low, 5–9 medium, 10–15 high and 16–25 critical. These are synthetic project scoring conventions.

## Risk register
### RA-01 — Primary boundary failure
Scenario — An authenticated operator changes an object identifier to export another customer's assessment.
Affected assets — integrity, availability and confidential project records.
Inherent likelihood 3, impact 5, score 15, high.
Treatment — Check organization membership on every create, status and download operation. Resolve the user identity from the authenticated session. Scope storage queries to that organization.
Verification — Create two organizations with distinct documents. Attempt cross-organization status and download requests, including after logout and membership removal. Confirm denial and an audit event.
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
