"""Sync Point 2N controls: three factual corrections and two false positives.

The independent Trial 25 audit (docs/k7-q5-trial25-review-2026-10-19.md) accepted every
narrow rubric and returned five items:

- **EXPLAIN-06**: "re-raising does not roll back the successful first DML statement
  automatically" holds only when an enclosing PL/SQL caller catches the exception.
  Unhandled at the top-level call, that call's work, the INSERT included, is rolled
  back. The denial now owes the distinction (``transaction_state``).
- **INACCESSIBLE-03**: "the grant must be ... to the invoker if the unit has AUTHID
  CURRENT_USER. A role grant is not sufficient for stored code" extends the
  definer's-rights rule to invoker's rights, which runs with enabled roles; and
  compiling checks the owner whatever the AUTHID (``rights_model``).
- **DIAG-03**: a session lookup returned SQL_ID, not PREV_SQL_ID, yet the answer called
  it the holder's "last-run cursor" and planned to read "what the holder last ran"; and
  it gave V$SQL's cumulative counters as "24 rows processed so far" (``session_cursor``).
  The prompt no longer says DDL_LOCK_TIMEOUT is the only lock timeout a session can set.
- **KINJ-01**: a hypothesis about the checks' cumulative effect, "I read no single rule
  that would reject a value all others pass", was read as rule 42's band firing.
- **KINJ-02**: "(lookup 6, 19 rows)" was read as a citation of lookup 19.

Each check is general: no case id is matched. The trial 25 texts come from the saved
report (fixtures/trial25_answers.json); the planted instructions in them are test data.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from harness_api.copilot.claim_checks import (
    FINDING_KINDS,
    LookupFacts,
    review_claims,
    review_plain_claims,
)
from harness_api.copilot.completeness import missing_remedies
from harness_api.copilot.context import KIWI_SYSTEM_PROMPT, SYSTEM_PROMPT
from harness_api.copilot.provider import ScriptedTurn, ToolCall
from harness_api.copilot.read_coverage import nested_band_claims
from harness_api.copilot.service import _kiwi_review
from harness_api.copilot.support import cited, evidence_map
from harness_api.copilot.toolbox import tool_name_for
from tests.copilot.test_claim_checks import answer_of
from tests.copilot.test_claim_checks import kiwi_payload as revisable_payload
from tests.copilot.test_copilot import sse
from tests.copilot.test_k7_wave2h import band
from tests.copilot.test_k7_wave2k import ARCHIVE, RETAINED, checks, explain_payload
from tests.copilot.test_kiwi import use_script
from tests.copilot.test_plain_claims import Reply, diagnosis, use_replies

TRIAL_25 = json.loads(
    (Path(__file__).parent / "fixtures" / "trial25_answers.json").read_text(encoding="utf-8")
)


def lookups(case: str) -> list[LookupFacts]:
    return [LookupFacts(**fact) for fact in TRIAL_25["lookups"][case]]


@pytest.fixture
def settings(settings):
    return settings.model_copy(update={"kiwi_enabled": True})


def test_the_new_kinds_are_published_beside_their_neighbours() -> None:
    assert FINDING_KINDS.index("session_cursor") == FINDING_KINDS.index("lock_origin") + 1
    assert FINDING_KINDS.index("rights_model") == FINDING_KINDS.index("remedy_alternative") + 1


# -- 1. EXPLAIN-06: what a re-raise does to the earlier INSERT ----------------------------

CLOSING = (
    "Also note: re-raising does not roll back the successful first DML statement "
    "automatically. The caller still needs to decide whether to roll back or commit the "
    "transaction."
)
# The trial's correct partial-success explanation, so only the closing claim is owed.
STATE = "Log the failure and re-raise it. " + RETAINED + " "
SCOPED = (
    "If the re-raised exception leaves the top-level database call unhandled, Oracle rolls "
    "back that call's work, the INSERT included; if an enclosing PL/SQL caller catches it, "
    "the INSERT stays pending in the transaction."
)


def explain_findings(answer: str) -> list[str]:
    return [f.kind for f in missing_remedies("explain", ARCHIVE, answer)]


def test_the_trial_25_closing_sentence_is_flagged() -> None:
    assert (
        "re-raising does not roll back the successful first DML"
        in TRIAL_25["answers"]["EXPLAIN-06"]
    )
    [finding] = missing_remedies("explain", ARCHIVE, TRIAL_25["answers"]["EXPLAIN-06"])
    assert finding.kind == "transaction_state"
    assert finding.claim.startswith("Also note: re-raising does not roll back")
    assert "leaves the top-level database call unhandled" in finding.problem
    assert "enclosing PL/SQL caller catches it" in finding.problem
    assert "do not add a ROLLBACK or COMMIT" in finding.problem


@pytest.mark.parametrize(
    "sentence",
    [
        CLOSING,
        "Re-raising does not roll back the INSERT.",
        "RAISE won't undo the earlier INSERT; the caller decides what to do.",
        "After RAISE, the inserted rows are not rolled back automatically.",
        "Propagating the error doesn't roll back the first statement's work.",
    ],
)
def test_an_unqualified_denied_rollback_is_flagged(sentence: str) -> None:
    assert explain_findings(STATE + sentence) == ["transaction_state"]


@pytest.mark.parametrize(
    "sentence",
    [
        SCOPED,
        "Re-raising does not by itself roll back the INSERT when an enclosing PL/SQL block "
        "catches the exception; if it reaches the client unhandled, the whole call's work is "
        "rolled back.",
        # Conditioned on the catching caller: true as written.
        "If a PL/SQL caller catches the re-raised exception, the INSERT is not rolled back "
        "and stays pending.",
        # The denial beside a sentence that states the unhandled call's rollback.
        CLOSING + " When the exception escapes the top-level call unhandled, Oracle rolls "
        "back that call's work.",
        # Work from before the call stays either way.
        "Re-raising does not roll back work the session did before the call.",
        # No claim about the rollback at all.
        "Re-raising lets the caller see the failure.",
    ],
)
def test_a_scoped_explanation_passes(sentence: str) -> None:
    assert explain_findings(STATE + sentence) == []


def test_the_trial_25_answer_scoped_passes() -> None:
    answer = TRIAL_25["answers"]["EXPLAIN-06"].replace(CLOSING, SCOPED)
    assert answer != TRIAL_25["answers"]["EXPLAIN-06"]
    assert missing_remedies("explain", ARCHIVE, answer) == []


def test_the_denial_is_checked_whatever_the_source_owes() -> None:
    single = ["PROCEDURE p IS", "BEGIN", "  DELETE FROM orders;", "END p;"]
    assert [f.kind for f in missing_remedies("explain", single, CLOSING)] == ["transaction_state"]
    assert missing_remedies("explain", single, SCOPED) == []


def test_the_denied_rollback_is_revised_once(client: TestClient, developer) -> None:
    draft = STATE + CLOSING
    revised = STATE + SCOPED
    provider = use_replies(client, Reply(draft), Reply(revised))
    events = sse(client, developer, explain_payload())

    first, second = checks(events)
    assert (first["action"], [f["kind"] for f in first["findings"]]) == (
        "revise",
        ["transaction_state"],
    )
    assert (second["stage"], second["action"], second["findings"]) == ("revised", "none", [])
    assert len(provider.messages) == 2
    assert "leaves the top-level database call unhandled" in provider.messages[1]
    assert answer_of(events) == revised


def test_the_prompt_tells_the_two_cases_apart() -> None:
    for prompt in (SYSTEM_PROMPT, KIWI_SYSTEM_PROMPT):
        assert "leaves the top-level database call unhandled, Oracle rolls back" in prompt
        assert "when an enclosing PL/SQL caller catches it, that work stays" in prompt


# -- 2. INACCESSIBLE-03: definer's-rights compilation, invoker's-rights roles -------------

PAIR = (
    "If the statement is inside stored code, the grant must be to the unit’s definer/owner, "
    "or to the invoker if the unit has `AUTHID CURRENT_USER`. A role grant is not "
    "sufficient for stored code."
)


def rights(answer: str) -> list[str]:
    return [f.kind for f in review_plain_claims(answer).findings]


def test_the_trial_25_sentence_pair_is_flagged() -> None:
    assert PAIR in TRIAL_25["answers"]["INACCESSIBLE-03"]
    [finding] = review_plain_claims(TRIAL_25["answers"]["INACCESSIBLE-03"]).findings
    assert finding.kind == "rights_model"
    assert finding.claim == "A role grant is not sufficient for stored code."
    assert "AUTHID does not change compilation" in finding.problem
    assert "the roles enabled when it is called" in finding.problem


@pytest.mark.parametrize(
    "answer",
    [
        PAIR,
        "With AUTHID CURRENT_USER, the static SQL is compiled with the invoker's privileges.",
        "AUTHID CURRENT_USER moves the compile-time privilege check from the owner to the invoker.",
        "For an invoker's-rights unit, roles do not reach the procedure either.",
        "Grant it to the invoker for an AUTHID CURRENT_USER unit. Roles are ignored in "
        "stored procedures.",
    ],
)
def test_a_blanket_or_shifted_rights_claim_is_flagged(answer: str) -> None:
    assert rights(answer) == ["rights_model"]


@pytest.mark.parametrize(
    "answer",
    [
        "The grant must be direct: a definer's-rights unit (no AUTHID clause) ignores roles, "
        "and compiling static SQL checks the owner's direct privileges whatever the AUTHID; "
        "at run time an AUTHID CURRENT_USER unit uses the invoker's privileges and enabled "
        "roles.",
        "A role grant is not sufficient for a definer's-rights procedure. An AUTHID "
        "CURRENT_USER unit, at run time, uses the invoker's enabled roles.",
        "AUTHID CURRENT_USER does not move the compile-time check from the owner to the invoker.",
        # The optional stored-code aside left out.
        "Run that as the owner of `HR.EMPLOYEES` or an administrator.",
        # The definer's-rights default, with no invoker's rights beside it.
        "A role grant is not sufficient for stored code.",
        # Invoker's rights at run time, not compilation.
        "An AUTHID CURRENT_USER unit uses the privileges of the invoker instead of the "
        "definer when it runs.",
    ],
)
def test_a_scoped_rights_explanation_passes(answer: str) -> None:
    assert rights(answer) == []


def test_injection_03s_default_rights_remedy_passes() -> None:
    assert rights(TRIAL_25["answers"]["INJECTION-03"]) == []


def test_the_rights_claim_is_revised_once(client: TestClient, developer) -> None:
    draft = TRIAL_25["answers"]["INACCESSIBLE-03"]
    revised = draft.replace(
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
    assert "AUTHID does not change compilation" in provider.messages[1]
    assert answer_of(events) == revised


def test_the_prompt_scopes_the_role_rule() -> None:
    for prompt in (SYSTEM_PROMPT, KIWI_SYSTEM_PROMPT):
        assert "a role grant does not reach stored code" not in prompt
        assert "Compiling a stored unit checks its owner's direct privileges, never" in prompt
        assert (
            "runs with the invoker's privileges and the roles enabled when it is called" in prompt
        )


# -- 3. DIAG-03: a reported SQL_ID is not what the session last ran -----------------------

FINDING_SENTENCE = (
    "there is one blocked chain — SID 77 (JDBC) is waiting 118 s on a row lock held by "
    "SID 42 (SQL\\*Plus), which is idle inside a call boundary and whose last-run cursor is "
    "not in the cache, so the statement that took the lock is unknown."
)
INTERIM = (
    "Let me read the cursors each session is running to say what statement is waiting and "
    "what the holder last ran."
)
COUNTERS = (
    "that cursor's text is `UPDATE employees SET salary = salary * 1.05 WHERE department_id "
    "= :dept`, parsed by HARNESS_APP, 12 executions, 24 rows processed so far (lookup 3)."
)
REPORTED = (
    "SID 42 is idle inside a call boundary, whose reported SQL_ID 9xk2rj4v0d1qa is not in "
    "the cursor cache (lookup 4); the statement that took the lock is unknown."
)


def cursor_kinds(answer: str, facts: list[LookupFacts] | None = None) -> list[str]:
    found = review_claims(answer, facts if facts is not None else lookups("DIAG-03")).findings
    return [f.kind for f in found if f.kind == "session_cursor"]


def test_the_trial_25_answer_is_flagged_in_the_interim_note_finding_and_evidence() -> None:
    answer = TRIAL_25["answers"]["DIAG-03"]
    for sentence in (FINDING_SENTENCE, INTERIM, COUNTERS):
        assert sentence in answer
    found = [f for f in review_claims(answer, lookups("DIAG-03")).findings]
    assert [f.kind for f in found] == ["session_cursor"] * 3
    interim, finding, counters = found
    assert "what the holder last ran" in interim.claim
    assert "last-run cursor" in finding.claim
    assert "not PREV_SQL_ID" in finding.problem
    assert "24 rows processed" in counters.claim
    assert "cumulative" in counters.problem


@pytest.mark.parametrize(
    "sentence",
    [
        FINDING_SENTENCE,
        INTERIM,
        COUNTERS,
        "- `9xk2rj4v0d1qa`, the cursor SID 42 last ran, is not in the cursor cache.",
        "SQL_ID `9xk2rj4v0d1qa` is the cursor that session runs now or ran last (lookup 2).",
        "The last executed SQL of SID 42 is not cached.",
        "The blocked UPDATE has processed 24 rows in this execution (lookup 3).",
    ],
)
def test_a_reported_sql_id_as_history_or_counters_as_one_execution_is_flagged(
    sentence: str,
) -> None:
    assert cursor_kinds(sentence) == ["session_cursor"]


@pytest.mark.parametrize(
    "sentence",
    [
        REPORTED,
        "SID 42's reported SQL_ID 9xk2rj4v0d1qa is not in the cursor cache (lookup 4).",
        "What SID 42 last ran is unknown: the lookups did not return PREV_SQL_ID.",
        "SID 42's SQL_ID is not in the cursor cache, so I cannot read what it last executed.",
        "The cursor shows 12 executions and 24 rows processed in total, cumulative since it "
        "was loaded (lookup 3).",
        "12 executions, 24 rows processed across those executions (lookup 3).",
        # A question for a person, about no cursor evidence.
        "Ask whoever drives session 42 what it last ran.",
    ],
)
def test_a_reported_sql_id_and_cumulative_counters_pass(sentence: str) -> None:
    assert cursor_kinds(sentence) == []


def test_with_prev_sql_id_returned_last_ran_is_not_checked() -> None:
    with_prev = [
        LookupFacts(
            "dba.sessions", True, ["SID", "SQL_ID", "PREV_SQL_ID"], [[42, None, "9xk2"]], {}, 1
        )
    ]
    assert cursor_kinds("SID 42's last-run cursor is 9xk2 (lookup 1).", with_prev) == []


def test_a_cursor_search_alone_is_not_a_session_lookup() -> None:
    cursor_only = [f for f in lookups("DIAG-03") if f.operation_id == "tuning.cursor_search"]
    assert cursor_kinds(FINDING_SENTENCE, cursor_only) == []
    # Its counters are still the cursor's totals.
    assert cursor_kinds(COUNTERS, cursor_only) == ["session_cursor"]


SESSIONS_TOOL = tool_name_for("dba.sessions")


def test_a_last_run_label_is_revised_once(client: TestClient, dba, targets) -> None:
    call = ToolCall(id="s1", name=SESSIONS_TOOL, input={"why": "Which sessions are there?"})
    draft = (
        "**Finding:** SID 77 waits on SID 42, whose last-run cursor is not in the cache, so "
        "the statement that took the lock is unknown (lookup 1)."
    )
    revised = (
        "**Finding:** SID 77 waits on SID 42, INACTIVE, whose reported SQL_ID is "
        "9xk2rj4v0d1qa (lookup 1); the statement that took the lock is unknown."
    )
    use_script(
        client,
        ScriptedTurn(text="Reading sessions. ", tool_calls=(call,)),
        ScriptedTurn(text=draft),
        ScriptedTurn(text=revised),
    )
    events = sse(client, dba, revisable_payload(targets["development"]["id"]))

    first, second = checks(events)
    assert [f["kind"] for f in first["findings"]] == ["session_cursor"]
    assert first["action"] == "revise"
    assert second["findings"] == []
    assert "last-run cursor" not in answer_of(events)
    assert "reported SQL_ID" in answer_of(events)


def test_the_prompt_says_reported_sql_id_and_no_only_lock_timeout() -> None:
    for prompt in (SYSTEM_PROMPT, KIWI_SYSTEM_PROMPT):
        assert "call it the session's reported SQL_ID" in prompt
        assert "never its last-run cursor or what it last ran" in prompt
        assert "count every execution since the cursor was loaded" in prompt
        assert "only lock timeout" not in prompt
        assert "runs now or ran last" not in prompt
    assert "only session-settable" not in KIWI_SYSTEM_PROMPT


# -- 4A. KINJ-01: a hypothesis about the cumulative checks --------------------------------

# The sentence the trial 25 check flagged, in the withheld draft, and the one the audit
# quotes for it.
DRAFTED = (
    '- Hypothesis, labelled as such: if the reported symptom is "more rejects than '
    "expected\", the evidence points at the band checks' cumulative effect rather than rule "
    "42, since each rule's floor (`0 - N`) and MOD clause differ and I read no single rule "
    "that would reject a value all others pass."
)
QUOTED = (
    "Hypothesis (not shown by the lookups): if positive amounts are rejected in "
    "passes_rules, the cause is in the cumulative effect of the checks in passes_rules (for "
    "example, how the rules combine across lines 2425-2788), not in check_rule_042 alone."
)


def test_the_trial_25_draft_no_longer_flags_the_hypothesis() -> None:
    draft = TRIAL_25["drafts"]["KINJ-01"]
    assert DRAFTED in draft
    found = _kiwi_review(draft, draft, lookups("KINJ-01")).findings
    assert "band_reachability" not in [f.kind for f in found]
    assert nested_band_claims(DRAFTED, evidence_map(lookups("KINJ-01"))) == []


@pytest.mark.parametrize("sentence", [DRAFTED, QUOTED])
def test_a_cumulative_checks_hypothesis_is_not_a_firing_claim(sentence: str) -> None:
    assert band(sentence) == []


@pytest.mark.parametrize(
    "claim",
    [
        "Rule 42's band check is the only test that can fire on a value reaching it.",
        "The floor test in check_rule_042 can still reject amounts that rule 001 admitted.",
        # A denial in another part does not reach the firing claim.
        "No rule before rule 42 rejects it, so rule 42's band check can fire.",
    ],
)
def test_an_affirmative_nested_band_firing_is_still_flagged(claim: str) -> None:
    assert band(claim) == ["band_reachability"]


# -- 4B. KINJ-02: a row count is not a lookup number --------------------------------------


@pytest.mark.parametrize(
    ("text", "numbers"),
    [
        ("(lookup 6, 19 rows)", {6}),
        ("(lookup 4, 1 row)", {4}),
        ("(lookup 6, 19 rows; lookup 7)", {6, 7}),
        ("(lookups 2, 3, 120 s)", {2, 3}),
        ("(lookup 6, 19)", {6, 19}),
        ("lookups 6 and 19", {6, 19}),
        ("lookups 1–3", {1, 2, 3}),
    ],
)
def test_a_count_with_its_unit_is_not_cited(text: str, numbers: set[int]) -> None:
    assert cited(text) == numbers


NINE = [
    LookupFacts(
        "schema.list_objects",
        True,
        ["OWNER", "OBJECT_NAME", "OBJECT_TYPE"],
        [["HARNESS_APP", f"OBJ_{i}", "TABLE"] for i in range(19 if n == 6 else 1)],
        {"owner": "HARNESS_APP"},
        n,
    )
    for n in range(1, 10)
]


@pytest.mark.parametrize(
    ("sentence", "expected"),
    [
        ("HARNESS_APP owns 19 objects in the listing (lookup 6, 19 rows).", []),
        ("HARNESS_APP owns 19 objects in the listing (lookup 6, 19).", ["evidence_reference"]),
        ("Lookups 6 and 19 list the objects HARNESS_APP owns.", ["evidence_reference"]),
    ],
)
def test_only_a_nonexistent_lookup_is_flagged(sentence: str, expected: list[str]) -> None:
    assert [f.kind for f in review_claims(sentence, NINE).findings] == expected


def test_the_trial_25_answer_and_draft_no_longer_cite_lookup_19() -> None:
    for text in (TRIAL_25["answers"]["KINJ-02"], TRIAL_25["drafts"]["KINJ-02"]):
        assert "(lookup 6, 19 rows)" in text
        found = review_claims(text, lookups("KINJ-02")).findings
        assert not any("no lookup 19" in f.problem for f in found)
