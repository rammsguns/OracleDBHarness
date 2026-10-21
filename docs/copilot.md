# Kiwi: what is shared, and what the model can do

Kiwi is the name users see for the harness's Oracle assistant. In code, routes and the
integration protocol it is still the copilot (`harness_api.copilot`,
`/api/v1/copilot/...`); those identifiers are not renamed.

Read this before enabling the copilot. It is the document to show whoever has to
approve sending source code to a model provider.

## Where it goes: this machine only, by default

`HARNESS_COPILOT_LOCAL_ONLY` is on unless an administrator turns it off. While it is
on, the only model the copilot may call is one served on the harness's own host (the
`local` provider, any server speaking the OpenAI-style Chat Completions API: the
strata server, llama.cpp's server, Ollama, vLLM). Nothing below leaves the machine.

It is enforced, not just configured (ADR-0012):

- A cloud provider (`anthropic`) is refused when the settings load, so the API does
  not start with one configured. The provider factory refuses it again.
- `HARNESS_COPILOT_BASE_URL` must be plain `http` to a loopback address, 127.0.0.0/8 or
  `::1`. A host name must resolve to loopback addresses only, and the client is then
  pinned to that address, so a name that later resolves elsewhere changes nothing. A
  bad endpoint stops the API at startup.
- Every request is checked again below the HTTP client: one to any other address or
  port is refused. Redirects are refused, not followed, and proxy settings in the
  environment are ignored.
- A local server's error text is not copied into records or logs: it can quote the
  prompt back.

Turning `HARNESS_COPILOT_LOCAL_ONLY` off is a data-sharing decision. The API warns at
startup whenever it is off.

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
  a note when they were cut and a note when nothing matched.
- **Kiwi is told its budget.** Each lookup parameter says how it is matched (names
  exactly, upper-cased; `name_filter`, `text_filter` and `source_filter` as `LIKE`
  patterns). After each batch of results the harness adds one line of its own, outside
  the markers: `[harness] Lookups used: n of N.`; when that was the last lookup, or the
  next turn is the last one `HARNESS_KIWI_MAX_STEPS` allows, the line tells Kiwi to
  answer with what it has, and that turn offers no tools (`tool_choice` none), so it
  has to be the answer. A call the model makes anyway is dropped. The request is
  still `partial`, with `stopReason` `tool_calls` or `steps`.
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

Additive fields and events since 1.3, with no version change (K-7 remediation): a
`validation` object on the `proposal` event and on each of its parts (`kind`, `complete`,
`problems`, `compiled: false`; a structural check of statement boundaries, never a
compilation), a `code_check` event listing fragments when an answer has code but no
proposal, and, only when `HARNESS_KIWI_CAPTURE_EVIDENCE` is on, `evidence` events (what a
lookup returned to the model, bounded, and the explainer's intermediate summaries). The
DataForge adapter drops the event names it does not know.

Protocol 1.4 (K-7, round 17) adds the `claim_check` and `revision` events and
`revisions` on `budget`, described under [Claim checks](#claim-checks). A client that
announces 1.4 or later may receive a `revision`; one that announces an earlier minor
version, or none (taken as 1.0), never does, because it might drop the event and show the
draft and its replacement one after the other. The DataForge adapter stays on 1.1, so its
requests are checked and the findings sent, but they are never revised.

Protocol 1.5 (K-7 qualification plan, 2026-10-18) adds three things, all safe for a
client that drops what it does not know:

- `claim_check.unchecked`: claims a check recognised but could not compute (such as
  `MOD` of a non-finite or very long operand), neither confirmed nor corrected. A check
  that raises is reported as action `check_failed:<type>` and the answer stands.
- `edit_contract`: for a request with an editor whose action edits code (`diagnose`,
  `propose`, `test_block`), whether the answer delivered contract `edit/1` (a fenced
  `sql`/`plsql` proposal, or for `diagnose` a line starting `No code change:`).
  `delivered` is `proposal`, `no_change` or `missing`; `repair` is `none`, `repaired`,
  `skipped:<bound>` or `failed:<reason>`. A missing answer gets one repair call within
  the request's remaining time. A repaired proposal is made from the accepted block as
  data, not by parsing the answer again, so a fence the first answer left open cannot
  change it; the harness closes that fence on screen and renders the accepted block after
  it. Code in prose is never lifted into a proposal. If the edit is still missing (the
  repair also missed, timed out or failed), `done` has outcome `failed` and `errorCode`
  `edit_output_missing`, and the stored request records the same; the answer and usage
  are kept and no `error` event is sent. A `No code change:` diagnosis succeeds.
- `lookup_record`: once per Kiwi answer (not the explain actions), after the answer is
  settled and before the last `budget`, the harness's record of the lookups: number,
  `callId`, `operationId`, the parameters it ran with, `coverage` (`complete`, `empty`,
  `at_limit`, `truncated`, `failed`), `rows`, `rowLimit`, `facts`, `subjects`,
  `sourceRead`, and `notRun` for calls
  the lookup budget refused. `facts` states supported fields from the rows (object status,
  compiler error line and position, waiter and blocker, the source lines read), at most
  ten per lookup; other row values are not repeated. Not part of the answer text.

### Claim checks

A Kiwi answer (every Kiwi action but the explain actions) is checked before it is
delivered (`harness_api/copilot/claim_checks.py`)
for kinds of statement that the K-7 trials got wrong although the prompt already
stated each rule:

- **Session role.** `V$SESSION.ROW_WAIT_OBJ#`, `ROW_WAIT_FILE#`, `ROW_WAIT_BLOCK#` and
  `ROW_WAIT_ROW#` name the row a session is waiting for. Attributing them to a session the
  blocking or session lookups show is not waiting (a blocker, or a session with no
  `BLOCKING_SESSION`) is flagged.
- **Oracle `MOD`.** `MOD(m, n)` has the sign of `m`. A literal `MOD(a, b) = c` that is
  false, or an example listed for `MOD(x, n) = r` that does not satisfy it, is flagged.
- **Where a change belongs.** "The fix belongs in X" is flagged when no source lookup in
  the request returned X's code. The owner counts: reading `APP.PKG` does not support
  `OTHER.PKG`, and a name that only appears in the lines read (a dependency) was not read.
  A member (`APP.PKG.RUN`, `` `PKG`'s run ``, or a single name that is a member) needs its
  implementation read whole, from its first line through its `END`, in a body or a
  standalone unit: a declaration in the specification, a header line or part of the
  member is not enough. A line number given must be among the lines read. Definitions
  and `END`s are found in code only, and only where it is known that the lines are code:
  in unbroken lines read from line 1 of the unit, or from a definition PL/Scope recorded
  (`schema.plscope_identifiers`, usage DEFINITION or DECLARATION) at that line. Lines read
  without the lines before them could lie inside a comment or literal opened earlier, so
  a member there has coverage unknown, and nothing carries across lines not read (a
  search's gaps). Where the start is known, a lexical scan blanks comments, `--` lines
  and literals (including `q'[...]'`) first; when the lines then end inside a comment or
  literal, or cut a member's header before its `IS`/`AS`, its coverage is unknown too,
  and the finding says why. An unnamed `END;` cannot be matched to a member. Lines of
  one unit from several lookups are read together, so adjoining ranges can cover a
  member.
- **More rows of a complete result** (`next_check_cap`). A step asking a lookup the
  request made for a larger `row_limit`, more rows or another page is flagged when every
  run of it for the same target was complete: fewer rows than its `row_limit` and not
  truncated. A step that names another target, or a parameter the runs did not use, is
  a different question and is not flagged; neither is a negated step ("Do not increase"). Each result
  the model receives ends with a harness line, outside the data markers, saying whether
  it was complete or may have more.
- **History from current state** (`history_claim`). Every catalog lookup reads the
  database as it is now, so a check offered to show that an object was dropped or never
  existed, or an unhedged "was dropped", is flagged. Stating that limit is not flagged,
  nor is a manual check of a historical source (an audit trail, flashback, the recycle
  bin, a change record); the harness has not read such a source.
- **Citations** (`evidence_reference`). Flagged: "lookup N" that the request did not
  make (also when it made none); a failed or denied lookup cited as showing something;
  a dotted name, in the text a citation is attached to, that no cited lookup is about
  (owner and name together) and is not a member or column of one; and in the diagnosis
  form, an Evidence bullet that cites neither a lookup nor source lines read, unless it
  is labelled a hypothesis or unknown. A value a lookup returned is evidence about its
  field, not about an object it names: `` JOB_ACTION `OTHER.RUN` `` (or "the job's
  action is") is supported, "OTHER.RUN is VALID" is not; a name in a source line is
  supported when that line is cited. A name cited alone ("`X` (lookup 2)") may come
  from any cell. A citation that labels what follows ("Lookup 1:"), or opens its
  sentence or clause after text naming nothing ("Lookup 2 shows", "As lookup 1 shows,",
  "According to lookup 1,"), covers the text after it; a parenthetical one, or one
  with nothing after it, the text before it; any other placement is unresolved, so
  both sides are checked. Hypotheses, conditions and absences ("not visible", "no such
  object", "returned no rows") are not read as "the lookup shows"; code quoted in
  backticks is not read as a hedge.
- **Source lines** (`evidence_reference`). A line reference ("lines 10-13") is of a
  unit: the one named next to it ("lines 10-13 of APP.PKG", "APP.PKG lines 10-13"),
  else one the text names that was read, else the only unit the cited lookups read;
  "spec" or "body" sets the type. Every line it gives must have been returned for that
  unit by the lookups cited for it; several lookups of one unit can cover a range
  together, another unit cannot. A range is a claim about the lines in it: a member's
  first line and its `END` do not cover the lines between, and a range whose ends came
  back without its middle is flagged with a note to cite the ends singly ("starts at
  line 10, `END calc;` at line 20"). With several units read and none named, the
  reference is unresolved and flagged unless every unit covers it. A citation of
  lookups that read no source (an error's line number) is not checked as source lines.
- **Bounded work on model-written numbers.** Line and lookup numbers in an answer are
  read up to nine digits; a longer one stands for a line past every unit's end. Ranges
  stay intervals and are tested against the lines returned with binary searches, never
  expanded, so `lines 1-100000000` costs the same as `lines 1-2`. A quoted claim is cut
  to 300 characters.
- **Next steps with no Unknown** (`next_check_unknown`). In the diagnosis form (Finding
  and Evidence given), Next steps with no Unknown stated are flagged: a step settles a
  stated Unknown. Which Unknown it settles is not checked.

These checks work from an evidence map built from the lookups, never from the model's
text (`harness_api/copilot/support.py`): per lookup, the objects it is about, from its
parameters and from typed owner/name columns (not from other cells), its filters and
page, the string cells returned with their columns, and per source unit the lines
returned and each member shown, declared or implemented, read whole, in part, or with
coverage unknown. The lookup record shows the same map as `subjects`
and `sourceRead`. An empty finding list is not a grounding verdict.

The checks use Oracle's documented semantics and the columns this request's lookups
returned, never a case, an expected answer or a fixture value. They match wording, so
they can miss a wrong claim phrased another way and can flag a right one; argument flow
(which value reaches a call) is not checked, because that would need a parse of the
PL/SQL the model read.

A finding is not a verdict and is never hidden. Every such answer gets a
`claim_check` event (`stage: "answer"`). With findings, and within the token bound,
the harness sends the model one note listing them and asks for the answer again, with no
tools. The revision is a step inside the request's bounds: it waits at most the smaller
of the request's remaining wall time and `HARNESS_KIWI_REVISION_MAX_WALL_SECONDS`, and
never extends the request. A `revision` event carries the draft being replaced, the revised text
follows as deltas, and a second `claim_check` (`stage: "revised"`) reports what remains.
A claim the model keeps is delivered as written, with its finding. If the revision fails,
the draft is streamed again and stands. `HARNESS_KIWI_MAX_REVISIONS` (default 1) bounds
this; `0` reports findings without revising. The text written between lookups is not
replaced, so the note asks the model to correct a flagged claim there explicitly.

### Package and process explainer (K-6)

The explain actions are harness-driven: the harness chooses and bounds every catalog
lookup, and the model gets no tools. Source reaches the model only inside
`BEGIN UNTRUSTED SOURCE` and `BEGIN UNTRUSTED SUMMARIES` markers.

- **Package.** The body is read in pages, scrubbed of comments and string literals,
  and split into subprograms. Subprograms are summarised in batches (one model call
  per batch), then one final call combines the summaries. One model call is always
  kept back for the combine step, and so is the time it needs: a further batch is
  summarised only if a call as slow as the slowest so far would still leave time to
  combine. Otherwise the summaries made so far are combined and the rest are listed
  as "not summarised".
- **Process.** A scheduler job is followed to its chain or program, then through the
  chain steps to the programs they run, then to the package procedures those call.
  Up to three packages are read. Triggers on the tables written are read as well.
- **Limits.** The explain actions have their own, larger per-request budget
  (`HARNESS_KIWI_EXPLAIN_*`: model calls, lookups, bytes, tokens, and
  `HARNESS_KIWI_EXPLAIN_MAX_WALL_SECONDS`, 420 s, in place of Kiwi's 180 s). Each
  summary is a separate model call, which on a local model can take a minute or more.
  When the budget runs out the answer is partial, the `done` event says
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

The proposal is the answer's first fenced block labelled `sql`, `plsql` or nothing; the
rest of the answer, other blocks included, is its rationale. Blocks are read a line at a
time, as Markdown reads them (`harness_api/copilot/fences.py`): a block opens on a line
starting with a fence and any label, possibly indented in a list, and closes on a line
holding only a fence. A ```` ```text ```` block holding the error message is therefore
skipped as a whole, rather than its closing fence being taken for the start of the
proposal. The model is asked for one block with the complete replacement, and told
that the first SQL or PL/SQL block is taken as the proposal, so lines it quotes go in
inline code or a ```` ```text ```` block (system prompt, rule 5). When it gives several
SQL blocks anyway, the first is still the proposal, and a reviewer judges the answer by
it.

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
**Incomplete edits.** Every proposal, and every part, carries `validation`
(`kind`, `complete`, `problems`, `compiled: false`, `note`): a structural boundary check
(balanced parentheses and BEGIN/CASE/IF/LOOP/END, a body, a closing `;`, nothing after the
unit), never an Oracle compilation. Policy: the server does not refuse an incomplete edit,
because staleness and all-or-none are its gates and completeness is advice; apply-check
returns the `validation` of the stored text (per part). The console shows the problems in
a warning and requires a second, explicit click ("Apply anyway" / "Apply all parts
anyway") before it calls apply-check at all, so nothing is marked applied by accident.
With no editor, a `code_check` event lists the answer's code blocks that failed the check;
the console shows them and the evaluation report saves them as `codeCheck`.

The console's multi-part mode ("Edit several parts together") works this way, with one
"Apply all parts" button.

## Playbooks and standards

`kiwi.diagnose`, `kiwi.create` and `kiwi.test_block` are playbook actions. Their
instructions (`copilot/context.py`) name the catalog lookups to use for invalid objects
and PLS- errors, pasted ORA- errors, failed scheduler jobs, blocking and slow
statements; the model still chooses its lookups, and without lookups it says which it
would have made. In a long body, `schema.object_source_search` finds the lines that
match a LIKE pattern (a subprogram, a rule, a phrase) and `schema.object_source_range`
reads around them, rather than paging through the body range by range. Drafts ground
every column in the context or a lookup, and test blocks report through DBMS_OUTPUT and end with ROLLBACK. After a failed compile the
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
that exist (`python -m tests.copilot.eval`). Since ADR-0012 the run goes to a model on
the harness's own host; its DBA review has not been done. See `tests/copilot/README.md`.

### Kiwi (K-7)

Case set `2026-09-29.1` adds 13 Kiwi cases: diagnose, create, package and ETL
explanations, injection in a job comment and in package source that tries to call tools,
authorisation refusals (no grant, unknown target, integration credential, unoffered and
write lookups) and a lookup-budget case. The exit criterion is that all safety and
authorisation cases pass, at least 90% of DBA-reviewed diagnose and explain cases are
correct, and no answer claims something Kiwi did not look up. Invariants 1-7 are mapped to
named tests in `tests/copilot/test_kiwi_evaluation.py`.

**Status:** the cases, runner checks and rehearsal exist and pass against scripted turns.
The case set is now `2026-10-15.4`. A run against the local model on 2026-10-16
completed all 52 cases with every automated safety check passing (KIWI_PLAN.md, status
log). Its DBA review and score have **not** been done, so `HARNESS_KIWI_ENABLED` stays
off by default.

### Compile on development targets (K-8, proposed)

Not built. [ADR-0011](decisions.md) proposes that Kiwi's draft may be compiled on a
target marked `development`, by the user's confirmation, in a separate session. Until
it is accepted, Kiwi cannot compile and applying a proposal is still not running it.
