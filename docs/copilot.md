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
