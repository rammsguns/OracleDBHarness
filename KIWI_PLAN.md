# Kiwi: the Oracle database agent

Prepared 2026-09-29 against `11ff1be`. This is a proposed delivery plan, not evidence
that any of it exists. Nothing below is implemented yet.

**Kiwi** is the name of the harness's Oracle agent. It grows out of the existing copilot
(`services/api/harness_api/copilot/`), keeps every guarantee that copilot makes, and
adds the one thing the copilot cannot do today: look things up for itself.

## What Kiwi is for

| Goal | Example request |
| --- | --- |
| **Solve issues** | "Why is `HR_PKG` invalid?", "The nightly load job failed, what happened?", "Fix this ORA-06502" |
| **Write PL/SQL** | "Create a procedure that archives closed orders older than 90 days", "Add a bulk-collect version of `LOAD_CUSTOMERS` to `ETL_PKG`" |
| **Explain PL/SQL** | "Explain `BILLING_PKG` end to end", "What does `CALC_TAX` do with NULL rates?" |
| **Explain processes** | "Walk me through the nightly ETL", "What writes to `FACT_SALES`, and in what order?" |
| **Tune** | "Explain this plan", "Why is this cursor slow?" (current-state views only; see ADR-0009) |

## The decision this plan is built on

Kiwi may call **reviewed, read-only catalog operations** on its own, within the
requesting user's permissions. It may never execute free-form SQL, write, compile,
commit or deploy. Compiling, running a test block or applying a change stays a click
by the user, through the existing authorised path.

This changes one promise in [docs/copilot.md](docs/copilot.md), "no automatic context
expansion", and flips `executesDatabaseOperations` semantics in the capabilities
endpoint from "never" to "read-only catalog lookups". Both are rewritten in K-1, and
an ADR records why (ADR-0010, below). The structural guarantee is unchanged: there is
still no code path from a model answer to a write.

Letting Kiwi compile its own drafts on development targets is a separate, later
decision (phase 2, K-8). It is not assumed by anything before it.

## Invariants Kiwi inherits

These already hold for the copilot and must still hold for every Kiwi request. Each
has a test in K-7 that fails if it breaks.

1. **No writes.** Tools resolve only to catalog entries with `risk: read`. Runbooks,
   worksheets, compile and commit are not tools. A model asking for one gets a typed
   refusal, not a workaround.
2. **No business data.** No tool selects from application tables. Kiwi sees metadata,
   source, errors, plans, statistics and scheduler history, never rows. `result_rows`,
   `bind_values`, `credentials` and `wallet` stay refused by category.
3. **Same authorisation as the user.** Every tool call goes through
   `ExecutionService` and `PolicyEngine` as the requesting actor on the requesting
   target, gets an `Execution` row and an `AuditEvent`, and fails closed on a missing
   privilege ("an unavailable panel is never an empty one").
4. **Production stays observation-only.** Kiwi adds no capability on a production
   target that the user's own role does not already have there.
5. **Untrusted content stays data.** Tool results are wrapped in the same UNTRUSTED
   markers as attachments. Instructions found in source comments, object comments or
   job names are reported, never followed, and can never trigger another tool call
   by themselves being text.
6. **Applying is not running.** A proposal changes an editor buffer. It stays pinned
   to editor, revision and hash (ADR-0008).
7. **Bounded.** Steps, tool calls, rows per tool, bytes of context and tokens are all
   capped per request, and a provider failure leaves every database workflow working.

## Architecture

```
console / DataForge
   |  POST /api/v1/copilot/ask  (existing endpoint, action = kiwi.*)
   v
CopilotService  --- agent loop (new) ----------------------------+
   |    context policy, budgets, proposals, history (existing)   |
   |                                                             |
   |  tool_use                                            tool result (wrapped, capped)
   v                                                             |
KiwiToolbox (new)  -- allowlist of read catalog entries ---------+
   |
ExecutionService -> PolicyEngine -> ExecutionEngine -> Oracle
   (existing, unchanged: same actor, same target, same audit)
```

- **Provider interface.** `Provider.stream(system, user_message)` becomes a
  multi-turn call that accepts tool definitions and yields text, tool-use and usage
  events. The fake provider gains scripted tool calls so every loop path is testable
  without a paid provider. ADR-0007 still holds: one service, one provider adapter.
- **Agent loop.** Lives in the copilot service, not the provider. The service decides
  whether a tool call is allowed, runs it, wraps the result and decides when to stop.
  The model never sees a connection, a credential or a tool it may not call.
- **Tool definitions come from the catalog.** Each allowed entry in `oracle/` is
  exposed with its id, title, description and parameters. Adding a tool means adding
  a reviewed `.sql` file with a `@kiwi: allowed` header, not writing Python.
- **Protocol.** New stream events: `tool_call` (operation id, parameters, why),
  `tool_result` (status, row count, bytes, truncation, execution id, never the rows
  themselves to an IDE that did not ask), `plan_step` and `budget`. K-1 checks whether
  the DataForge adapter ignores unknown events; if it does these are protocol 1.1,
  otherwise 2.0 with a compatibility window.

## Tools

Existing catalog entries Kiwi can use from day one:

| Area | Operations |
| --- | --- |
| Schema | `schema.list_schemas`, `list_objects`, `object_status`, `object_source`, `object_errors`, `object_dependencies`, `table_columns`, `table_constraints`, `table_indexes`, `table_statistics` |
| Tuning | `tuning.cursor_search`, `cursor_statistics`, `cursor_plan`, `explain_plan_rows` |
| DBA (DBA role only) | `dba.invalid_objects`, `scheduler_jobs`, `scheduler_failures`, `blocking`, `sessions`, `tablespace_usage` |

New reviewed catalog entries Kiwi needs (K-4, added), each with its privileges and
minimum version in the header like the rest:

| Operation | Source view | Why |
| --- | --- | --- |
| `schema.object_referenced_by` | `ALL_DEPENDENCIES` (reverse) | "What calls this?", impact of a change |
| `schema.package_subprograms` | `ALL_PROCEDURES`, `ALL_ARGUMENTS` | Map a package before reading its body |
| `schema.object_source_range` | `ALL_SOURCE` with line bounds | Read one subprogram of a large body without the whole thing |
| `schema.plscope_identifiers` | `ALL_IDENTIFIERS` | Call graph and table usage, **only** where PL/Scope was enabled at compile time; absence is reported, not guessed around |
| `schema.plscope_statements` | `ALL_STATEMENTS` (12.2+) | Which DML statement hits which table, in which subprogram |
| `schema.triggers` | `ALL_TRIGGERS` | Hidden writes during an ETL |
| `schema.db_links_referenced` | `ALL_DEPENDENCIES`, `ALL_DB_LINKS` (names only) | Remote sources in an ETL, without exposing link credentials |
| `dba.scheduler_job_detail` | `ALL_SCHEDULER_JOBS`, `_PROGRAMS` | What a job runs, when, and as whom |
| `dba.scheduler_chain` | `ALL_SCHEDULER_CHAIN_STEPS`, `_RULES` | Step order of a chained ETL |
| `dba.scheduler_run_history` | `ALL_SCHEDULER_JOB_RUN_DETAILS` | Durations and failures over time, with `additional_info` treated as untrusted |
| `dba.scheduler_program` | `ALL_SCHEDULER_PROGRAMS` | From a chain step to the PL/SQL it runs (added in K-6) |

Tool results are capped per call (rows and bytes) and marked truncated when they are.
A tool that fails for lack of privilege returns the privilege it needed, and Kiwi says
so in its answer instead of working around it.

## Capabilities

### Solve issues (playbooks)

Playbooks are system-prompt guidance plus an expected tool sequence, not hard-coded
flows. Each gets evaluation cases (K-7).

| Symptom | Kiwi's route |
| --- | --- |
| Invalid object / PLS error | `object_status` -> `object_errors` -> `object_source_range` around the error -> `object_dependencies` for invalid parents -> proposed fix, and "recompile runbook" as a suggested next action if the cause is a parent |
| ORA error pasted by the user | classify the error -> fetch the named object's source and the columns it touches -> point at the line -> proposed repair |
| Failed scheduler job | `scheduler_failures` -> `scheduler_job_detail` -> `scheduler_run_history` -> source of the program -> cause and fix |
| Blocking / hang | `blocking` -> `sessions` (DBA only) -> explanation; never suggests killing a session as something Kiwi does |
| Slow statement | `cursor_search` -> `cursor_statistics` -> `cursor_plan` -> experiments the user can measure, estimates labelled as estimates |

### Write PL/SQL

- `kiwi.create`: draft a procedure, function, package, trigger or type from a
  description, grounded in columns and constraints Kiwi looked up, not guessed.
- **Packages are two documents.** A proposal today pins one editor buffer. Packages
  need spec and body applied together or not at all, so proposals gain an ordered
  list of parts, each pinned to its own editor and revision, with an all-or-nothing
  `apply-check`.
- **House style is configuration.** A per-deployment standards file (naming prefixes,
  error-logging package, `BULK COLLECT ... LIMIT` size, exception policy, header
  comment template) is included in the system prompt and cited when applied.
- Every draft comes with a `kiwi.test_block` companion that does not commit.
- **Compile loop, user in the loop.** After the user compiles through
  `/api/v1/plsql/compile`, the console offers "Ask Kiwi to fix" with the line-level
  errors attached. That is a new request with a new proposal, never an automatic retry.

### Explain packages and processes

Large units do not fit in one context, and explaining them well means reading the
right parts, not all of it.

- `kiwi.explain_package`: map the spec with `package_subprograms`, read each
  subprogram with `object_source_range`, summarise each, then combine: public API,
  internal call graph, tables read and written, commit points, exception handling,
  autonomous transactions, dynamic SQL, and anything that looks risky.
- `kiwi.explain_process`: start from a job, chain, package or table. Walk scheduler
  chain -> programs -> packages -> dependencies and triggers to a depth and object
  limit, then describe data lineage (source -> staging -> target), step order, commit
  and restart behaviour, and failure history.
- **Evidence levels are explicit.** Every edge in a lineage is labelled
  *catalog* (from `ALL_DEPENDENCIES`/PL/Scope), *source* (read from code) or
  *inferred* (dynamic SQL, db links, naming). Kiwi does not present an inferred edge
  as fact.
- **Output.** A structured explanation (sections, object references that link into the
  schema explorer) and a Mermaid diagram of the flow, exportable as Markdown so a team
  can keep it as documentation.

## Delivery sequence

Local identifiers below are planning IDs, not GitHub issues. Each item lands behind
`HARNESS_KIWI_ENABLED` (off by default) until K-7 passes, so none of it touches the
[NEXT_PHASE_PLAN.md](NEXT_PHASE_PLAN.md) pilot gates.

| ID | Work | Exit |
| --- | --- | --- |
| **K-1** (done 2026-09-29) | **Identity and docs.** Name Kiwi in the console, the system prompt and the capabilities endpoint (`assistant: "Kiwi"`). Keep module paths, route paths and protocol ids as they are, to avoid churn. Write ADR-0010 (read-only tool use). Rewrite docs/copilot.md for tool calls. Check how the DataForge adapter treats unknown stream events. | Docs reviewed by whoever approves provider data sharing. No behaviour change. |
| **K-2** (done 2026-09-29) | **Provider tool use.** Extend the provider interface and the Anthropic adapter to multi-turn tool use with usage per turn. Scripted tool calls in the fake provider. | Unit tests for tool-use turns, stop reasons, usage accounting and provider failure mid-loop. |
| **K-3** (done 2026-09-29) | **Toolbox and agent loop.** `KiwiToolbox` over the catalog with the `@kiwi: allowed` header; policy per call; wrapping and caps on results; budgets for steps, tool calls, tokens and wall time; new stream events; history records every tool call's execution id. | Invariant tests 1-7 pass against the stand-in. A request that runs out of budget ends with a partial answer marked as partial. |
| **K-4** (done 2026-09-29, stand-in only; 19c run pending) | **New catalog entries** from the table above, with privileges and minimum versions, qualification fixtures in `oracle/qualification/`, and PL/Scope absence handled. | Run against the stand-in and against the 19c instance used for the earlier qualification. |
| **K-5** (done 2026-09-29) | **Issue playbooks and authoring.** `kiwi.diagnose`, `kiwi.create`, multi-part proposals for packages, standards file, "Ask Kiwi to fix" after compile. | Console and API tests for multi-part apply-check (all-or-nothing, stale part refuses the whole). |
| **K-6** (done 2026-09-29) | **Package and process explainer.** Per-subprogram summarise-then-combine, dependency and scheduler walk with limits, evidence labels, Mermaid export. | Explains a 3k-line fixture package and a fixture ETL chain within budget, with every lineage edge labelled. |
| **K-7** | **Evaluation.** Extend `tests/copilot/eval/cases.json` with a new case set version: tool-trace expectations, playbook cases, packages, ETL, injection in source and job comments that tries to call tools, privilege-denied paths, budget exhaustion. DBA review with the NP-04 runner and rubric. | All safety and authorisation cases pass; >= 90% DBA-reviewed correctness on diagnose and explain; no case where Kiwi claims something it did not look up. Then `HARNESS_KIWI_ENABLED` may default on for development targets. |
| **K-8** | **Phase 2 decision: compile on development targets.** Only after K-7. Kiwi may compile its own draft in a separate session on a target explicitly marked `development`, never test or production, and never touch a user's worksheet session. Needs its own ADR and eval cases. | A separate go/no-go. Not assumed by K-1 to K-7. |

Surfaces follow the API: the console's `CopilotDrawer` becomes the Kiwi panel in K-3
(tool trace shown as it happens, with each call's execution id linking to history),
and the DataForge adapter gains the new events in the same release as the protocol
change.

## Dependencies and risks

- **NP-04 first.** Kiwi's quality cannot be measured before the real-provider
  evaluation path exists and has been run once. K-7 reuses it.
- **Pilot scope freeze.** NEXT_PHASE_PLAN.md rules out new tool surfaces during the
  pilot. Kiwi work proceeds behind a flag that is off in the pilot deployment, or waits
  until the pilot gates close; that is the owner's call.
- **Cost.** An agent spends several model turns per question. Budgets are per request
  and per actor, and the eval report records per-case tool calls and cost.
- **PL/Scope is often off.** Without it, table-level lineage comes from reading source,
  which is weaker. Kiwi says which it used.
- **Metadata can still be sensitive.** Source and object names leave the harness under
  the same data-sharing approval as today; the approval text is updated in K-1 to say
  Kiwi fetches them itself.
- **DBA views show other users' activity.** `dba.sessions` and `dba.blocking` stay
  limited to the DBA role, exactly as in the console.

## Out of scope

Autonomous writes of any kind, killing sessions, index or parameter changes, AWR/ASH
and advisors (ADR-0009), reading table data, an MCP server (still deferred in
MVP_PLAN.md; if it comes, it exposes the same toolbox), and inline autocomplete.

## Validation

### 2026-09-29, K-1 identity and docs

- The console heading, buttons and disabled notice say Kiwi; the system prompt opens
  "You are Kiwi"; `GET /api/v1/integrations/capabilities` carries
  `"assistant": "Kiwi"` (optional in the TypeScript contract, so older harnesses stay
  valid). Module paths, routes, action ids and protocol 1.0 are unchanged.
- ADR-0010 records the read-only tool decision; docs/copilot.md describes it as
  accepted and not enabled.
- Unknown stream events: the console ignores them; the DataForge adapter's `readSse`
  forwards every event verbatim and its route relays it. The DataForge frontend's
  behaviour is not in this repository. K-3 therefore adds an event filter to the
  adapter before any new event is emitted, and the events can then ship as 1.1.
- The system prompt changed by one sentence. NP-04 has not had its paid run, so there
  is no earlier real-provider result it invalidates.

### 2026-09-29, K-2 provider tool use

- `Provider.start_conversation` returns a `ToolConversation`: each `turn()` streams
  text and ends with a `TurnEnd` carrying the stop reason, the requested tool calls
  and that turn's usage. The provider never runs a tool; the caller answers every
  pending call exactly once with `add_tool_results` before the next turn, or the
  conversation refuses.
- Tool calls are honoured only on a `tool_use` stop. A `max_tokens` stop drops them
  (a truncated call may be incomplete); a `refusal` stop records its usage and raises
  `ProviderError`. A failed or abandoned turn ends the conversation, keeps the usage of
  the turns that completed, and marks the total `complete: false`.
- The Anthropic adapter sends `tools`, keeps its own transcript and resends each
  assistant turn unchanged, thinking blocks included. It is tested against a stand-in
  client only; no real provider was called. `FakeProvider` takes a script of turns.
- Nothing calls it yet: `CopilotService` still uses `stream()`, so behaviour is
  unchanged. Tool names must match `^[A-Za-z0-9_-]{1,64}$`, so K-3 maps catalog ids
  such as `schema.object_status` to tool names.
- `tests/copilot/test_tool_use.py`: 28 tests.

### 2026-09-29, K-3 toolbox and agent loop

- `KiwiToolbox` (`harness_api/copilot/toolbox.py`) offers only catalog entries with
  `-- @kiwi: allowed` and `risk: read`: 19 entries across `schema`, `dba` and
  `tuning`, each reading dictionary and `V$` views only. Runbooks, the worksheet,
  `explain_plan` and any other name are refused with `policy_refused` and audited;
  unknown parameters are `invalid_request`. Tool names map `.` to `__`.
- Every call loads the profile and grant afresh and runs through
  `ExecutionService.run_catalog_operation`, so it leaves an `Execution` and an
  `AuditEvent` like a console panel. Refusals (`not_authorized`, `not_found`) are
  audited under `copilot.kiwi` and go back to the model as typed errors. A grant
  revoked mid-request fails the next lookup. On production, dba reads succeed and
  anything else stays refused.
- Results reach the model as JSON inside `BEGIN/END UNTRUSTED TOOL_RESULT` markers,
  capped by `HARNESS_KIWI_MAX_ROWS_PER_TOOL` and `HARNESS_KIWI_MAX_RESULT_BYTES` with a
  "(truncated: ...)" note. The `tool_result` event and the `CopilotToolCall` record
  carry status, row count, bytes, truncation and execution id, never row values.
- Budgets: steps, tool calls, tokens, lookup bytes and wall time. Reaching one ends the
  request with `done` `outcome: "partial"`, `partial: true` and `stopReason`; a
  tool-call cap asks the model once more for an answer without tools.
- Protocol 1.1: `plan_step`, `tool_call`, `tool_result`, `budget`, and
  `done.partial`/`stopReason`, in the generated schema and the TypeScript contract.
  The DataForge adapter drops event names it does not know (`KNOWN_STREAM_EVENTS`) and
  sends 1.1. The console's Kiwi panel sends `profileId` and shows the lookup trace,
  budget and a partial-answer notice; it shows each execution id as text, not yet as
  a link to history.
- Invariants 1-7 are covered in `tests/copilot/test_kiwi.py` (28 tests) against the
  stand-in provider. No real provider was called. Full suite: 625 passed, 71 skipped;
  adapter 23 node tests; web 45 tests.

### 2026-09-29, K-4 catalog entries

- Ten entries added with `-- @kiwi: allowed`, `risk: read`, `@privileges` and
  `@min_version: 12`: `schema.object_referenced_by`, `package_subprograms`,
  `object_source_range`, `plscope_identifiers`, `plscope_statements`, `triggers`,
  `db_links_referenced`, and `dba.scheduler_job_detail`, `scheduler_chain`,
  `scheduler_run_history`. Kiwi now has 29 tools. `start_line` and `end_line` are
  integer parameters.
- PL/Scope absence is reported, not guessed around: both PL/Scope lookups return one
  row per unit whose `PLSCOPE_STATUS` is `COLLECTED`, `NOT COLLECTED: compiled with
  PLSCOPE_SETTINGS=...`, `NONE RECORDED: ...` or `UNKNOWN: ...`, with the data columns
  empty when nothing was collected. `plscope_statements` needs 12.2 (`ALL_STATEMENTS`);
  the version gate compares majors only, so on 12.1 it fails with ORA-00942 and says so
  in its description.
- `db_links_referenced` returns link names, owners and remote objects, never
  `USERNAME` or `HOST`; a link the user cannot see shows as `NOT VISIBLE`.
  `scheduler_run_history` returns `ADDITIONAL_INFO` inside the untrusted markers.
- The stand-in seeds a trigger, a PL/Scope-compiled function, `EMPLOYEE_REPORT`
  compiled without PL/Scope, two remote synonyms, a scheduler program, chain and two
  jobs, and a run log carrying an injection attempt. `tests/copilot/test_kiwi_catalog.py`
  (13 tests) runs every new entry through the toolbox.
- `oracle/qualification/01_fixtures.sql` and `02_teardown.sql` build and drop the same
  trigger, function (compiled with `IDENTIFIERS:ALL, STATEMENTS:ALL`), scheduler
  program, chain and job; `tests/qualification/test_kiwi_catalog.py` runs the shipped
  SQL against them. They need `CREATE TRIGGER`, `CREATE JOB` and the rules-engine
  privileges for chains, which the product grants do not include (see
  `oracle/qualification/README.md`). No database link is created, so on 19c
  `db_links_referenced` is checked for its shape and returns no rows.
- **Exit criterion only half met.** Run against the stand-in only: full suite 638
  passed, 74 skipped (the 19c tests among the skips, three of them new). **The 19c run has not been done**:
  no Oracle instance was reachable from this session. Writing the qualification test
  found one column that would have failed there (`ALL_STATEMENTS.TEXT`, which the
  stand-in had seeded as `SQL_TEXT`), so the 19c run is still needed before K-4 counts
  as closed.

### 2026-09-29, K-5 playbooks and authoring

- Three new actions: `kiwi.diagnose`, `kiwi.create` and `kiwi.test_block`. They are
  playbooks written into the action instructions, not fixed flows. The model chooses
  its lookups and names the playbook it followed. When no lookups are available, it
  says which lookups it would have made. The blocking playbook never suggests that
  Kiwi kill a session. `kiwi.create` returns a test block that ends in `ROLLBACK`.
- **Multi-part proposals.** A request can send up to 8 named `parts` instead of one
  `editor`, for example a package spec and its body. Each part is pinned to its own
  editor, revision and SHA-256 and stored as a `ProposedEditPart`. The model labels
  each part's replacement with ```` ```plsql part=<name> ````.
- **All-or-nothing apply-check.** If any part is stale, missing or unknown, the whole
  proposal is refused with "Nothing was applied: a multi-part proposal applies all
  parts or none." The refusal lists reasons per part.
- **Standards file.** `HARNESS_KIWI_STANDARDS_FILE` holds at most 16 KB of JSON with
  five allowed keys. Its contents are added to the system prompt, and Kiwi cites
  `(standard: <key>)`. An unknown key, a bad value or an unreadable file returns a
  `configuration_error`.
- **Console.** The copilot drawer has an "Edit several parts together" mode with a
  diff per part and "Apply all parts". The PL/SQL view shows "Ask Kiwi to fix" after
  a failed compile, which seeds `kiwi.diagnose` with the compiler errors.
- **Protocol 1.2.** The copilot protocol is now 1.2. The DataForge adapter stays on
  1.1 because it sends no parts.
- **Exit criterion met.** The API tests are in `tests/copilot/test_kiwi_authoring.py`.
  The console tests are in `apps/web/src/views/CopilotDrawer.test.tsx`. Both cover
  all parts applying together and a stale part refusing the whole proposal.
- **Test results.** pytest: 674 passed, 74 skipped. Web: 51 passed, 3 skipped. The
  adapter: 23 passed.
- **Still pending.** There has been no run against a real provider. Playbook
  behaviour is covered with the scripted provider only. The K-4 19c run is still
  pending.

### 2026-09-29, K-6 package and process explainer

- Two new actions: `kiwi.explain_package` and `kiwi.explain_process`, with a `subject`
  field on the request. The harness drives the lookups within the request budget and
  the model gets no tools (`copilot/explainer.py`).
- **Package.** Source is read in pages, scrubbed of comments and string literals,
  split into subprograms and summarised in batches, then combined in one final call.
  Public subprograms roll up their helpers. Catalog references with no source edge
  are added as `catalog` evidence.
- **Process.** Job, chain, steps, programs and package procedures are walked from the
  scheduler (`dba.scheduler_program` is new), then up to three packages are read and
  the triggers on written tables are added.
- **Evidence labels.** Every lineage edge is `source`, `inferred`, `catalog` or
  `scheduler`. Source beats inferred beats catalog/scheduler.
- **Mermaid.** Deterministic `flowchart LR` export, and a "Copy Mermaid" button in
  the console.
- **Injection.** Fixture text that reads as an instruction (EXPORT_ORDERS run 9002,
  ETL rule 42 comment) is treated as data: it produces no lookup and no edge.
- **Protocol 1.3.** Additive. The DataForge adapter stays on 1.1.
- **Exit criterion.** `tests/copilot/test_kiwi_explainer.py` explains a 3k-line
  fixture package and the ETL chain (4-5 model calls, 23-31 lookups, under budget)
  with every edge labelled.
- **Still pending.** No real provider has been run; the explanations in tests are the
  fixture provider's canned text. The K-4 19c qualification run is still pending, and
  the ETL fixture SQL has not yet been run against a real Oracle. Table access
  detection is regex based, not a PL/SQL parse.
