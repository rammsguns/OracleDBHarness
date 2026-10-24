# Security review, 2026-10-24 (A7, G2 agent stand-in)

Two independent AI reviews of the production boundary at commit `76a7c37`. Each reviewer
worked on its own worktree, without seeing the other's report:

- [agy, Gemini 3.8 Flash](security-review-2026-10-24-agy.md): 2 Medium, 4 Low, 2 Info.
- [Claude, claude-opus-5-5](security-review-2026-10-24-claude.md): 2 High, 6 Medium,
  6 Low, 3 Info.

This reconciliation and the change approval are by Claude (AI agent, coordinator and
acting DBA, not a human). **These are AI agent reviews, not a human security sign-off.**
G2 ("security review and DBA change approval with names and dates") stays open for
people. The change approval below covers the test targets (D1, D2, D4) only.

## Verdicts

Both reviewers: **no blocking findings for a test-stand-in pilot.**

Both reviewers found no path for a signed-in non-administrator to write to a target
labelled `production`, and no way around authentication with the shipped compose
configuration.

## Where the models agree

| Topic | Claude | agy | Disposition |
| --- | --- | --- | --- |
| No CSP, framing or other security headers on nginx; plain HTTP | C-8 (Medium) | SEC-AGY-001 (Medium) | Track. Required before a non-loopback deployment |
| The placeholder dev token secret is never warned about, despite a code comment saying it is | C-3, part (Medium) | SEC-AGY-003 (Low) | Track |
| No tamper-evidence on the audit log | C-7 (Medium) | SEC-AGY-007 (Info) | Track. An operational control (separate store or SIEM) before production |

## Found by one model only

The coordinator checked both High findings against the code.

| Finding | Severity | Checked | Disposition |
| --- | --- | --- | --- |
| C-1. The observation-only rule keys on the exact free-text label `production` (`schemas.py` `ProfileIn.environment: str`; `policy.py:83`, `execution.py:347`, `routers/admin.py:170`). A target labelled `Production` or `prod` gets the non-production treatment | High | Confirmed | **Fix now**: validate the environment against a closed set |
| C-2. Administrative changes (users, secret references, targets, grants, integration credentials, revocations) write no audit event | High | Confirmed by reading `routers/admin.py` | **Fix now**: audit every admin mutation |
| C-3, rest. The defaults fail open (dev auth, development env); the shipped compose sets OIDC | Medium | | Track |
| C-4. An empty endpoint allowlist allows any endpoint; a host-only entry allows any port | Medium | | Track |
| C-5. Failed, outcome-unknown and refused-access executions are not audited | Medium | | Track |
| C-6. The commit-recovery runbook refers to session IDs that the audit events do not carry | Medium | | Track |
| SEC-AGY-002. HTTPS is not enforced for the OIDC issuer and JWKS outside development | Medium | | Track |
| SEC-AGY-005, -006, -008, and Claude's Low and Info findings | Low/Info | | Track |

The two High findings, which only one model reported, are the main reason for running
two models.

## Change approval (acting DBA, test targets only)

The fixes for C-1 and C-2 are approved for the test targets. Everything else is tracked
here and is not approved for production by this review.
