# Kiwi: what is shared, and what the model can do

Kiwi is the name users see for the harness's Oracle assistant. In code, routes and the
integration protocol it is still the copilot (`harness_api.copilot`,
`/api/v1/copilot/...`); those identifiers are not renamed.

Read this before enabling the copilot. It is the document to show whoever has to
approve sending source code to a model provider.

## What leaves the harness

Only these categories, and only what a user explicitly selected or supplied:

| Category | What it is |
| --- | --- |
| `selected_source` | The SQL or PL/SQL the user selected |
| `object_definition` | The definition of an object the user named |
| `schema_metadata` | Permission-filtered metadata for objects in the request |
| `error_text` | An Oracle error or compiler output the user supplied |
| `plan_text` | An execution plan the user supplied |
| `database_version` | The target's version string |
| `user_message` | The user's own question |

## What never leaves, whatever a caller sends

`result_rows`, `bind_values`, `credentials`, `wallet`. These are refused by category
with a 400. They are not filtered, sanitised or truncated - they are rejected.

There is also no full-schema dump, no unrelated file, and no automatic context
expansion. If it was not selected, named, or pasted, it is not sent.

## Seeing it before it is sent

`POST /api/v1/copilot/context/preview` returns exactly what would go: every
attachment's category, provenance, byte length and SHA-256, the total size, and the
excluded list. The console shows this before the Ask button does anything, and the
`start` stream event repeats it so an IDE can show it too.

Attachment *content* is never echoed back in a preview - only its hash and size.

## What the model can and cannot do

It can read the context above and produce text and proposed edits. With
`HARNESS_KIWI_ENABLED` on and a `profileId` in the request, it can also ask for
reviewed read-only lookups (below). Nothing else.

It cannot run free-form SQL, change data, commit, compile, deploy, or run a runbook.
There is no code path from a model answer to a write. Applying a proposal replaces
text in an editor buffer; running the result is a separate action the user takes,
through the ordinary authorised path.

## Read-only lookups (ADR-0010, off by default)

With `HARNESS_KIWI_ENABLED=true`, a request that names a target (`profileId`) may let
Kiwi call catalog operations that carry `-- @kiwi: allowed` and `risk: read` -- object
status, errors, source, dependencies, columns, cursor statistics, scheduler history and
the like. Without the flag, or without a `profileId`, a request behaves exactly as
before and the model is offered no tool. So, when it is on:

- **Context is no longer only what the user selected.** It includes what Kiwi fetched,
  from the same allowed categories. Approve that before turning the flag on.
- **Every lookup is an ordinary operation.** `KiwiToolbox` runs it as the requesting
  user through `ExecutionService`: grant, permission, privileges, version, limits. It
  leaves an `Execution` row and an `AuditEvent`, like running it from the console. A
  lookup the user could not run, Kiwi cannot run; the refusal goes back to the model as
  a typed error (`not_authorized`, `policy_refused`, `invalid_request`, `not_found`),
  never as a substitute query. Grants are checked on every call, so a grant revoked
  mid-request stops the next lookup. Production stays observation-only.
- **A tool the model is not offered cannot be reached.** Asking for a runbook, the
  worksheet, `explain_plan` or any name outside the list is refused and audited.
- **PL/Scope and database links.** `schema.plscope_identifiers` and
  `plscope_statements` say when PL/Scope data is missing (`PLSCOPE_STATUS` is
  `NOT COLLECTED`, `NONE RECORDED` or `UNKNOWN`, with the compile settings), and Kiwi
  is told to report that instead of inferring a call graph. `schema.db_links_referenced`
  returns link names and remote objects only, never the link's user or host.
- **Results are data.** Rows go to the model as JSON inside
  `BEGIN/END UNTRUSTED TOOL_RESULT` markers, capped per lookup in rows and bytes, with
  a note when they were cut.
- **Rows never reach the stream or the record.** The stream reports each lookup as
  `tool_call` (tool, parameters, why) and `tool_result` (status, row count, bytes,
  truncated, execution id); history lists each request's tool calls with their
  execution ids.
- **Everything is bounded.** Steps, lookups, tokens, lookup bytes and wall time per
  request (`HARNESS_KIWI_MAX_*`, see [setup.md](setup.md)). A request that reaches one
  ends with the answer it has: `done` carries `outcome: "partial"`, `partial: true`
  and `stopReason`.
- **Still never:** free-form SQL, application table rows, bind values, runbooks,
  compile, commit or deploy. The categories under *What never leaves* stay refused.

### Stream events and existing adapters

Protocol 1.1 adds `plan_step`, `tool_call`, `tool_result` and `budget`, and
`done.partial`/`done.stopReason`. The console shows them as a lookup trace. The
DataForge adapter (`integrations/dataforge/src/adapter.ts`) now drops any event name
it does not know before relaying the stream, so a later minor version cannot surprise
the DataForge frontend; a 1.0 client that ignores unknown events keeps working.

Protocol 1.2 (K-5) adds the playbook actions, `parts` on a request, `multiPart` and
`parts` on the `proposal` event, and `parts`/`partReasons` on apply-check. All of it is
additive: a request without `parts` behaves exactly as in 1.1. The DataForge adapter
still announces 1.1 on purpose. A minor version is compatible, and the adapter never
sends `parts`, so it never receives a multi-part proposal.

Protocol 1.3 (K-6) adds `kiwi.explain_package` and `kiwi.explain_process`, the
`subject` field on a request (`OWNER.NAME` or `NAME`) and the `lineage` event. It is
additive; the DataForge adapter stays on 1.1.

### Package and process explainer (K-6)

The explain actions are harness-driven: the harness chooses and bounds every catalog
lookup, and the model gets no tools. Source reaches the model only inside
`BEGIN UNTRUSTED SOURCE` and `BEGIN UNTRUSTED SUMMARIES` markers.

- **Package.** The body is read in pages, scrubbed of comments and string literals,
  and split into subprograms. Subprograms are summarised in batches (one model call
  per batch), then one final call combines the summaries. One model call is always
  kept back for the combine step.
- **Process.** A scheduler job is followed to its chain or program, then through the
  chain steps to the programs they run, then to the package procedures those call.
  Up to three packages are read. Triggers on the tables written are read as well.
- **Limits.** The existing request budget applies (model calls, lookups, bytes,
  tokens, wall time). When it runs out the answer is partial, the `done` event says
  `partial`, and the lineage notes say which subprograms were "not summarised".
- **Lineage.** The `lineage` event carries `nodes`, `edges`, `mermaid` and `notes`.
  Every edge has an `evidence` label: `source` (read from source text), `inferred`
  (for example dynamic SQL), `catalog` (data dictionary) or `scheduler`. If the same
  edge arises twice, source wins over inferred, and inferred over catalog/scheduler.
  Comments are stripped before scanning, so a table named in a comment is not an edge.
- **Mermaid.** The export is deterministic (`flowchart LR`, ids `n0...`, labels
  `relation · evidence`). The console has a "Copy Mermaid" button.
- **What it is not.** Table access is found by pattern matching against the tables the
  catalog reports; it is not a full PL/SQL parse. Nothing is executed or compiled.

## Untrusted content

Source comments, object comments, error text and anything else retrieved from a
database or supplied by a caller are wrapped in labelled delimiters, and the system
prompt states that they are data. Text inside them that tries to give instructions is
reported to the user, not followed. It cannot grant permission, change the rules, or
cause an operation.

This is defence in depth, not a guarantee about model behaviour. The guarantee is
structural: the backend authorises every real operation, and the model has no way to
reach one.

## Records

Each request stores: actor, integration, target reference, action, provider, model,
context categories and size, token counts, latency and outcome. Prompt text is **not**
stored unless `HARNESS_COPILOT_LOG_PROMPTS=true`, which exists for debugging and warns
at startup when it is on.

`GET /api/v1/copilot/history` shows an actor's own requests. It covers copilot
requests only - database operations an IDE performed by itself are that IDE's record,
not the harness's.

## Limits and failure

- A per-actor daily request limit (`HARNESS_COPILOT_USER_DAILY_REQUESTS`).
- Per call: an output-token limit that thinking counts against
  (`HARNESS_COPILOT_MAX_OUTPUT_TOKENS`), a timeout and a retry count.
- A context size cap (`HARNESS_COPILOT_MAX_CONTEXT_BYTES`), enforced per attachment
  and in total.
- A provider failure returns a typed `provider_failure` and leaves every database
  workflow working.
- A browser disconnect propagates to the provider. A partially delivered request is
  never replayed automatically.

## Proposals

A proposal is pinned to the editor id, revision and SHA-256 of the text it was
generated from. `apply-check` refuses if the document changed, the revision moved, the
editor or target changed, the actor differs, or it was already applied. Refusing costs
one re-ask; overwriting someone's work costs more.

### Multi-part proposals

A package spec and body change together, so a request may carry up to eight named
`parts` instead of one `editor`. Each part has its own editor id, revision and text,
and names and editors must be unique. The model is asked to return one fenced block
per part it changes, labelled ```` ```plsql part=<name> ````; labels that name no part
are ignored, and with no labelled block there is no proposal. Every part is pinned,
including those the model left alone (`changed: false`), so an edit to any of them
makes the proposal stale.

Apply-check takes `parts` with each part's current revision and text and applies **all
or none**. A missing, extra, duplicated or re-pointed part, a changed hash or revision
in any one of them, a changed target or a second apply refuses the whole proposal:
`canApply: false`, `reasons` with each part's reasons prefixed `<part>: `,
`partReasons` per part, and no `parts` in the response. Nothing is marked applied, so
after fixing the stale buffer (or re-asking) the proposal can still be applied once.
The console's multi-part mode ("Edit several parts together") works this way, with one
"Apply all parts" button.

## Playbooks and standards

`kiwi.diagnose`, `kiwi.create` and `kiwi.test_block` are playbook actions. Their
instructions (`copilot/context.py`) name the catalog lookups to use for invalid objects
and PLS- errors, pasted ORA- errors, failed scheduler jobs, blocking and slow
statements; the model still chooses its lookups, and without lookups it says which it
would have made. Drafts ground every column in the context or a lookup, and test
blocks report through DBMS_OUTPUT and end with ROLLBACK. After a failed compile the
PL/SQL view offers "Ask Kiwi to fix", which opens Kiwi on `kiwi.diagnose` with the
source and the compiler errors as `line:column text`.

`HARNESS_KIWI_STANDARDS_FILE` points at a JSON file (at most 16 KB) with the team's
standards. Only `namingPrefixes`, `errorLoggingPackage`, `bulkCollectLimit`,
`exceptionPolicy` and `headerTemplate` are accepted, so a typo fails loudly. The
standards go into the system prompt under "Team standards", and Kiwi cites each one it
applies as `(standard: <key>)`. The capabilities endpoint reports
`kiwi.standards {configured, keys}`, or `error` when the file cannot be read or parsed;
in that case each request fails with `configuration_error` before any provider call.

## The fixture provider

With `HARNESS_COPILOT_PROVIDER=fake` the harness returns canned answers and calls no
provider. It is for tests and demonstrations. It announces itself in the capabilities
endpoint, in the `start` event (`isFixtureProvider: true`), in the console banner, and
in the startup warnings.

## Evaluation

`tests/copilot` covers the harness around the model: authorisation, context policy,
streaming, budgets, injection reporting, staleness, provider failure. It does not
evaluate answer quality, because the fixture provider's answers are fixed.

MVP_PLAN.md requires at least 30 representative cases reviewed by a DBA against a real
provider, with all authorisation and no-automatic-execution cases passing and at least
90% reviewed correctness on explain and fix cases. The case set and an opt-in runner for
that exist (`python -m tests.copilot.eval`); the paid run and its DBA review have not
been done. See `tests/copilot/README.md`.
