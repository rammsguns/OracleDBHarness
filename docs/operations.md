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
   Query the affected rows directly. **Do not rerun the statement to find out.**
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
