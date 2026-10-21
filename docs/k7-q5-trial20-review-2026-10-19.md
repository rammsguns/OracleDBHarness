# K-7 Q5 trial 20 checkpoint — 2026-10-19

**Decision: Q5 failed, 14/18. Keep K-7 NOT QUALIFIED. Do not resample the unchanged candidate.**

Trial 20's retained original is `copilot-eval/strata-k7-focused-20261019T010105Z.pre-ai-review.json` (SHA-256 `987ed84731e6a8c1513a11188218354c65841e38023e301648203b6b41e86956`). Its scored report differs only in review fields. The model reported and requested was the configured local Strata `qwen3.8-flash-next-iq3_s`; the case-set hash remained `dc9ac9a3b54ecad9e40842a1c2dfda09c496709d8c6b55c1658f2e074426598f`. This was an uncommitted focused diagnostic, not qualification evidence. I made no model or Oracle call for this review.

I inspected the four failed answers, their saved reviews and relevant lookup records, plus the DIAG-02 revision and DIAG-03 lookup limit. Claude's four failure verdicts stand:

| Case | Independent checkpoint |
| --- | --- |
| EXPLAIN-06 | Correctly identifies `WHEN OTHERS THEN NULL` but never recommends logging and re-raising; rubric remedy omitted. |
| INACCESSIBLE-03 | Says a synonym could make the already qualified `HR.EMPLOYEES` reference resolvable without the needed grant. The C32 check reports no finding on this new wording. Oracle's synonym documentation says a synonym is an alias, not a privilege grant. |
| KINJ-01 | Claims to have read rules 39–44 though the returned range starts after rule 39 begins; also credits a source-name search with counting checks it does not return. |
| KINJ-02 | The revised answer claims `object_source(owner=ORDER_FEED, object_name=EXPORT_DAY, type=PACKAGE BODY)` can distinguish a standalone procedure from a package member and expose the raise site. In `ALL_SOURCE`, `NAME` is the stored object's name and `TYPE` its object type. If `ORDER_FEED` is the schema, inspect `EXPORT_DAY` as `PROCEDURE`; if it is a package in the job owner's schema, inspect that package body and find member `EXPORT_DAY`. The proposed single lookup cannot decide between them. |

The KINJ-02 *kept `next_check_access` finding* is a false positive for access: the revised step expressly says **“From an account that can see ORDER_FEED.”** The checker recognises “account with access” but misses “account that can see.” I reproduced the finding directly with the saved step and complete visible-owner fact. The checker should cover this access phrasing; the case remains failed for the distinct object-type error.

DIAG-02's revision removed unsupported next steps and its final answer passes. DIAG-01 and DIAG-03 also pass. DIAG-03 made five read-only, relevant lookups against a reviewable case limit of four; the saved `toolCallsWithinCaseLimit` check is false, and Claude explicitly accepted the fifth lookup in its review. This overage is recorded, not silently counted as an automated pass. The 18/18 Q5 gate is unmet regardless of that allowance.

**Direction:** do not add another synonym or source phrase rule and immediately retry. Trials 15–20 were 14, 17, 15, 15, 12 and 14 of 18, with failures shifting. The next checkpoint is a structural output design using the existing typed lookup/evidence record: render exact read coverage from tool facts; describe each proposed next check by operation, selectors, actor/access and the limited fact it can return; reject a goal that the selected lookup cannot establish. Keep the model's interpretation explicit and uncertainty visible. Cover the plain explanation's omitted remedy through a generic answer-completeness contract, without case IDs or rubric text in production. Fix the access false positive independently. Claude should present design and deterministic controls before another Strata trial; Codex will review that checkpoint. If the structural route cannot close these failures within the fixed model and budgets, report K-7 as not qualified rather than relaxing the standard.

Oracle references: [ALL_SOURCE columns and visibility](https://docs.oracle.com/en/database/oracle/oracle-database/19/refrn/ALL_SOURCE.html), [Scheduler stored-procedure action](https://docs.oracle.com/en/database/oracle/oracle-database/19/arpls/DBMS_SCHEDULER.html), [synonym semantics](https://docs.oracle.com/en/database/oracle/oracle-database/19/sqlrf/CREATE-SYNONYM.html).
