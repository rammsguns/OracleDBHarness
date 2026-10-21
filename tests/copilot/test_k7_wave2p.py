"""Sync Point 2O controls: three scope follow-ups to the Wave 2O corrections.

The independent review (docs/k7-wave2o-review-2026-10-20.md) accepted the Wave 2O
mechanisms and the two false-positive fixes, and returned seven claims that escaped:

- **EXPLAIN-06**: a denied catching ("when no enclosing caller catches", "does not catch
  the error") exempted a re-raise rollback denial, and an open question ("whether an
  unhandled top-level error rolls back the INSERT is unknown") counted as the
  distinction. The catching verb must now be affirmed, and the unhandled call's rollback
  stated, not asked.
- **INACCESSIBLE-03**: "an AUTHID CURRENT_USER procedure ignores roles" (subject before
  the verb) was missed, and "owner", "compiles" or "enabled roles" anywhere in the
  sentence exempted a run-time denial. The role statement is now read in either order,
  and its scope only in the part of the sentence that holds it.
- **DIAG-03**: "the cumulative number of executions is 12" exempted "the blocked
  execution has processed 24 rows" in another part. Cumulative now qualifies only the
  part that gives the count.

Each control is general: no case id is matched.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from harness_api.copilot.provider import ScriptedTurn, ToolCall
from harness_api.copilot.toolbox import tool_name_for
from tests.copilot.test_claim_checks import answer_of
from tests.copilot.test_claim_checks import kiwi_payload as revisable_payload
from tests.copilot.test_copilot import sse
from tests.copilot.test_k7_wave2k import checks, explain_payload
from tests.copilot.test_k7_wave2o import (
    CLOSING,
    PAIR,
    SCOPED,
    STATE,
    TRIAL_25,
    cursor_kinds,
    explain_findings,
    rights,
)
from tests.copilot.test_kiwi import use_script
from tests.copilot.test_plain_claims import Reply, diagnosis, use_replies


@pytest.fixture
def settings(settings):
    return settings.model_copy(update={"kiwi_enabled": True})


# -- 1. EXPLAIN-06: an affirmed catching caller, a stated call rollback -------------------

DENIED_CATCHING = [
    "Re-raising does not roll back the INSERT, even when no enclosing caller catches the "
    "exception.",
    "Even if the outer PL/SQL caller does not catch the error, re-raising does not roll back "
    "the INSERT.",
    "Re-raising does not roll back the successful first DML statement automatically. "
    "Whether an unhandled top-level error rolls back the INSERT is unknown.",
]


@pytest.mark.parametrize(
    "sentence",
    [
        *DENIED_CATCHING,
        "Re-raising does not roll back the INSERT unless a caller catches it.",
        "Re-raising never rolls back the INSERT, without any handler catching it.",
        CLOSING + " It is unclear whether an unhandled exception rolls back the call's work.",
        CLOSING + " I do not know if the top-level call rolls back on an unhandled error.",
    ],
)
def test_a_denied_catching_or_an_open_rollback_still_owes_the_distinction(sentence: str) -> None:
    assert explain_findings(STATE + sentence) == ["transaction_state"]


@pytest.mark.parametrize(
    "sentence",
    [
        SCOPED,
        "Re-raising does not by itself roll back the INSERT when an enclosing PL/SQL block "
        "catches the exception; if it reaches the client unhandled, the whole call's work is "
        "rolled back.",
        "If a PL/SQL caller catches the re-raised exception, the INSERT is not rolled back "
        "and stays pending.",
        CLOSING + " When the exception escapes the top-level call unhandled, Oracle rolls "
        "back that call's work.",
        "Re-raising does not roll back work the session did before the call.",
        "Log the error with DBMS_OUTPUT and re-raise it with RAISE.",
    ],
)
def test_the_scoped_explanations_still_pass(sentence: str) -> None:
    assert explain_findings(STATE + sentence) == []


@pytest.mark.parametrize("sentence", DENIED_CATCHING)
def test_a_denied_catching_is_revised_once(client: TestClient, developer, sentence: str) -> None:
    draft, revised = STATE + sentence, STATE + SCOPED
    provider = use_replies(client, Reply(draft), Reply(revised))
    events = sse(client, developer, explain_payload())

    first, second = checks(events)
    assert (first["action"], [f["kind"] for f in first["findings"]]) == (
        "revise",
        ["transaction_state"],
    )
    assert (second["stage"], second["action"], second["findings"]) == ("revised", "none", [])
    assert len(provider.messages) == 2
    assert answer_of(events) == revised
    assert "ROLLBACK;" not in answer_of(events)


# -- 2. INACCESSIBLE-03: either order, scope only where the claim is ---------------------

RUN_TIME_DENIALS = [
    "An AUTHID CURRENT_USER procedure ignores roles at runtime.",
    "Roles are ignored in an AUTHID CURRENT_USER procedure even when enabled roles exist.",
    "For AUTHID CURRENT_USER, the owner still compiles the unit, but roles are ignored in "
    "the procedure at runtime.",
]


@pytest.mark.parametrize(
    "answer",
    [
        *RUN_TIME_DENIALS,
        "An invoker's-rights function does not use enabled roles.",
        "An AUTHID CURRENT_USER package cannot use its roles when it runs.",
        "The owner compiles it; an AUTHID CURRENT_USER procedure disables roles at run time.",
    ],
)
def test_a_run_time_role_denial_for_invokers_rights_is_flagged(answer: str) -> None:
    assert rights(answer) == ["rights_model"]


@pytest.mark.parametrize(
    "answer",
    [
        # No AUTHID: the definer's-rights default.
        "A procedure with no AUTHID clause ignores roles; grant SELECT directly to its owner.",
        "A definer's-rights procedure ignores roles at run time; an AUTHID CURRENT_USER unit "
        "uses the invoker's enabled roles.",
        # A denied invoker's-rights mention: the definer's-rights default (2026-10-17
        # INJECTION-03, found by replaying the saved reports).
        "Because the unit has no `AUTHID CURRENT_USER` clause, it runs with definer’s "
        "rights, so role grants are not enough for stored code.",
        # Owner compilation.
        "Compiling static SQL ignores roles, even in an AUTHID CURRENT_USER unit.",
        "The grant must be direct: a definer's-rights unit (no AUTHID clause) ignores roles, "
        "and compiling static SQL checks the owner's direct privileges whatever the AUTHID; "
        "at run time an AUTHID CURRENT_USER unit uses the invoker's privileges and enabled "
        "roles.",
        # A denied compilation shift, and a denied role denial.
        "AUTHID CURRENT_USER does not move the compile-time check from the owner to the invoker.",
        "An AUTHID CURRENT_USER procedure does not ignore enabled roles at run time.",
        # The direct grant alone.
        "Run `GRANT SELECT ON HR.EMPLOYEES TO HARNESS_APP;` as the owner or an administrator.",
    ],
)
def test_scoped_rights_explanations_still_pass(answer: str) -> None:
    assert rights(answer) == []


@pytest.mark.parametrize("sentence", RUN_TIME_DENIALS)
def test_a_run_time_role_denial_is_revised_once(
    client: TestClient, developer, sentence: str
) -> None:
    draft = TRIAL_25["answers"]["INACCESSIBLE-03"].replace(PAIR, sentence)
    assert draft != TRIAL_25["answers"]["INACCESSIBLE-03"]
    revised = TRIAL_25["answers"]["INACCESSIBLE-03"].replace(
        PAIR,
        "If the statement is inside a definer's-rights unit, the grant must be direct to the "
        "unit's owner; a role is not enough there.",
    )
    provider = use_replies(client, Reply(draft), Reply(revised))
    events = sse(client, developer, diagnosis())

    first, second = checks(events)
    assert (first["action"], [f["kind"] for f in first["findings"]]) == (
        "revise",
        ["rights_model"],
    )
    assert (second["stage"], second["action"], second["findings"]) == ("revised", "none", [])
    assert len(provider.messages) == 2
    assert answer_of(events) == revised


# -- 3. DIAG-03: cumulative qualifies the count it is said of ----------------------------

MIXED = (
    "The blocked execution has processed 24 rows; the cumulative number of executions is 12 "
    "(lookup 3)."
)


def test_a_cumulative_execution_count_does_not_exempt_a_current_row_count() -> None:
    assert cursor_kinds(MIXED) == ["session_cursor"]


@pytest.mark.parametrize(
    "sentence",
    [
        "24 rows processed across 12 executions, cumulative since loading (lookup 3).",
        "The cursor shows 12 executions and 24 rows processed in total, cumulative since it "
        "was loaded (lookup 3).",
        "SID 42's reported SQL_ID 9xk2rj4v0d1qa is not in the cursor cache (lookup 4).",
        "What SID 42 last ran is unknown: the lookups did not return PREV_SQL_ID.",
    ],
)
def test_cumulative_and_reported_id_positives_still_pass(sentence: str) -> None:
    assert cursor_kinds(sentence) == []


def test_a_mixed_counter_sentence_is_revised_once(client: TestClient, dba, targets) -> None:
    sessions = ToolCall(id="s1", name=tool_name_for("dba.sessions"), input={"why": "Sessions?"})
    cursor = ToolCall(
        id="c1",
        name=tool_name_for("tuning.cursor_search"),
        input={"why": "The waiter's cursor.", "sql_id": "b7t3wq8m2z0pf"},
    )
    draft = (
        "**Finding:** SID 77 waits on SID 42 (lookup 1). The blocked execution has processed "
        "24 rows; the cumulative number of executions is 12 (lookup 2)."
    )
    revised = (
        "**Finding:** SID 77 waits on SID 42 (lookup 1). Its cursor shows 24 rows processed "
        "across 12 executions, cumulative since loading (lookup 2)."
    )
    use_script(
        client,
        ScriptedTurn(text="Reading sessions. ", tool_calls=(sessions,)),
        ScriptedTurn(text="Reading the cursor. ", tool_calls=(cursor,)),
        ScriptedTurn(text=draft),
        ScriptedTurn(text=revised),
    )
    events = sse(client, dba, revisable_payload(targets["development"]["id"]))

    first, second = checks(events)
    assert [f["kind"] for f in first["findings"]] == ["session_cursor"]
    assert first["action"] == "revise"
    assert second["findings"] == []
    assert "The blocked execution has processed" not in answer_of(events)
