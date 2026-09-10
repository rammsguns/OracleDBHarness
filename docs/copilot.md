# Copilot: what is shared, and what the model can do

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

It can read the context above and produce text and proposed edits.

It cannot connect to a database, run a statement, commit, compile, deploy, or call a
tool. There is no code path from a model answer to a database operation. Applying a
proposal replaces text in an editor buffer; running the result is a separate action
the user takes, through the ordinary authorised path.

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
90% reviewed correctness on explain and fix cases. That has not been done. See
`tests/copilot/README.md`.
