# Operations

## Reading a failure

Every error the API returns carries a stable `code`. The console and the IDE adapters
branch on it; so should you.

| Code | HTTP | What it means | What to do |
| --- | --- | --- | --- |
| `authentication_required` | 401 | No token, a bad token, or an account that is authenticated but not registered in the harness | Register the account; check the issuer and audience |
| `not_authorized` | 403 | No grant on this target, or someone else's worksheet session | Grant access; the message is deliberately identical for a missing and a foreign session so identifiers cannot be probed |
| `policy_refused` | 403 | A layer refused: grant permission, role, environment, or worksheets disabled | The message names the layer |
| `capability_unavailable` | 409 | A required Oracle view or privilege is missing on this target | Grant it, or accept that the feature stays off; it will not degrade quietly |
| `session_expired` | 409 | Idle expiry, or the session was closed | Open a new one; uncommitted work was rolled back |
| `session_busy` | 409 | A statement is still running in that session, or a cancellation of one is still being delivered | Wait, or cancel it; a break takes one round trip |
| `limit_exceeded` | 413 | Result, response or copilot context limit | Narrow the request |
| `execution_timeout` | 504 | The statement passed its budget and did not stop | The session was discarded |
| `outcome_unknown` | 502 | **A write may or may not have been applied** | Verify in the database. Do not retry blindly |
| `provider_failure` | 502 | The model provider failed | Database workflows are unaffected |
| `runtime_superseded` | 503 | Another execution service has claimed the metadata store, so this one has stopped dispatching | Two API processes are pointed at one store. Stop this one; see *Restarting* |
| `identity_provider_unavailable` | 502 | The OIDC provider's discovery document or signing keys could not be fetched, or its discovery document names a different issuer | Check `HARNESS_OIDC_ISSUER` and `HARNESS_OIDC_JWKS_URL` from inside the API container |
| `oracle_error` | 400 | Oracle raised an error; `oracleCode` carries the ORA/PLS code | As for the ORA code |

`outcome_unknown` is the one that matters most. It appears when a connection breaks
mid-statement, when a statement does not stop after cancellation, and when a commit
loses its connection with the `COMMIT` in flight. The harness will not guess and will
not retry.

A commit is the sharpest case, because Oracle may have made the transaction durable
and lost only the acknowledgement. Three answers are kept apart:

| Answer | Code | Session | What to tell the user |
| --- | --- | --- | --- |
| Committed | 200 | Kept | Nothing further |
| Refused by Oracle | 400 `oracle_error` | Kept, transaction still open | The work is still pending; commit again or roll back |
| Connection lost mid-commit | 502 `outcome_unknown` | **Retired** | Verify the affected rows in the database before running anything again |

The retired session is dropped from the registry so nothing can commit it a second
time, and the audit event for that commit is written with outcome `outcome_unknown`
and `verificationRequired: true` in its detail. Search the audit trail for those to
find every commit that needs checking after an incident.

## Health and configuration

- `GET /healthz` - liveness. It never opens an Oracle connection, so it stays useful
  when a database is down.
- `GET /api/v1/system/info` - version, identity mode, backend, catalog size, and the
  list of configuration warnings this deployment currently has. Check it after every
  deployment; it is faster than reading the environment.

## Sessions

Idle worksheet sessions are reaped every 15 seconds against a five-minute default
timeout. Expiry rolls back, never commits: abandoned work is not assumed to be wanted.

`GET /api/v1/worksheets` shows your own live sessions with their transaction state and
expiry. A session whose connection broke is discarded rather than reused.

Three other things retire a session before the user asks:

- A statement that does not stop within five seconds of a delivered break. The session
  is dropped from the registry immediately, because a worker thread is still inside the
  driver call and the transaction on that connection must never be committed by a later
  request. The connection is closed once the statement finally returns; until then it
  counts against the target, so a run of these is worth investigating on the database
  side. The response for the statement itself is `outcome_unknown` for a write. The
  same rule applies to the one-shot connections diagnostics, compilation and runbooks
  open: nothing rolls back or closes them while a worker still owns them.
- A commit that lost its connection, as above.
- Revoking a grant, or narrowing it so the worksheet permission is gone. Sessions the
  user held on that target are closed and their uncommitted work rolled back, rather
  than being left able to commit. A commit re-checks target access before it runs, so
  the same applies to a session the API only learns about at commit time.

## History and audit

- `GET /api/v1/executions` - your own executions, with state, policy decision, row
  counts, timings and verification evidence.
- `GET /api/v1/audit` - Administrator only. Append-only: nothing in the application
  updates or deletes an audit event.

Statements are recorded by SHA-256 fingerprint, with literals removed. Raw SQL is kept
only for reviewed catalog operations, whose text is fixed and reviewed. Worksheet SQL
is not retained, because it can contain data a user pasted in. Result rows and bind
values are never stored.

Decide retention and backup for `audit_events` during the pilot. Nothing here rotates
it for you.

## What to watch

- **Executions ending `outcome_unknown`.** Any is worth a look; a pattern means the
  network or the target is unstable. After a restart, work through
  `GET /api/v1/admin/reconciliation` rather than the raw list.
- **`recovery:` log lines at startup.** A restart that resolved nothing says so. One that
  resolved writes names how many need verification.
- **Panels reporting `capability_unavailable`.** Usually a grant that was revoked.
- **Sessions closed with reason `statement did not stop after cancellation`.** The
  target is under pressure, or a statement is genuinely stuck.
- **Copilot `provider_failure` rates.** Users should still be able to work; if they
  cannot, something is coupling the copilot to a database path that should be
  independent.

## Measuring capacity

The load procedure for the pilot target - ten users, three databases, steady, saturation
and recovery phases, and contamination checks - is in [capacity.md](capacity.md). Run it
against a disposable load deployment, never the pilot or a production target.

## Restarting

Startup reconciles the previous process's interrupted work before it serves a single
request. You do not run anything; you read what it decided and follow up on the one case
it cannot settle for you.

Execution intent is persisted before dispatch, and the moment of dispatch is persisted
too. That second marker is what lets a restart tell two situations apart instead of
treating both as unknown:

| State the process died in | What the store holds | Resolved to | Why |
| --- | --- | --- | --- |
| Before dispatch | `queued`, no `dispatched_at` | `cancelled` | No connection was ever asked to run it. Nothing was applied |
| During a read | `running`, `risk_class` `read` | `failed` | A read changes nothing, so there is nothing to verify or undo |
| During DML or PL/SQL | `running`, a write risk class | `outcome_unknown` | Oracle may have applied it. **Never retried** |
| During a commit | Session record with `commit_requested_at` set | Session closed, `outcome_unknown` audit event | The transaction may be durable |
| Written by a build before schema version 4 | No `owner_id`, no `dispatched_at` | Writes to `outcome_unknown` | That build did not mark dispatch, so nothing can be established from the store |

Nothing is ever redispatched. A write whose fate is unknown is recorded as unknown and
left to a person, because retrying a write that may already have been applied applies it
twice.

Worksheet sessions do not survive a restart. Their connections are closed by the database
when the process goes away, which rolls back uncommitted work, and their records are
closed with reason `process restart`.

### After a restart

1. `GET /api/v1/admin/reconciliation` - what this startup resolved, and every execution
   still waiting for someone to look in the database. It carries the procedure with it.
2. For each entry in `outstanding`, check in the database whether that change is present.
   Query the affected rows directly. **Do not rerun the statement to find out.** Check
   only once the dead process's session has gone from `V$SESSION` (module
   `OracleDBHarness`): until the database has cleaned it up, its transaction is still in
   progress and a read shows the state before it. Expect a session that was running or
   waiting on a lock when the process died to stay until that statement ends. (This
   project has not yet observed that on Oracle; `tests/integration/test_process_death.py`
   records it when run against a target.)
3. Record what you found:
   `POST /api/v1/admin/executions/{executionId}/verification` with
   `{"finding": "applied" | "not_applied" | "unresolved", "note": "..."}`.
   That takes it off the outstanding list and writes a `system.restart.verified` audit
   event. The execution keeps its `outcome_unknown` state: the harness never observed the
   outcome, and your later check is a different kind of fact from one it saw itself.
4. Only after a `not_applied` finding should anyone run the statement again, and it is
   the original actor who reruns it, as a new request.
5. Work through `outstandingCommits` the same way. A commit has no execution record and no
   statement of its own -- it made a whole transaction durable or it did not -- so find that
   session's `worksheet.execute` audit events to see what the transaction contained, check
   whether those changes are present (all of them, or none; a commit is not partial), and
   record the finding with
   `POST /api/v1/admin/worksheets/{sessionId}/commit-verification`. The session cannot be
   reopened, so uncommitted work has to be redone from a new one.

The outstanding list is not scoped to the current process. An interrupted write nobody
has verified is still outstanding several restarts later, which is the point.

In the audit trail, a restart leaves `system.restart.reconciled` (one summary per
startup), `system.restart.execution` (one per resolved record, with `redispatched: false`)
and `system.restart.worksheet` (one per closed session). Those survive the next restart;
the report from the endpoint does not.

### One execution service per store

The execution service owns the worksheet connections, so one deployment runs one of them.
That is enforced rather than assumed. A starting process claims the metadata store,
serialized on a single row so that simultaneous startups cannot both win, and supersedes
any earlier claim. A second process started against a live store therefore fences the first
rather than racing it, and logs `was still heartbeating when this process claimed the
metadata store`. If you see that line, two API processes are pointed at one metadata store:
stop one.

A superseded process stops in two stages. Anything that could change the database durably
-- a commit, a write, opening a new worksheet session -- checks the store itself and is
refused with `runtime_superseded` (503) straight away, because the gap before the next
heartbeat is exactly when a commit would land behind a record saying its outcome was
unknown. Reads are not checked against the store, since a read changes nothing; but once a
refusal has happened, or at the latest at the next heartbeat, the process gives up its
sessions and stops serving entirely. A rollback and a cancellation stay allowed throughout:
both only ever remove pending work, and refusing them would leave a user holding a
transaction with no way to discard it.

A statement that was already inside a driver call when its record got reconciled cannot
overwrite that verdict when it finally returns; the late answer is refused and logged.

## Upgrading

1. Back up the metadata store.
2. Read `docs/compatibility.md` for anything newly qualified.
3. Deploy. At startup the API brings the store to the schema version it expects,
   or refuses to start and says why.
4. Check `GET /api/v1/system/info` for new warnings.

`initialize_schema` in `services/api/harness_api/db.py` reads the recorded version and:

| Store | What happens |
| --- | --- |
| Empty | Tables are created and the current version recorded |
| At the expected version | Missing tables are created; existing ones are never altered |
| Older, with a registered path | Every step runs in one transaction, then the version is recorded. A step that fails rolls the whole upgrade back |
| Older, no path | Startup is refused before anything changes |
| Newer than the build | Startup is refused. A store is never downgraded; run the build that upgraded it |
| Tables present but no version | Startup is refused: there is no telling which build created them |

After any of the accepted cases, startup also refuses a store that is missing a column
the models expect, naming it. That is what a hand-edited store, or one an unreleased
build touched, looks like; it would otherwise start and then fail mid-request.

The registered steps:

| From | To | Change | Stores |
| --- | --- | --- | --- |
| 1 | 2 | `executions.dedup_key` is unique per `(user_id, dedup_key)` rather than globally, so one user's idempotency key cannot collide with another's | PostgreSQL. SQLite cannot swap a table constraint in place; remove a version 1 development store and let the API create a new one |
| 2 | 3 | Adds `executions.request_digest` for exact idempotency checks | PostgreSQL and SQLite |

To test an upgrade against PostgreSQL, point `HARNESS_TEST_POSTGRES_URL` at an empty,
disposable database and run
`uv run --with "psycopg[binary]" pytest tests/unit/test_metadata_schema.py`. The
PostgreSQL case is skipped without it.

Version 3's digest covers prepared SQL (including literals), named bind values and types,
and effective execution limits. Bind ordering does not matter. Raw SQL and bind
values are not retained by this mechanism. Reusing a key with different inputs is
rejected. Old executions have no digest and cannot be verified as identical retries;
their keys are rejected too. Verify their outcome before submitting a new request.

## Secret and host scan

Run `bash scripts/secret_scan.sh` from the repository to check tracked working-tree
files, or pass tracked file paths to check a selected set (for example,
`bash scripts/secret_scan.sh .env.example docs/operations.md`). This requires Bash,
Git and Python 3; it does not inspect the index contents. Untracked files, symlinks,
`OrcaServer/` and `databases/` are excluded. Output contains only
`file:line:rule`; exit codes are 0 for clean, 1 for findings and 2 for invalid
usage or allowlist configuration. Rules check private keys, credential assignments,
Oracle connection/wallet references, cloud hosts, IPv4 addresses, home paths and
emails, with placeholder and documentation-address exceptions. This heuristic scan
requires review and does not prove the absence of secrets.

Reviewed false positives can use `scripts/secret_scan.allow`: put an explanatory
comment immediately before each `repository-relative-path:rule:Python-regex` entry.
The regex suppresses only that rule on matching lines in that exact tracked file;
other rules still run. Historical username exceptions apply only to `home-path`.
Reserved example/internal/test/invalid domains, complete placeholder values,
secret reference/file keys, source expressions and reviewed synthetic literals
are distinguished from literal credentials. Package lock metadata is scanned
without treating integrity/resolved fields as credential assignments. CI fixture
connections to localhost and action references are recognized. Regression tests
plant plausible credentials and hosts alongside these exceptions.
Real secrets or host identifiers require a STOP report with locations only,
without copying values or rewriting history.

## Production boundary

Stage 1 profiles whose `environment` is exactly `production` are observation only.
Copilot and Kiwi default off in `config.py`; even if enabled globally for development,
a known production binding is refused before provider work, budget consumption or
Oracle lookup. Refusals use the existing HTTP 403 `policy_refused` error; copilot
keeps its HTTP 200 SSE transport and emits only an `error` event. Earlier grant,
role and request-validation failures retain their existing errors.

Production read authorization also requires a successful target probe reporting
Oracle Database major version **19**. Unprobed, empty/unparseable and non-19
identities (including 23ai and 26ai) are refused with `policy_refused`. Before a
production re-probe, the cached identity is cleared durably: a failed connection,
identity lookup or timeout cannot retain an earlier 19c authorization. Probe
failures retain their existing error shape. Development/test version behavior is
unchanged. A numeric version from `product_component_version` or the driver's
`connection.version` is authoritative; banner wording, edition and CDB/PDB fields
are descriptive and do not substitute for the numeric version. The fake-backend
and driver-stand-in regressions are in `tests/unit/test_production_identity.py`.

The P1 audit found that production copilot/Kiwi bindings were accepted, console
controls remained available, and worksheet policy depended solely on stored flags.
The coordinator approved the minimal server and console fixes on 2026-10-22.
Production worksheet permission, compile permission and session/persistent/admin
write risks now fail closed regardless of stored enablement flags. No protocol
contract changed.

| Path | Enforced where | Regression evidence |
| --- | --- | --- |
| `POST /api/v1/admin/targets` with either unsafe flag | `routers/admin.py:create_target`: production flags rejected before insert | `tests/unit/test_production_boundary.py:test_production_create_refuses_mutating_flags` (both flags) |
| Target PUT/PATCH | No update route is registered; target-specific PUT/PATCH returns 404/405 | `test_profile_update_has_no_route` (both methods; stored flags unchanged) |
| `POST /api/v1/admin/grants` (create or replace) | `routers/admin.py:create_grant` rejects worksheet grants when disabled; compile/runbook grants confer no production execution authority because policy rechecks environment | `test_production_worksheet_grant_refused`; compile and mutating-runbook direct-request tests use fully authorized grants |
| Manifest import, including `targets.json` | `scripts/bootstrap_targets.py:validate_manifest` rejects either unsafe production flag before storage or probing; `bootstrap` rejects stored profile drift and issues only read grants on production | `test_bootstrap_refuses_unsafe_production_before_storage_or_probe` (both flags); existing `tests/unit/test_bootstrap_targets.py:test_invalid_and_unsafe_manifest`, `test_idempotent_grants_and_drift` |
| `POST /api/v1/worksheets` | `execution.py:open_worksheet` calls `policy.py:_check_environment`; production worksheet permission is refused even if stored flag is true | `test_production_worksheet_open_is_refused` (normal and drifted flags); existing `tests/integration/test_targets_and_access.py:test_production_is_observation_only` |
| Existing worksheet `/execute`: query, DML, DDL, anonymous PL/SQL | `routers/worksheet.py:execute` reloads profile/grant; `execution.py:run_worksheet_statement` reauthorizes before execution records or backend execution; production worksheet permission and write risks refused | `test_existing_session_cannot_write_after_becoming_production` (query, DML, DDL, PL/SQL) |
| Existing worksheet `/commit` | `execution.py:commit_worksheet` reloads and reauthorizes with persistent-write risk before commit intent or backend commit | Same test (commit case); backend execute/commit spies remain uncalled |
| Transaction SQL in `/execute` | `execution.py:_risk_for_kind` classifies transaction-control SQL as persistent-write; production worksheet/write policy refuses it before execution | `test_existing_session_cannot_write_after_becoming_production` (SQL COMMIT and explicit commit endpoint); existing `tests/unit/test_statement.py` classification checks |
| `POST /api/v1/plsql/compile` | `execution.py:compile_plsql` authorizes compile/persistent-write before execution record/connection; production check in policy is independent of flags | `test_production_compile_is_refused`; `test_production_policy_refuses_session_write_and_compile_independent_of_flags` |
| Session-write policy independent of permission | `policy.py:_check_environment` refuses production `SESSION_WRITE` alongside persistent/admin writes | `test_production_policy_refuses_session_write_and_compile_independent_of_flags`; worksheet DML/PLSQL direct requests above |
| `POST /api/v1/tuning/explain` | `execution.py:explain_statement` requires worksheet permission before writing session-private PLAN_TABLE rows; production worksheet gate applies | `test_production_explain_is_refused` |
| Mutating runbook `/run`, confirmed or UI bypassed | `runbooks.py:RunbookService._run_mutation` delegates to `execution.py:run_catalog_operation`; policy refuses administrative risk even when stored flag and grant allow it | `test_production_mutating_runbooks_refused` (all current mutating runbooks: recompile and gather stats); existing integration test above |
| Copilot requests with production `profileId`, plain or Kiwi mode | `copilot/service.py:run` calls `_check_production_access` before budget/provider/lookup; refusal audit has no execution ID | `test_production_copilot_refused_before_provider_budget_or_lookup` (both modes); updated `tests/copilot/test_kiwi.py:test_on_production_kiwi_is_refused` |
| Copilot with only a known production reference | Same service gate matches colon-delimited reference components against production profile IDs/names, or exact ID/name references | Same parametrized test (harness and raw ID/name references, both modes) |
| Console worksheet/session writes | `views/WorksheetView.tsx` replaces controls with an observation-only notice for production, independently of flag | `apps/web/src/App.targetSwitch.test.tsx`: production worksheet controls test |
| Console compile/copilot/target switch | `App.tsx` blocks production drawer entry/render and hides global Ask Kiwi; `PlsqlView.tsx` disables compile/Ask Kiwi; schema and tuning views disable source/plan copilot entry | Production tests in `App.targetSwitch.test.tsx` and `views/ProductionBoundary.test.tsx` |
| Console mutating runbooks | `views/RunbooksView.tsx` disables production mutating selection and confirmation, including a prior development preview | Production tests in `views/RunbooksView.test.tsx` |

The new Python tests explicitly use the fake Oracle backend, authorized DBA grants,
backend spies and metadata assertions: policy refusals create exactly one refused
AuditEvent through the central refusal helper, with actor, known profile,
action/permission and request path, and create no execution record. The global
error handler covers direct REST refusals; caught panel/session errors and copilot
SSE errors use the same helper and avoid duplicate rows. Audit storage failures are
logged and preserve the original refusal response. Successful worksheet-open audit
rows from session setup are retained. Copilot tests additionally assert no budget,
request or tool-call record. These are stand-in
regressions, not Oracle compatibility or production qualification evidence.

Runbook catalog/preview, local copilot context preview and proposal apply-check do
not execute Oracle or call the provider; a preview is not execution authorization.
Rollback, cancellation and close remain available to discard pending work in an
existing session. Reviewed production reads remain available to granted users.
There is no profile PATCH/PUT implementation to harden in this release.

`targetReference` is caller-supplied text: only references matching registered
production IDs/names can be identified as production. Unknown external references
and unbound editor-only questions remain allowed when copilot is enabled; the
harness cannot infer the origin of pasted code or an unregistered adapter target.
Do not enable copilot/Kiwi on the production deployment or attach production source
through an unbound/external editor request. This limitation was explicitly accepted
by the coordinator; it does not grant database access or bypass a known production
profile refusal. Oracle 19c identity qualification belongs to P2.
