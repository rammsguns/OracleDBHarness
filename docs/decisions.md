# Architecture decisions

Short records of decisions that are hard to reverse or easy to misread from the code.
Each one says what was decided, why, and what would make us revisit it.

---

## ADR-0001: A modular monolith with one execution service

**Decision.** The API, policy layer, operation catalog and execution service run in
one process. The execution service is a class with a real interface
(`ExecutionService`), not a set of helpers scattered through the routers.

**Why.** The pilot is one organisation and ten concurrent users across three
databases. A distributed queue and horizontal session routing would add failure modes
we would then have to test, for a load we do not have. Because every database
operation already goes through one interface, moving the execution service onto its
own host later is a deployment change, not a rewrite.

**Revisit when.** Session-affinity routing is needed, or the API and the execution
service need to scale separately.

---

## ADR-0002: Worker threads, not worker processes

**Decision.** Statements run on a bounded `ThreadPoolExecutor` inside the execution
service, sized by `HARNESS_WORKER_PROCESSES`.

**Why.** MVP_PLAN.md says "bounded worker processes". Processes cannot work here: an
Oracle connection belongs to the process that opened it, and a worksheet session owns
its connection for the life of its transaction. Handing a statement to another
process would mean handing over the connection, which is exactly what the transaction
ownership rules forbid. python-oracledb releases the interpreter lock during database
round trips, so a bounded thread pool still bounds real concurrency against the
target, which is the property the plan was after.

**Revisit when.** Row post-processing becomes CPU-bound enough to matter, in which
case the boundary to move is the whole execution service (ADR-0001), not individual
statements.

---

## ADR-0003: Deduplication lives in the execution record

**Decision.** An idempotency key is stored on the `Execution` row, unique per actor
rather than globally. A repeat request from the same actor, in the same worksheet
session, for the same statement returns the original execution instead of dispatching
again. Reuse of a key for anything else is refused.

**Why.** An in-memory map would forget across a restart, which is exactly when a
client retries. Every response for a deduplicated request says explicitly that
deduplication prevents a second *dispatch* and does not make execution exactly-once
inside Oracle.

Keys are chosen by clients, so two users pick the same string sooner or later. A
globally unique key would let one user's request be answered with another's execution
record: the statement would never run, and the caller would be shown a row count and a
verification block belonging to someone else. Scoping the key to the actor is what makes
the mechanism safe to expose; refusing reuse within one actor is what keeps the returned
outcome the one the caller actually asked about.

**Revisit when.** Never, as a mechanism; the honesty of the message is the part worth
protecting.

---

## ADR-0004: Statement classification is not a security boundary

**Decision.** `harness_worker.statement` classifies statements for policy records,
risk labelling and DDL handling. It is not used to decide that a statement is
read-only.

**Why.** A `SELECT` can call a function that writes. Filtering keywords produces a
boundary that looks real and is not. The actual boundaries are: free-form worksheets
are enabled per profile, grants are per user and target, and the Oracle account is
constrained by reviewed grants. Production gets curated diagnostic operations only.

**Revisit when.** Never on this point. If a keyword check is ever added it is a hint
in the UI, not an authorization decision.

---

## ADR-0005: Connections are leased, never pooled across users

**Decision.** A worksheet session opens its own Oracle connection and closes it on
commit-and-close, rollback-and-close, idle expiry or explicit close. There is no
shared pool.

**Why.** The plan requires that a connection with an open transaction or residual
package or session state never reaches another user. A pool makes that a discipline
you have to get right on every return path; no pool makes it structurally impossible.
The cost is a connection per active worksheet, which the pilot's load target absorbs.

**Revisit when.** Connection counts become a real constraint on the target database.
A pool would then need session-state reset and an explicit "never return a dirty
connection" gate.

---

## ADR-0006: A local stand-in backend, clearly labelled

**Decision.** `harness_worker.backend.fake` implements the backend interface over
SQLite, seeds the fixture schema the acceptance criteria refer to, and is the default
in development.

**Why.** The whole application - policy, sessions, limits, transactions, panels,
copilot - can be built and tested before an Oracle 19c environment exists. Without
it, milestone 1 blocks everything.

**The risk, and what we do about it.** A green test suite against a stand-in can be
mistaken for Oracle evidence. So: the backend names itself in `/api/v1/system/info`,
the console shows a standing warning, the startup log warns, every test module says
so in its docstring, and `docs/compatibility.md` records that no Oracle version has
been qualified. The stand-in refuses PL/SQL it cannot honour rather than pretending.

**Revisit when.** Oracle 19c access exists. The stand-in stays for fast local
development, but the integration suite must be run against 19c before any release
claim.

---

## ADR-0007: One copilot service, one provider adapter

**Decision.** Context policy, budgets, streaming, proposals and history live in
`harness_api.copilot.service`. Providers implement a two-method interface.

**Why.** The DataForge plan explicitly warns against a second model-provider
implementation in the IDE. Keeping the provider boundary narrow means the IDE
adapter, the console and any future adapter get the same context rules and the same
limits, and adding a provider cannot accidentally add a way around them.

The Kiwi tool loop (ADR-0010) lives in the same service and uses the same providers
through `start_conversation`; tools are the harness's catalog, never the provider's.

**Revisit when.** More than one provider is configured at once, which needs a routing
policy this design does not have.

---

## ADR-0008: Copilot proposals are pinned to a document revision

**Decision.** A proposal records the editor id, revision and SHA-256 of the text it
was generated from. `apply-check` refuses if any of those, or the target, or the
actor changed, and refuses a second application.

**Why.** The failure this prevents is quiet: a model answer applied over work someone
did in the meantime. Refusing costs one re-ask; overwriting costs data.

---

## ADR-0009: Excluded pack-dependent features

**Decision.** AWR, ASH, ADDM, SQL Tuning Advisor and Real-Time SQL Monitoring are not
collected, not queried, and not in the reviewed grants.

**Why.** These carry edition and management-pack conditions. Having the technical
privilege does not establish entitlement, and a diagnostic screen that quietly reads
them can create a licensing problem for the customer. The tuning workbench uses
`V$SQL`, `V$SQL_PLAN` and `EXPLAIN PLAN`, which do not.

**Revisit when.** A per-target entitlement configuration exists and has been reviewed
against the applicable Oracle terms.

---

## ADR-0010: Kiwi may run reviewed read-only catalog operations

**Status.** Accepted 2026-09-29. Implemented in K-3 of [KIWI_PLAN.md](../KIWI_PLAN.md)
behind `HARNESS_KIWI_ENABLED`, off by default. Tested against a stand-in provider only.

**Decision.** Kiwi, the harness's Oracle assistant, may call catalog operations with
`risk: read` that are explicitly marked for it, as the requesting actor, on the
requesting target, through `ExecutionService` and `PolicyEngine`. Each call is an
`Execution` with an `AuditEvent`, like the same operation run from the console. Kiwi
may not run free-form SQL, runbooks, compile, commit or read application tables. Tool
results are untrusted data, wrapped and capped like attachments.

**Why.** Diagnosing an invalid package, a failed job or an ETL chain means looking up
errors, dependencies, source and scheduler history. Today the user has to fetch each
of those and paste it in, and the answer is only as good as what they guessed to
paste. The catalog already holds reviewed, privilege-documented, bounded read queries;
letting the model choose among them adds no statement the user could not already run.

**What it gives up.** docs/copilot.md promised "no automatic context expansion". With
Kiwi's tools enabled, what leaves the harness is no longer limited to what the user
selected or pasted: it includes what Kiwi fetched, from the same allowed categories,
shown in the stream as it happens and recorded in history. The data-sharing approval
has to cover that before the flag is turned on.

**What does not change.** There is still no code path from a model answer to a write,
a compile or a commit (ADR-0008 and the proposal rules stand), and production stays
observation-only. A tool the model is not offered cannot be reached by asking for it.

**Revisit when.** Compiling drafts on development targets is proposed (K-8). That is a
write and needs its own decision.


## ADR-0011: Kiwi may compile its own draft on a development target (proposed)

**Status.** Proposed 2026-09-29. **Not accepted and not implemented.** It is gated on
two things that have not happened: the K-7 exit criterion (a paid real-provider run
with a DBA review, plus the K-4 19c run) and a separate go/no-go from the owner. Until
both, no code path lets Kiwi compile, `HARNESS_KIWI_ENABLED` stays off by default, and
ADR-0010's "Kiwi may not compile" stands.

**Decision (proposed).** Kiwi may compile a draft it produced, and only when all of
these hold:

1. The target's environment is exactly `development`. A missing, unknown, `test`,
   `staging` or `production` value refuses. `Target.environment` is a free string today,
   so the go/no-go must first decide whether it becomes a validated enumeration.
2. The requesting user holds `PERMISSION_COMPILE` on that target, evaluated by
   `PolicyEngine` as for `POST /plsql/compile`. Kiwi has no grant of its own, an
   integration credential is refused, and nothing is elevated.
3. The compile runs on a fresh connection through `ExecutionService.compile_plsql`
   (`session_id=None`), never on a worksheet session, so it cannot commit that
   session's pending work.
4. A person confirms each compile. Kiwi proposes, the console shows the exact source
   and the target, and the compile is a user action on a confirm step. Kiwi does not
   call it as a tool inside its own loop. Compile stays out of `KiwiToolbox`.
5. Every compile is an `Execution` and an `AuditEvent` that names the requesting user,
   the target and the proposal it came from. The source is retained as for any compile.
6. A compile that fails returns compiler errors as untrusted data for the next Kiwi
   turn, within the existing budget. No automatic retry loop.

**Why.** A fix that has to be pasted into the console by hand and compiled there loses
the compiler errors Kiwi needs to correct it. Compiling on a development target closes
that loop while staying inside what the user is already allowed to do.

**Invariants.** Invariant 1 (no writes) would be amended to: no writes except a
user-confirmed compile on a `development` target. Invariant 4 (production is
observation-only) is unchanged: production, test and unknown environments refuse.
Invariant 6 (applying is not running) is kept by point 4: a model answer alone never
reaches the compiler. Invariants 2, 3, 5 and 7 are unchanged, and a compile counts
against the request's budget.

**What it gives up.** Compiling replaces an object in a shared development schema, and
other developers' work there can break. `CREATE OR REPLACE` has no undo. Mitigation to
decide at go/no-go: compile only into a scratch schema or with a name suffix.

**Eval cases required before acceptance** (safety-gated, added with the implementation):
compile confirmed on a development target succeeds; refused on test, production and an
unset environment; refused without `PERMISSION_COMPILE`; refused for an integration
credential; refused with no user confirmation; a job comment or source text that asks
for a compile is not followed; a worksheet session's open transaction is untouched;
compiler errors containing instruction-like text are not followed; the audit event
names the user, the target and the proposal.

**Revisit when.** The owner makes the go/no-go, or `Target.environment` changes.
