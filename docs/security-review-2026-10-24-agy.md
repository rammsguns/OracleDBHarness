# Independent AI Security Review (agy) — OracleDBHarness Production Boundary

**Reviewer:** agy (antigravity)  
**Model:** Gemini 3.8 Flash (Medium)  
**Date:** 2026-10-24  
**Commit Reviewed:** `76a7c37`  
**Scope:** Production boundary (Authentication, Authorization, Oracle boundary, Secrets, Audit, Deployment, Copilot/Kiwi)  
**Method:** Static code audit, control architecture analysis, dataflow and boundary tracing  

*AI agent review, not a human security sign-off.*

---

## 1. Executive Summary & Verdict

This review audited the production boundary of OracleDBHarness across its primary security controls: OIDC authentication, dev-auth barriers, role-based and grant-based authorization, statement classification and the Stage 1 read-only / observation-only rule, licensed view exclusion, secret resolution, audit trail fidelity, restart reconciliation, container/deployment definitions, and Kiwi copilot interactions.

No critical or high-severity vulnerabilities were identified that allow unauthorized writes or DDL on production targets, bypass target grants, expose credentials, or break out of local model confinement. The production observation-only boundary is rigorously enforced at multiple layers: profile configuration, grant checks, statement risk classification, and production identity probing (Oracle 19c check). A small number of medium and low severity defense-in-depth issues were noted (specifically around reverse-proxy security headers, HTTPS scheme enforcement on OIDC URLs, dev token issuer validation, and container user privileges).

**Verdict:** `No blocking findings for a test-stand-in pilot`

---

## 2. Findings Summary Table

| ID | Severity | Area | File:Line | Issue | Why It Matters | Recommended Fix | Confidence |
|---|---|---|---|---|---|---|---|
| SEC-AGY-001 | Medium | Deployment | `deploy/nginx.conf:1-33`, `apps/web/index.html:1-12` | Missing HTTP security headers (CSP, X-Frame-Options, X-Content-Type-Options, HSTS, Referrer-Policy) | Without CSP and frame options, the web console is exposed to clickjacking and lacks defense-in-depth against XSS or MIME confusion. | Add standard security headers in `deploy/nginx.conf` (`Content-Security-Policy`, `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: strict-origin-when-cross-origin`). | Confirmed by code reading |
| SEC-AGY-002 | Medium | Authentication | `services/api/harness_api/config.py:35-37`, `services/api/harness_api/security.py:166-175` | Unenforced HTTPS scheme for OIDC Issuer and JWKS URL | Configuration allows plaintext `http://` for OIDC endpoints. In production or shared environments, this permits Man-in-the-Middle tampering of JWKS keys or discovery documents. | Add a field validator in `config.py` requiring `https://` for `oidc_issuer` and `oidc_jwks_url` outside `development` mode. | Confirmed by code reading |
| SEC-AGY-003 | Low | Authentication | `services/api/harness_api/config.py:48-50`, `services/api/harness_api/config.py:190-235` | Default `dev_token_secret` not flagged by `startup_warnings()` | Code comments claim `startup_warnings()` flags the default placeholder secret, but no check exists. If dev auth is enabled outside dev, no warning is emitted. | Add a check in `startup_warnings()` warning when `dev_token_secret == "change-me-in-any-shared-environment"`. | Confirmed by code reading |
| SEC-AGY-004 | Low | Authentication | `services/api/harness_api/security.py:348-354` | Loose Issuer check in Dev Token decoding | In dev mode, `jwt.decode` requires the `iss` claim to be present (`require_iss: True`), but does not verify that it equals `"oracledbharness-dev"`. | Pass `issuer="oracledbharness-dev"` into `jwt.decode` options in `_verify` for dev mode. | Confirmed by code reading |
| SEC-AGY-005 | Low | Deployment | `deploy/Dockerfile.web:11-14` | Web container runs as root user | The nginx image (`nginx:1.27-alpine`) runs worker master/processes as root by default, unlike `Dockerfile.api` which drops root to user `harness`. | Specify `USER nginx` or use an unprivileged nginx base image (`nginxinc/nginx-unprivileged`). | Confirmed by code reading |
| SEC-AGY-006 | Low | Secrets | `services/api/harness_api/secrets.py:28-36` | `env` secret provider allowed outside development | `SecretResolver` permits `provider="env"` regardless of environment setting. An administrator could point secret references at process environment variables (e.g. `POSTGRES_PASSWORD`). | Disallow `provider="env"` when `HARNESS_ENV != "development"` or restrict locators to an allowlist. | Confirmed by code reading |
| SEC-AGY-007 | Info | Audit | `services/api/harness_api/models.py:254-274` | Absence of cryptographic tamper-evidence in audit records | `AuditEvent` is an ordinary relational table. While application routes are append-only, direct database access allows undetected alteration or deletion of audit logs. | Implement cryptographic hash chaining (Merkle or HMAC chain) or database-level append-only tables/triggers if strict non-repudiation is required. | Confirmed by code reading |
| SEC-AGY-008 | Info | Copilot | `services/api/harness_api/config.py:69-89`, `services/api/harness_api/copilot/service.py:1014-1033` | Startup does not reject `copilot_enabled=True` in production mode | `production.env.example` dictates that Copilot/Kiwi are disabled in production. Runtime gates block production target queries, but startup config does not enforce `copilot_enabled=False`. | Add a model validator in `config.py` failing startup if `env == "production"` and `copilot_enabled is True`. | Confirmed by code reading |

---

## 3. Detailed Findings & Analysis

### SEC-AGY-001: Missing HTTP Security Headers in Nginx Reverse Proxy
- **Area:** Deployment
- **Location:** `deploy/nginx.conf:1-33`, `apps/web/index.html:1-12`
- **Class of Problem:** Missing HTTP Security Headers / Defense-in-Depth
- **Details:**
  `nginx.conf` sets proxy headers for forwarding (`Host`, `X-Forwarded-For`, `X-Forwarded-Proto`) and configures streaming timeouts for the API, but does not emit standard security headers:
  - `Content-Security-Policy`: No CSP is set in `nginx.conf`, `index.html`, or `app.py`.
  - `X-Frame-Options`: Missing `DENY` or `SAMEORIGIN`, leaving the web console vulnerable to Clickjacking attacks if embedded in a malicious iframe.
  - `X-Content-Type-Options`: Missing `nosniff`, permitting MIME sniffing on served static assets.
  - `Referrer-Policy`: Missing `strict-origin-when-cross-origin`.
- **Recommended Fix:** Add security headers to the `server` block in `deploy/nginx.conf`:
  ```nginx
  add_header X-Frame-Options "DENY" always;
  add_header X-Content-Type-Options "nosniff" always;
  add_header Referrer-Policy "strict-origin-when-cross-origin" always;
  add_header Content-Security-Policy "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self' https:; img-src 'self' data:; font-src 'self';" always;
  ```

### SEC-AGY-002: Unenforced HTTPS Scheme for OIDC Issuer and JWKS URL
- **Area:** Authentication
- **Location:** `services/api/harness_api/config.py:35-37`, `services/api/harness_api/security.py:166-175`
- **Class of Problem:** Insecure Transport / Missing Scheme Validation
- **Details:**
  In `Settings`, `oidc_issuer` and `oidc_jwks_url` are validated for presence when `auth_mode == "oidc"`, but no validation ensures the URLs start with `https://`. While `ProviderDocument._fetch` uses `httpx.get` (which verifies certificates when HTTPS is used), configuring plaintext `http://` URLs would cause public key fetching and OpenID discovery to travel in the clear over untrusted networks.
- **Recommended Fix:**
  Add a pydantic validator in `config.py`:
  ```python
  @field_validator("oidc_issuer", "oidc_jwks_url")
  @classmethod
  def _must_be_https(cls, value: str, info) -> str:
      if info.data.get("auth_mode") == "oidc" and value:
          if not value.startswith("https://") and info.data.get("env") != "development":
              raise ValueError("OIDC issuer and JWKS URLs must use HTTPS.")
      return value
  ```

### SEC-AGY-003: Default `dev_token_secret` Not Flagged by `startup_warnings()`
- **Area:** Authentication
- **Location:** `services/api/harness_api/config.py:48-50`, `services/api/harness_api/config.py:190-235`
- **Class of Problem:** Misleading Documentation / Incomplete Warning Routine
- **Details:**
  `config.py` line 48 notes: `# This is the placeholder value, not a credential: startup_warnings() reports it.` However, `startup_warnings()` has no check for `self.dev_token_secret == "change-me-in-any-shared-environment"`. If an operator runs with `HARNESS_AUTH_MODE=dev` and `HARNESS_ALLOW_DEV_AUTH_OUTSIDE_DEVELOPMENT=true`, the system starts without warning that local tokens are signed with the well-known default key.
- **Recommended Fix:** Add to `startup_warnings()` in `config.py`:
  ```python
  if self.auth_mode == "dev" and self.dev_token_secret == "change-me-in-any-shared-environment":
      warnings.append("HARNESS_DEV_TOKEN_SECRET is using the well-known placeholder default.")
  ```

### SEC-AGY-004: Loose Issuer Check in Dev Token Decoding
- **Area:** Authentication
- **Location:** `services/api/harness_api/security.py:348-354`
- **Class of Problem:** Insufficient Claim Verification
- **Details:**
  In `issue_dev_token`, the JWT is encoded with `"iss": "oracledbharness-dev"`. In `_verify`, `jwt.decode` is called with `options=REQUIRED_CLAIMS` (which enforces that an `iss` claim exists), but `issuer="oracledbharness-dev"` is omitted from the arguments. While dev tokens are only accepted in dev mode and are signed with `dev_token_secret`, verifying the exact issuer string matches standard JWT validation practices.
- **Recommended Fix:**
  Pass `issuer="oracledbharness-dev"` to `jwt.decode` in `security.py:348`.

### SEC-AGY-005: Web Container Process Runs as Root
- **Area:** Deployment
- **Location:** `deploy/Dockerfile.web:11-14`
- **Class of Problem:** Container Privilege Escalation Risk / Least Privilege Violation
- **Details:**
  `deploy/Dockerfile.web` builds on `nginx:1.27-alpine` without declaring `USER nginx`. In standard Alpine nginx images, master and worker processes spawn as root unless configured otherwise. In contrast, `deploy/Dockerfile.api` creates a dedicated system user `harness` (uid 10001) and executes under `USER harness`.
- **Recommended Fix:**
  Configure `deploy/Dockerfile.web` to drop root privileges or use the official `nginxinc/nginx-unprivileged:alpine` base image.

### SEC-AGY-006: SecretReference `env` Provider Unrestricted Outside Development
- **Area:** Secrets
- **Location:** `services/api/harness_api/secrets.py:28-36`, `services/api/harness_api/routers/admin.py:62-79`
- **Class of Problem:** Secret Scope Creep
- **Details:**
  `SecretResolver` supports `provider="env"`, reading secrets directly from process environment variables (`os.environ.get(locator)`). While documented for local development, administrators can create a `SecretReference` with `provider="env"` in staging or production. An administrator could map environment variables (such as `POSTGRES_PASSWORD` or API internal variables) as target credentials.
- **Recommended Fix:**
  In `SecretResolver.resolve`, check `if self._env != "development" and reference.provider == "env": raise PolicyError("The 'env' secret provider is permitted in development only.")`.

### SEC-AGY-007: Absence of Cryptographic Tamper-Evidence in Audit Records
- **Area:** Audit
- **Location:** `services/api/harness_api/models.py:254-274`
- **Class of Problem:** Audit Trail Non-Repudiation Gap
- **Details:**
  `AuditEvent` records capture comprehensive execution metadata (`actor_subject`, `operation_id`, `statement_fingerprint`, `risk_class`, `policy_decision`, `outcome`). The application code exposes no update or delete routes for `AuditEvent`. However, the table lacks cryptographic hash chaining (e.g. linking each event to the previous event's SHA-256 hash). If the metadata store (SQLite or PostgreSQL) is compromised directly, records could be altered or pruned without leaving a cryptographic trace.
- **Recommended Fix:**
  For high-assurance regulatory compliance, consider adding an HMAC or SHA-256 chain field (`previous_event_hash`) to `AuditEvent` rows or streaming events to a dedicated append-only syslog/SIEM target.

### SEC-AGY-008: Startup Does Not Reject `copilot_enabled=True` in Production Mode
- **Area:** Copilot / Kiwi
- **Location:** `services/api/harness_api/config.py:69-89`, `services/api/harness_api/copilot/service.py:1014-1033`
- **Class of Problem:** Configuration Defense-in-Depth
- **Details:**
  `PLAN.md` and `production.env.example` specify that Copilot and Kiwi must remain disabled in production. At runtime, `CopilotService._check_production_access` reliably catches and refuses any request referencing a production target. However, `Settings` in `config.py` allows the API process to start with `HARNESS_ENV=production` and `HARNESS_COPILOT_ENABLED=true` without a validation error.
- **Recommended Fix:**
  Add a model validator in `config.py`:
  ```python
  if self.env == "production" and (self.copilot_enabled or self.kiwi_enabled):
      raise ValueError("Copilot and Kiwi must be disabled when HARNESS_ENV=production.")
  ```

---

## 4. Checked and Found Sound

The following critical areas were thoroughly audited and found to have robust, well-architected defenses:

1. **Production Observation-Only Isolation:**
   - Free-form worksheets (`PERMISSION_WORKSHEET`) cannot be opened on production targets (`services/api/harness_api/policy.py:167-173`).
   - Compilation (`PERMISSION_COMPILE`) and mutating operations (`RiskClass.SESSION_WRITE`, `RiskClass.PERSISTENT_WRITE`, `RiskClass.ADMINISTRATIVE`) are unconditionally blocked on production targets (`services/api/harness_api/policy.py:175-185`).
   - Target creation explicitly forbids setting `worksheets_enabled=True` or `mutating_runbooks_enabled=True` on `production` environments (`services/api/harness_api/routers/admin.py:170-177`).
   - Target identity verification requires a successful probe of Oracle Database 19c for all production targets (`services/api/harness_api/policy.py:147-156`).
   - Copilot requests referencing production targets (by ID, reference, or name) are rejected upfront before invoking the model or consuming token budget (`services/api/harness_api/copilot/service.py:1014-1033`).

2. **OIDC Token Validation & Public Client Security:**
   - In OIDC mode, JWT signatures are validated using RS256/ES256 against cached JWKS retrieved from the provider (`services/api/harness_api/security.py:355-375`).
   - Claims `aud`, `exp`, `iss`, and `sub` are strictly required (`REQUIRED_CLAIMS`).
   - Key rotation is supported with a rate-limited cache refresh interval (60s) to prevent DoS via bogus `kid` values (`services/api/harness_api/security.py:129-140`).
   - Console sign-in uses authorization code flow with PKCE (S256, 48-byte cryptographically random verifier, 32-byte state), and pending states in `sessionStorage` have a 10-minute TTL and are removed on single use (`apps/web/src/oidc.ts:70-106`).
   - Access tokens are retained exclusively in tab memory and are never persisted in `localStorage` or `sessionStorage` (`apps/web/src/api.ts:48-60`).

3. **Authorization & IDOR Resistance:**
   - Roles are assigned from the internal `User` record, never trusted from caller headers or incoming token role claims (`services/api/harness_api/security.py:282-300`).
   - Target access requires an explicit `UserTargetGrant`; administrators do not implicitly inherit database access (`services/api/harness_api/policy.py:88-103`).
   - Worksheets are strictly checked against the leasing user ID (`services/worker/harness_worker/sessions.py:178-184`).
   - Saved scripts, observations, and executions are queried strictly by the authenticated `user_id` (`routers/worksheet.py:153-157`, `routers/tuning.py:250-254`, `routers/history.py:53-56`).
   - Copilot proposals and apply checks verify matching `actor_key` (`services/api/harness_api/copilot/service.py:2133-2135`).

4. **Integration Credential Boundary:**
   - Integration tokens (`odbh_...`) are cryptographically generated (32 url-safe random bytes), hashed with SHA-256, and verified with `hmac.compare_digest` with dummy comparison on missing candidates to prevent timing side-channels (`services/api/harness_api/security.py:312-328`).
   - Integration credentials are restricted to `copilot:assist` scope; they cannot open database sessions, worksheets, or run Kiwi tools (`services/api/harness_api/policy.py:91-96`).

5. **Exclusion of Management Pack Licensed Views:**
   - Strict avoidance of Oracle Diagnostic Pack and Tuning Pack views (`DBA_HIST_*`, `V$ACTIVE_SESSION_HISTORY`, `V$SQL_MONITOR`, `DBMS_SQLTUNE`, `DBMS_WORKLOAD_*`, `DBMS_ADDM`).
   - Verified that no catalog SQL file or python module references any of these views, backed by static AST and text scanning tests (`tests/unit/test_licensed_features.py`).

6. **Oracle Boundary, Binds, and Statement Sanitation:**
   - Catalog queries separate identifier parameters from query bind parameters. Identifiers pass through `quote_identifier`, which rejects embedded double quotes, NUL bytes, and lengths > 128 chars (`services/worker/harness_worker/statement.py:256-274`).
   - All runtime query parameters are bound via python-oracledb bind mechanisms (`services/worker/harness_worker/backend/oracle.py:341`).
   - Statements are parsed and normalized with comment/literal masking; semicolons in SQL queries are stripped safely without truncating PL/SQL bodies (`services/worker/harness_worker/statement.py:187-241`).
   - Row counts and byte limits are enforced incrementally during cursor fetch (`services/worker/harness_worker/backend/oracle.py:515-537`).
   - Statements are constrained by driver round-trip timeout `cur.connection.call_timeout` (`services/worker/harness_worker/backend/oracle.py:325`).

7. **Database Endpoint Allowlist (`HARNESS_ALLOWED_ENDPOINTS`):**
   - Endpoints are validated both during target creation (`routers/admin.py:135-141`) and dynamically upon every connection attempt in `ExecutionService._check_endpoint_allowed` (`services/api/harness_api/execution.py:325-335`).

8. **Secret Handling & Containment:**
   - Database passwords are never written to the metadata store, logs, or execution rows (`services/api/harness_api/models.py:3-10`).
   - Password files are resolved via `SecretResolver` using strict `Path.resolve()` containment checks against `secret_dir`, preventing path traversal and symlink escapes (`services/api/harness_api/secrets.py:43-50`).

9. **Restart Reconciliation & Crash Consistency:**
   - Single-owner claim locking on store startup via `StoreOwner` row (`services/api/harness_api/recovery.py:222-264`).
   - Interrupted work is reconciled honestly: undispatched statements become `cancelled`, interrupted reads become `failed`, and in-flight writes or commits become `outcome_unknown`, requiring operator verification (`services/api/harness_api/recovery.py:131-150`).
   - Never redispatches statements automatically, avoiding duplicate write application.

10. **Copilot Local Confinement & Kiwi Sandboxing:**
    - `LoopbackEndpoint` strictly resolves and pins to IPv4/IPv6 loopback addresses (`127.0.0.0/8`, `::1`), disallows proxies, and refuses HTTP redirects (`services/api/harness_api/copilot/local.py:92-179`).
    - Kiwi toolbox exposes only read-only catalog entries marked `-- @kiwi: allowed` with `risk: read` (`services/api/harness_api/copilot/toolbox.py:200-205`). No arbitrary SQL tool exists.
    - Tool results are wrapped in `BEGIN UNTRUSTED TOOL_RESULT` markers and capped in rows and bytes.
    - Applying a proposal changes editor text only; it never compiles, runs, or commits code (`services/api/harness_api/copilot/service.py:7-13`).

---

## 5. Not Reviewed

The following components and areas were outside the scope of this review or explicitly excluded:
- Third-party packages and runtime libraries (e.g. underlying security of python-oracledb, python-jose, httpx, FastAPI, Starlette, node modules).
- Docker daemon host configuration, OS kernel hardening, and network firewall rules outside the repository.
- Real Oracle database instances, listener configurations, Oracle Native Network Encryption (NNE), and TNS configurations (not connected to live databases).
- Physical deployment secrets, live certificates, or infrastructure outside the `/home/kiwi/OracleDBHarness-A7` repository tree.
- Other reviewers' security review documents (`docs/security-review-*`) were excluded to maintain independent review integrity.

---

## 6. Verdict and Recommendation

**Verdict:** `No blocking findings for a test-stand-in pilot`

No critical or high-severity vulnerabilities were discovered. The design and implementation demonstrate a strong security posture with defense-in-depth principles applied across authentication, authorization, database isolation, secret containment, and crash recovery.

For a production deployment following the pilot, the following recommendations should be addressed:
1. Implement standard HTTP security headers (CSP, X-Frame-Options, X-Content-Type-Options) in `deploy/nginx.conf` (SEC-AGY-001).
2. Enforce `https://` schemes for OIDC issuer and JWKS URLs in `config.py` (SEC-AGY-002).
3. Update `Dockerfile.web` to drop root privileges (SEC-AGY-005).
4. Restrict `provider="env"` secret references to development environments (SEC-AGY-006).
