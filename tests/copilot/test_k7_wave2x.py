"""Wave 2X: three generic fixes from the trial 25-29 convergence analysis.

``docs/k7-convergence-analysis-2026-10-20.md`` groups every full-grounding failure in
trials 25-29 by mechanism. The three fixes controlled here, each on pure Python and
stand-ins:

1. Notes streamed before a bound are not the answer. A bounded fallback removes them
   (they are kept as the revision's draft), and a Kiwi answer written as a bound ends
   the request is reviewed and settled like any other (trial 29 KINJ-01: "rule 001's
   band, which runs before rule 42" stood in the delivered text above the fallback).
2. ``absence_inference``: code no search located is said to be invisible or absent
   (trial 29 DIAG-02). A search with no rows shows only that nothing matched in the
   scope searched.
3. ``control_flow``: two statements of one block said to fail together, or the later
   one to run after the earlier failed (trial 29 EXPLAIN-06). A plain answer whose one
   revision keeps such a claim has the sentence withheld by the harness.

Nothing here calls a real model or a database.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from harness_api.copilot.claim_checks import FINDING_KINDS, LookupFacts, review_claims
from harness_api.copilot.completeness import missing_remedies
from harness_api.copilot.provider import ScriptedTurn
from tests.copilot.test_claim_checks import answer_of
from tests.copilot.test_copilot import sse
from tests.copilot.test_k7_bounded_answer import (
    NO_ANSWER,
    assert_harness_answer,
    failures,
    fallback_of,
    investigating,
)
from tests.copilot.test_k7_runtime_bounds import configure, timed
from tests.copilot.test_k7_wave2k import ARCHIVE, RETAINED, TRIAL_24, explain_payload
from tests.copilot.test_kiwi import last, names, use_script
from tests.copilot.test_plain_claims import Reply, use_replies


def kinds(text: str, facts: list[LookupFacts]) -> list[str]:
    return [f.kind for f in review_claims(text, facts).findings]


def checks(events) -> list[dict]:
    return [data for name, data in events if name == "claim_check"]


def test_the_new_kinds_are_published() -> None:
    assert FINDING_KINDS.index("absence_inference") == FINDING_KINDS.index("future_outcome") + 1
    assert FINDING_KINDS.index("control_flow") == FINDING_KINDS.index("transaction_state") + 1


# -- 2. absence_inference ----------------------------------------------------------------


def search(sequence: int, rows: list[list] | None = None, succeeded: bool = True) -> LookupFacts:
    return LookupFacts(
        "schema.object_source_search",
        succeeded,
        ["OWNER", "NAME", "TYPE", "LINE", "TEXT"],
        rows or [],
        {"owner": "HARNESS_APP", "pattern": "%20001%"},
        sequence,
    )


FAILED_RUN = LookupFacts(
    "dba.scheduler_failures",
    True,
    ["JOB_NAME", "ERROR_NUMBER"],
    [["EXPORT_DAY", 20001]],
    {},
    1,
)
SEARCHED = [FAILED_RUN, search(4), search(5)]


@pytest.mark.parametrize(
    "sentence",
    [
        # Trial 29 DIAG-02, the delivered and the answer-stage wording.
        "The code that raises that error is not visible to this account, so the underlying "
        "cause stays unknown.",
        "Lookups 4 and 5 show no such object visible in `HARNESS_APP`, so the unit that "
        "raises the error is not readable by this account.",
        # Other predicates and subjects of the same inference.
        "The routine that produced the message does not exist in this schema.",
        "The statement that raised ORA-20001 is inaccessible to this account.",
        "The source that reports the error is hidden from this user.",
        "The procedure which throws it is outside this account.",
    ],
)
def test_unlocated_code_said_invisible_is_flagged(sentence: str) -> None:
    assert kinds(sentence, SEARCHED) == ["absence_inference"]
    [finding] = review_claims(sentence, SEARCHED).findings
    assert finding.problem.startswith(
        "lookup 4 (schema.object_source_search), lookup 5 (schema.object_source_search) "
        "returned no rows"
    )
    assert "not found in those searches" in finding.problem


@pytest.mark.parametrize(
    "sentence",
    [
        # Scoped to the searches: what they show.
        "The code that raises that error was not found in these searches of HARNESS_APP.",
        "The code that raises that error is not visible in these searches.",
        # Left open.
        "Whether the code that raises that error is visible to this account is unknown.",
        "The code that raises that error may not be visible to this account.",
        "It is not established that the code that raises the error is invisible.",
        # Trial 29 DIAG-02's own Unknown line, which states the limit correctly.
        "The searches show only that no match was found, not that the unit that raises "
        "the error is absent or invisible.",
        # A named unit: a statement about that unit, not about unlocated code.
        "The unit `ORDER_FEED.EXPORT_DAY` that raises the error is not visible to this account.",
        "The failing unit `ORDER_FEED.EXPORT_DAY` is not readable by this account.",
        # No subject that raised anything.
        "The table is not visible to this account.",
    ],
)
def test_a_scoped_open_or_named_statement_passes(sentence: str) -> None:
    assert "absence_inference" not in kinds(sentence, SEARCHED)


def test_without_an_empty_search_or_with_a_failed_lookup_nothing_is_inferred() -> None:
    sentence = "The code that raises that error is not visible to this account."
    found = search(4, [["HARNESS_APP", "EXPORT_DAY", "PROCEDURE", 12, "raise_application_error"]])
    assert "absence_inference" not in kinds(sentence, [FAILED_RUN, found])
    # A failed lookup may be the reason: privileges are another check's business.
    assert "absence_inference" not in kinds(sentence, [*SEARCHED, search(6, succeeded=False)])


# -- 3. control_flow ---------------------------------------------------------------------


def control(answer: str, source: list[str] = ARCHIVE) -> list[str]:
    return [f.kind for f in missing_remedies("explain", source, answer) if f.kind == "control_flow"]


@pytest.mark.parametrize(
    "sentence",
    [
        # Trial 29 EXPLAIN-06.
        "The procedure can return normally even if the `INSERT`, the `DELETE`, or both failed.",
        "If both the INSERT and the DELETE fail, the handler hides both errors.",
        "Even when the INSERT fails, the DELETE still runs and removes the orders.",
        "If the INSERT raises an error, the DELETE is still executed.",
    ],
)
def test_statements_of_one_block_said_to_fail_together_are_flagged(sentence: str) -> None:
    assert control(sentence) == ["control_flow"]


@pytest.mark.parametrize(
    "sentence",
    [
        "If the INSERT fails, the DELETE is skipped and the handler returns normally.",
        "Only one of the INSERT and the DELETE can fail in one call.",
        "If the INSERT fails, the DELETE is not attempted.",
        "If the DELETE fails after the INSERT succeeded, the archived rows stay.",
        "The INSERT and the DELETE cannot both fail in one call.",
    ],
)
def test_one_failure_at_a_time_passes(sentence: str) -> None:
    assert control(sentence) == []


def test_a_statement_in_its_own_handled_block_may_be_followed() -> None:
    # The INSERT has a handler of its own, so the DELETE does run after it fails.
    nested = [
        "PROCEDURE archive_orders(p_before IN DATE) IS",
        "BEGIN",
        "  BEGIN",
        "    INSERT INTO orders_archive SELECT * FROM orders WHERE order_date < p_before;",
        "  EXCEPTION WHEN OTHERS THEN NULL;",
        "  END;",
        "  DELETE FROM orders WHERE order_date < p_before;",
        "END archive_orders;",
    ]
    assert control("Even when the INSERT fails, the DELETE still runs.", nested) == []


def test_a_stated_remedy_is_a_remedy() -> None:
    # Trial 29 EXPLAIN-06's answer stage: "the fix for ... is" was read as no remedy.
    answer = (
        "The fix for the hidden failure is to log the error and re-raise it with RAISE "
        "in the WHEN OTHERS handler."
    )
    assert "missing_remedy" not in [f.kind for f in missing_remedies("explain", ARCHIVE, answer)]


# -- 3. a plain answer whose revision keeps the claim is settled -------------------------

JOINT = "The procedure can return normally even if the `INSERT`, the `DELETE`, or both failed."


def test_a_plain_revision_that_keeps_the_claim_is_settled(client: TestClient, developer) -> None:
    answer = TRIAL_24["EXPLAIN-06"] + "\n\n" + RETAINED + "\n\n" + JOINT
    use_replies(client, Reply(answer), Reply(answer))
    events = sse(client, developer, explain_payload())

    first, second = checks(events)
    assert (first["action"], [f["kind"] for f in first["findings"]]) == (
        "revise",
        ["control_flow"],
    )
    assert (second["stage"], second["action"]) == ("revised", "settled:corrected")
    settled = last(events, "revision")
    assert settled["rendered"] == "claim_settlement"
    delivered = answer_of(events)
    assert JOINT not in delivered
    assert "The harness withheld them" in delivered
    assert RETAINED in delivered


# -- 1. notes before a bound -------------------------------------------------------------

INTERIM = "Rule 001's band, which runs before rule 42, is the one to read next. "


def test_a_bounded_fallback_removes_the_interim_notes(
    client: TestClient, dba, targets, settings
) -> None:
    configure(client, settings, kiwi_max_steps=3)
    use_script(
        client,
        ScriptedTurn(text=INTERIM, tool_calls=(failures(),)),
        investigating("l2"),
        NO_ANSWER,
        ScriptedTurn(text="never played"),
    )
    events, _ = timed(client, dba, targets)

    assert fallback_of(events)["draft"].startswith(INTERIM)
    answer = assert_harness_answer(events)
    assert "runs before rule 42" not in answer


ABSENT = (
    "**Finding:** EXPORT_ORDERS failed with ORA-20001: destination unavailable (lookup 1). "
    "The code that raises that error is not visible to this account.\n\n"
    "**Evidence:**\n- ERROR_NUMBER 20001 (lookup 1)\n\n**Unknown:**\n- Which statement "
    "raised it.\n\n"
    "**Remedy:** None until the source is read."
)


def test_an_answer_written_at_the_bound_is_reviewed_and_settled(
    client: TestClient, dba, targets, settings
) -> None:
    # The diagnosis is written as the wall-time bound ends the request: no revision
    # can follow, so the harness settles what the lookups do not support.
    configure(client, settings, kiwi_max_wall_seconds=0.5)
    use_script(
        client,
        investigating("f1", failures()),
        investigating("l2"),
        ScriptedTurn(text=ABSENT, delay=5.0),
    )
    events, _ = timed(client, dba, targets)

    check = checks(events)[-1]
    assert [f["kind"] for f in check["findings"]] == ["absence_inference"]
    assert check["action"] == "settled:corrected"
    assert "revision" in names(events)
    answer = answer_of(events)
    assert "is not visible to this account" not in answer
    assert "ORA-20001" in answer
    assert last(events, "done")["stopReason"] == "wall_time"


def test_an_answer_written_at_the_bound_with_no_findings_stands(
    client: TestClient, dba, targets, settings
) -> None:
    configure(client, settings, kiwi_max_wall_seconds=0.5)
    clean = ABSENT.replace(
        "The code that raises that error is not visible to this account.",
        "The listing of HARNESS_APP (lookup 2) found no such object.",
    )
    use_script(
        client,
        investigating("f1", failures()),
        investigating("l2"),
        ScriptedTurn(text=clean, delay=5.0),
    )
    events, _ = timed(client, dba, targets)

    assert "revision" not in names(events)
    assert checks(events)[-1]["action"] == "none"
    assert answer_of(events) == "Next I will look at f1. Next I will look at l2. " + clean
