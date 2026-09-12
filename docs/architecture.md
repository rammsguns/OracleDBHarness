# Architecture

## The shape of it

```
apps/web            React console
  |  same origin, bearer token from the identity provider
services/api        FastAPI: authentication, policy, operation catalog, copilot
  |
services/worker     Execution engine, session leases, Oracle adapter
  |
Oracle              python-oracledb (thin), or the local stand-in in development

integrations/dataforge   IDE adapter: scoped context in, streamed answer out
packages/contracts       OpenAPI + copilot protocol + the shared TypeScript client
oracle/                  Reviewed queries, runbooks and grants
```

The API and the execution service run in one process for the pilot (ADR-0001). The
console and the IDE adapters are peers: both call the same versioned API, and neither
gets a privileged path.

## The operation contract

Everything that reaches Oracle is expressed the same way:

```
operation ID + actor + target + parameters + capability requirements + risk class
  + limits  ->  execution record + structured result + verification evidence
```

Concretely, in `harness_api.execution.ExecutionService`:

1. Load the profile and the caller's grant on it.
2. `PolicyEngine.authorize` - grant permission, application role, environment, target
   capabilities, minimum Oracle version. Each layer is separate and each records why.
3. Persist an `Execution` row **before** dispatch, so interrupted work is visible
   after a restart.
4. Commit the dispatch marker, so a restart can tell work that never reached a
   connection from work that was in flight. See `harness_api.recovery`.
5. Run it through `ExecutionEngine` with narrowed limits.
6. Write the outcome and an append-only `AuditEvent`, unless another process has already
   resolved the record -- a reconciliation verdict is never overwritten by a late answer.

Free-form worksheet SQL and reviewed catalog operations both take this path. They
differ in where the statement came from and what permission it needs, not in what
happens to it.

## Execution and transactions

A worksheet session owns one Oracle connection outright (ADR-0005). Operations inside
one session are serialized; a second concurrent request is refused with
`session_busy` rather than queued behind a statement the user cannot see.

Rules the code enforces, each because Oracle behaves in a way that surprises people:

- **DDL commits.** A worksheet with pending DML refuses DDL rather than committing
  the user's work as a side effect. `CREATE OR REPLACE` of a program unit is DDL too,
  and is refused on the same terms; `/api/v1/plsql/compile` runs it in its own session
  for that reason, where there is no user transaction to commit. The refusal is decided
  twice: once when the request is admitted, and again in the engine under the session
  lock, because another statement in the same session can leave a transaction open
  between the two.
- **Rollback is not a general undo.** PL/SQL can commit on its own. The policy layer
  attaches that caveat to every session-write decision instead of implying otherwise.
- **Cancellation is best effort once a statement is in flight.** The result says
  whether the break was delivered and whether the statement actually stopped. A
  statement that does not stop within the grace period makes the session unusable: it
  leaves the registry before the response does, so no later request can reach the
  connection the statement still holds, and the connection is closed once that
  statement returns.
- **A break holds the session until it lands.** The break is aimed at a connection,
  not at a statement, and the statement being cancelled can finish on its own while
  the request is still being made. The session is therefore held from the moment the
  break is decided on until the driver call returns: a new statement, commit, rollback
  or close in that window is refused with `session_busy`. Left free, the session would
  be leased again and the break would stop the next statement instead -- work nobody
  asked to stop, reported under the execution that had already finished. If the
  statement ends inside that window the break is not sent at all, because nothing else
  can have claimed the connection it would have landed on.
- **A break Oracle acknowledges is a cancellation, not a failure.** Stopping a
  statement means breaking the call, and Oracle reports that back as `ORA-01013`. The
  adapter maps it to `execution_cancelled`, so the outcome is `cancelled`; classified
  as a plain Oracle error it would surface as `failed` and send the user looking for a
  bug in SQL that was only stopped. `ORA-01013` rolls back that statement alone --
  earlier work in the transaction is still pending, and the session stays usable.
- **Cancelling work that is still queued is exact.** The worker pool is bounded, so an
  accepted statement can be waiting for a slot with nothing sent to the database. A
  break request cannot stop what Oracle has never seen, so the cancellation is recorded
  on the session and the dispatch guard refuses to send the statement at all; the
  result reports `statementStarted: false`. Without that, a write cancelled in the
  queue would be applied whenever a slot freed, on a session its owner believes is
  idle. The guard's decision and the cancellation's are taken under the same lock:
  ordering the reads and writes is not enough, because a check-then-set on one side
  and a set-then-check on the other can both miss, and `statementStarted: false` would
  then be reported for a write already on its way to the database.
- **Unknown is a state.** A write whose connection broke mid-flight is reported as
  `outcome_unknown`, never as failure, and never retried automatically. That includes
  a one-shot statement whose autocommit was in flight: the execution record keeps the
  uncertainty rather than flattening it into `failed`.
- **A connection in use is nobody else's to take.** Closing a session takes the same
  exclusive hold as commit and rollback, so a close while a statement is running is
  refused with `session_busy` instead of pulling the connection out from under the
  thread inside the driver call. For the same reason the idle sweep skips a session
  with a statement in flight, however long that statement has been running: it is
  busy, not abandoned.

Limits are configured centrally and narrowed per request, never widened: 1,000 rows,
10 MB, a 30-second total operation deadline enforced above the driver round-trip
limit, five-minute idle expiry, bounded DBMS_OUTPUT and LOB previews.

## Capability discovery

Nothing is assumed about a target. `POST /api/v1/targets/{id}/test` connects, asks the
database who it is, and runs one minimal probe per capability. Results are stored per
target with the time they were checked.

A missing capability disables exactly the feature that needs it, with a message
naming the view or privilege. A panel that could not be collected is returned with
`available: false` and the error. It is never rendered as an empty, healthy panel -
that is the single most dangerous failure mode for a diagnostic tool.

## Access

Four application roles - Viewer, Developer, DBA, Administrator - and four grant
permissions - `read`, `worksheet`, `compile`, `runbook`. A role says what kind of work
you may do; a grant says on which target. Neither bypasses Oracle privileges, and a
grant may name its own credential reference so one target can be reached with
different Oracle accounts for different roles.

Administrators manage access. They do not inherit it.

Access is re-checked where it matters most, not only when a session opens. Committing a
worksheet transaction re-runs target authorization, and revoking or narrowing a grant
closes the sessions the user already holds on that target, rolling their uncommitted
work back. Otherwise a revocation would take effect for the next request while a live
connection was still able to make an earlier one durable. Revocation is never refused:
a session that is running a statement at that moment leaves the registry immediately,
so nothing can be leased or committed on it again, and its connection is closed as
soon as the statement returns.

The identity provider is authoritative for who you are; the harness record is
authoritative for what you may do. A token claiming a role you do not hold changes
nothing.

## Secrets

A profile points at a `SecretReference`. The value is resolved at connection time from
a mounted file or an environment variable and is never written to the metadata store,
returned by the API, or logged. `/api/v1/admin/secrets` reports whether a reference
resolves, never what it resolves to.

The metadata store's own password follows the same rule. `HARNESS_METADATA_PASSWORD_FILE`
names the file the API reads it from - the same file PostgreSQL reads - so the URL in
the deployment carries no credential and there is one password rather than two places
to keep in step. A file that is named but missing is a configuration error at startup,
not a fallback to whatever the URL happened to hold.

## The copilot

The model never acts. It reads explicitly selected context and proposes; the backend
authorises every real operation, and there is no path from a model answer to a
database call.

- **Context** is selected source, a user-supplied error or plan, the target version,
  and permission-filtered metadata. Result rows, bind values, credentials and wallets
  are refused by category, whatever a caller sends.
- **Untrusted by default.** Source comments, object comments and error text are
  wrapped in labelled delimiters, and the system prompt says they are data. An
  embedded instruction is reported, not followed.
- **Proposals are editor edits.** Applying one changes a buffer. Compiling or running
  the result is a separate, separately authorised action.
- **Staleness is refused.** A proposal is pinned to the editor, revision and content
  hash it came from (ADR-0008).
- **Provider failure is contained.** The copilot returns a typed error; every
  database workflow keeps working.

## IDE adapters

DataForge is the first adapter. Its backend holds a scoped integration credential
(`copilot:assist`), resolves context under its own authorization, and calls the same
copilot API. The harness never receives a database credential, never calls DataForge,
and cannot execute anything against a DataForge connection. An actor reference the
adapter asserts is namespaced by integration instance and is never treated as a role.

See `integrations/dataforge/README.md`.

## What is deliberately absent

- No Kubernetes, no distributed queue, no second execution service (ADR-0001).
- No keyword-based read-only filter (ADR-0004).
- No AWR, ASH, ADDM, advisor or Real-Time SQL Monitoring collection (ADR-0009).
- No automatic index creation, optimizer parameter changes, or accepted tuning
  recommendations.
- No session termination, user provisioning, or arbitrary privileged scripts.
- No automatic retry of a write, ever.
