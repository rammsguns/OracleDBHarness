# K-7 qualification success plan

Prepared 2026-10-18. Status: **Q1–Q4, C28/C29, C31 and C32 accepted in reviewed scope;
Q5 trial 20 failed 14/18;
K-7 NOT QUALIFIED**.
Owner: Claude implements and records evidence; Codex independently reviews it.

This is the active execution plan. It supersedes the sequence and stale status in
[the original remediation plan](k7-remediation-plan.md), while preserving its
qualification requirements and historical evidence. Work assignments remain in
[claude_tasks.md](../claude_tasks.md). The starting review is
[round 18](k7-claude-round18-review-2026-10-18.md).
Latest checkpoint: [C33–C35 design review](k7-c33-c35-design-review-2026-10-19.md).
C24-A/B/C and C27 are closed in their assigned scope; Q3/Q4 implementation readiness
is accepted. C23, C25 and C26 remain closed in assigned scope. The first Q5 model
diagnostic completed on existing Strata and failed six grounding reviews (12/18).
C28/C29, C31 and C32 are accepted in assigned scope, and C30's reassessment
is recorded. The released Q5 retry (trial 20) scored 14/18. C33's access fix is accepted;
C34/C35 need design corrections and integrated review before any further provider run.
Requirements below are unchanged; the [second-pass review](k7-c24-second-pass-review-2026-10-18.md),
[C23–C26 correction review](k7-c23-c26-review-2026-10-18.md) and
[first Q1–Q4 review](k7-q1-q4-review-2026-10-18.md) remain historical evidence.

## 1. Outcome and fixed constraints

Qualify the existing local Strata model through two consecutive complete, independently
reviewed runs on the same clean implementation and configuration.

| Requirement | Acceptance for each full run |
| --- | --- |
| All cases | 52/52 pass, including categories outside the old correctness gate |
| Correctness | 18/18 |
| Safety | 19/19 and no failed mandatory safety check in any category |
| Reviews | All 39 required reviews pass, with accurate reviewer identity |
| Grounding | Every Kiwi answer reviewed against retained evidence, including interim text, proposals and next steps |

> **Amended 2026-10-20 (user decision):** Q6 grounding is judged with the S1/S2 rubric: 0 S1 and ≤ 2 S2 per full run; all other rows are unchanged. See [k7-acceptance-bar-2026-10-20.md](k7-acceptance-bar-2026-10-20.md) §4.
| Completion | No aborted or budget-stopped evaluation; each case meets its defined outcome |
| Provenance | Clean implementation commit, case/fixture hashes, model identity, settings and complete original outputs |
| Repeatability | Full A and full B pass consecutively with no implementation/configuration/input changes |

Expected authorization refusals and permitted partial answers remain governed by
their existing case definitions. They do not require unsafe dispatches to count as
passing. The fixed inventory has 46 dispatch cases; preserve the scorer's provider
completion requirement as well as the stricter 52/52 target.

- Provider: `local`, localOnly true; Strata `http://127.0.0.1:8080/v1`.
- Model: `qwen3.8-flash-next-iq3_s`; verify requested, served and response identities.
- Reuse configured authentication. Follow the existing connection instructions in
  `claude_tasks.md`; never put credentials in reports.
- Preserve case version `2026-10-18.1`, SHA
  `dc9ac9a3b54ecad9e40842a1c2dfda09c496709d8c6b55c1658f2e074426598f`.
- Starting stand-in SHA:
  `e457f49db26e3cee5d76744fe1a85b81249d2c34cbb9b3bf4f9c2ff5b2acba44`.
- No model/server substitution, restart, wider runtime budgets, reduced rubric,
  deleted difficult cases or production answers keyed to case IDs.
- Kiwi stays OFF by default. K-8 and autonomous Oracle compilation remain gated.

Success is an evidence requirement, not a guarantee that this model will meet it.
If it does not, retain NOT QUALIFIED and report the actual limitation.

## 2. Why the approach must change

Trial 18 scored 15/18: FIX-01 supplied unfenced code, DIAG-01 inferred history from
current catalog state, and KINJ-02 proposed increasing an already adequate row limit.
The five Kiwi claim checks reported no findings and no revision occurred. Passing
502 unit/integration tests therefore does not establish answer reliability.

Earlier trials moved between failure patterns as prompt rules accumulated. The new
revision/checker implementation also introduced C21 budget defects and C22 arithmetic
defects. Adding another phrase matcher for each failed sentence is not the primary
strategy for this plan.

The implementation direction is **validated output contracts, structured evidence,
and bounded finalization**. The model interprets the request and proposes conclusions;
the harness preserves evidence identity, renders supported factual fields, validates
edits and prevents invalid repair attempts. Reviewer judgment still decides semantic
correctness and grounding.

## 3. Ordered work packages

Do these in dependency order. Do not run another expensive diagnostic merely because
a prompt-string test passes. Keep prior accepted C16/C18/C19/C20 fixture and selector
fixes, C17 compiler safety, and C11 review history intact.

### Q1 — Repair runtime correctness first (C21, C22)

Primary files: `copilot/service.py`, `claim_checks.py`, `config.py`, provider adapters
and their regression tests.

1. Give the whole request one authoritative deadline and turn/token accounting.
   A revision consumes the remaining original budget. Reserve a finalization turn
   early when useful; do not grant extra turns or time after exhaustion.
2. Apply a real asynchronous timeout while awaiting provider events. Count revision
   turns in the same budget and cancel/close the provider stream on expiration.
3. Restore the retained draft on failed, empty or unexpected tool-request revisions;
   report partial/error state and findings accurately. Preserve legacy clients.
4. Make supported MOD arithmetic exact with faithful operand formatting. Reject
   non-finite/unsupported inputs and contain checker exceptions. Unchecked arithmetic
   must not become an authoritative correction or a successful grounding verdict.

**Exit evidence:** reproduce the round-18 failures as failing controls, then show they
pass after the fix. Include an exhausted original budget, silent provider, delayed
TurnEnd, cancellation, empty revision, 38-digit integer, scaled decimals and malformed
operands. Re-run affected protocol/drawer tests. Codex closes C21/C22 before Q5.

### Q2 — Make editor output a validated contract (C08 / FIX-01)

Primary files: `copilot/service.py`, `proposal.py`, `fences.py`, action instructions,
provider response handling and console contract only where needed.

Define one explicit response contract for actions that promise an edit: explanation,
proposed unit text and its document/context identity. Parse it locally and render the
SQL fence/UI proposal from accepted code, so display formatting is not the sole source
of proposal existence. Use a versioned structured envelope or equivalent explicit
contract; do not assume Strata supports constrained decoding without checking it.

Malformed contract output may receive one repair attempt inside Q1's remaining budget.
Do not extract arbitrary prose as executable SQL or silently wrap a package member
as a standalone unit. Preserve complete-unit, multi-part, stale-source, authorization
and structural validation checks. A failed repair remains an explicit failed answer;
it is not a passing refusal for a case requiring a usable proposal.

**Exit evidence:** the actual trial-18 unfenced example produces either a valid,
reviewable replacement through the contract/repair path or a correctly reported
failure. Tests also retain quoted-source handling, deletion-only edits, package
spec/body identity and invalid/multiple statements. Compilation remains separate
evidence and occurs only in the established disposable verification workflow.

### Q3 — Build answers from structured evidence (C08)

Primary files: `copilot/toolbox.py`, `service.py`, `context.py`, and a small shared
evidence/answer-contract module if needed. Keep the change bounded to diagnosis first.

Create a request-local evidence record from existing retained lookups. Reuse call ID,
sequence, operation, target, parameters, status, returned rows/ranges and truncation.
Record the model's conclusions separately; they must not become evidence for themselves.

Require diagnosis output to identify:

| Output item | Required support |
| --- | --- |
| Observed fact | Specific lookup/row or source range with matching target and scope |
| Inference | Stated premise, an explicit uncertainty label and no conflict with returned data |
| Unknown | What the current evidence cannot settle |
| Suggested check | The unknown it could resolve, the necessary parameters, and why existing results do not already settle it |
| Suggested change | Read source/object identity and sufficient evidence that a change is warranted |

Have the harness render known factual fields and citations from this record where
possible: waiter/blocker identities, returned error locations, object status and source
coverage. The model should not regenerate those values from memory. Locally validate
references and scope. A valid citation alone does not prove the attached prose true.

For the current failure families:

- Current catalog state cannot establish whether an absent object was dropped or
  never existed. Require historical evidence for historical claims.
- Recommend a larger result cap only with evidence of truncation/cap limitation;
  account for filters, paging and byte limits. Do not infer retention cause solely
  from a counter/history difference.
- Derive session roles from returned fields. Never invent the waiter's row from the
  blocker identity; observe field validity limits.
- If arithmetic or control-flow analysis cannot be verified within the supported
  scope, omit unnecessary derived examples. If the question needs them, read/check
  enough evidence or explicitly leave that part unresolved.
- Unread source permits a request to inspect it, not an assertion that it needs a fix.
- Keep interim progress text factual: report lookup activity, not premature diagnoses.

Remove optional background and speculative next steps that do not help answer the
request. Do not make all answers empty or evasive to pass grounding: required rubric
content still has to be delivered. Broader explain/create paths must preserve their
existing behavior and be evaluated in the full suite.

**Exit evidence:** service-level tests demonstrate accepted supported answers and
rejected/repaired unsupported ones using changed identifiers and equivalent wording.
Exercise incomplete, denied and truncated evidence. Keep raw drafts, findings, final
answers and repair outcomes reviewable. No new free-form SQL tool is needed.

### Q4 — Validate the implementation independently (C06)

Build a small regression matrix from failure families, not a table of canned case
answers. Include correct controls so over-rejection is visible. Use actual retained
bad answers for regressions and separate variations not used to implement the checker.
These supplementary checks do not alter the fixed qualification case set.

Cover output delivery; empty versus invalid results; current versus historical state;
cap-limited versus complete results; unread source; source order; numeric boundaries;
waiter/blocker roles; privilege/injection constraints; budget and protocol failures.

Run affected Python checks during development. At readiness, run the full Python suite,
web typecheck/tests and lint once; classify skips accurately. Run the applicable
retained-proposal Oracle compile/behavior checks after C17 safeguards. Record checked,
skipped and answer-only SQL separately; arrange targeted verification of required
generated code the helper cannot cover. Do not call helper success universal coverage.

**Exit evidence:** independent review closes open P1 defects; no known incorrect
checker correction, budget bypass or required proposal failure remains. Record all
validation commands and outcomes in `claude_k7.md`. No full qualification yet.

### Q5 — Demonstrate readiness on Strata (C08)

Use the existing authenticated identity check and runner commands in `claude_tasks.md`.
Run the same 18 diagnostic IDs, with `--only`, against the completed Q1–Q4 candidate.
Record model, effective settings, implementation/fixture provenance, originals and
all revisions. Review all 18 answers afresh, including previously passing cases.

**Exit:** 18/18 with no unsupported claims and no unresolved implementation defect.

> **Superseded 2026-10-20 (user decision):** the Q5 exit is now the pooled, severity-tiered bar in [k7-acceptance-bar-2026-10-20.md](k7-acceptance-bar-2026-10-20.md). The original text above is kept for history.
This is a readiness gate, not qualification. Do not turn valid expected refusals or
partial results into different case requirements.

If it fails, map each failure to Q1/Q2/Q3 and retain the run. Fix the general cause,
rerun affected controls, then rerun the diagnostic. Do not repeatedly sample an
unchanged failed candidate until a lucky run appears. If a failure family persists
after two implementation iterations, record a short design reassessment with concrete
examples before another provider run. Keep the same model and target.

### Q6 — Freeze, run full A, then full B (C06)

After readiness and final review, coordinate a clean implementation commit. Include
all executable helpers; existing dirty-state logic must identify the code that runs.
Record a candidate manifest containing commit, case hash, stand-in hash, protocol,
provider/model identities, effective generation settings and runtime limits. Record
server settings as available; do not retune or restart Strata for this plan.

Run the existing provider command **without `--only`** for A, with a fresh report path.
Complete all fresh reviews and separate grounding checks, then run `score` and inspect
`strictTarget.met`. Scorer success alone does not establish grounding or two-run success.

Only after A passes every requirement in section 1, run B on the same candidate and
configuration, again with fresh reviews and a different report path. Check every case;
do not carry verdicts across. All model-visible interim/final text remains in review.
Raw replaced drafts remain audit evidence; any incorrect text still shown to the user
must be assessed rather than hidden by revision bookkeeping.

If A or B fails, preserve the result and return to the relevant implementation package.
A code, prompt, model-setting, fixture or case change requires a new candidate and a
new A/B sequence. Do not cherry-pick passing cases across runs or exclude failed runs
from the qualification record.

### Q7 — Publish the qualification decision (C09 / C11)

Create `docs/k7-qualification-result.md` with links to the two original reports,
reviewed reports, scores, independent grounding confirmations, candidate manifest,
validation and Oracle coverage. Identify reviewers as AI or human accurately.

Update `claude_k7.md`, `claude_tasks.md`, `KIWI_PLAN.md`, `NEXT_PHASE_PLAN.md` and the
evaluation README with the actual decision. Mark K7 qualified only when every gate
is met. Enabling Kiwi or beginning K8 remains a separately recorded decision.

## 4. Execution queue and checkpoints

| Order | Claude deliverable | Codex checkpoint | Status |
| --- | --- | --- | --- |
| Q1 | C21/C22 fixes and reproductions | Runtime/arithmetic behavior verified | Accepted in assigned scope, 2026-10-18 |
| Q2 | Validated proposal output contract | Unfenced/malformed/context controls | Accepted in reviewed plain-editor scope; C23/C25 closed |
| Q3 | Evidence-backed diagnosis and next-check contract | Unsupported claims and correct controls | Accepted in reviewed scope; C24-A/B/C and C27 closed |
| Q4 | Candidate validation report | Open P1 findings closed | Accepted in reviewed scope; fourth-pass controls and checks pass |
| Q5 | Fresh 18-case Strata report with reviews | 18/18 readiness | Trial 20 reviewed, 14/18; C33 accepted, C34/C35 corrections and integration checkpoint before another run |
| Q6 | Clean candidate manifest, full A and B | Two complete 52/52 results and grounding | Pending Q5 |
| Q7 | Qualification decision and linked evidence | Traceable final status | Pending Q6 |

Complete C34/C35 corrections and integrated review before another focused Q5 diagnostic. Do not
reopen previously verified work wholesale. Record progress per package in `claude_k7.md`, including
what is implemented, what is tested and which
exit criterion remains. A plan, a green test suite or an empty findings list is never
recorded as successful model qualification.
