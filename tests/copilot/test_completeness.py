"""Whether a plain explanation gives the remedy its task asks for (C35).

The source is a handler-swallowing procedure of the kind EXPLAIN-06 supplies. The
flagged answers are retained answers' words (trial 20, copilot-eval/strata-k7-focused-
20261019T010105Z.json; and 2026-10-16, run-2026-10-16-one-block.json), cut to the
sentences about the handler; the controls are reviewed passes' words and the same
source changed so that nothing is owed.
"""

from __future__ import annotations

import pytest

from harness_api.copilot.completeness import handlers, missing_remedies, obligations

SWALLOWS = [
    "PROCEDURE purge_log(p_before IN DATE) IS",
    "BEGIN",
    "  DELETE FROM app_log WHERE logged_at < p_before;",
    "EXCEPTION",
    "  WHEN OTHERS THEN",
    "    NULL;",
    "END purge_log;",
]
RERAISES = [*SWALLOWS[:5], "    log_error(SQLERRM);", "    RAISE;", "END purge_log;"]
COMMENTED = [
    "PROCEDURE purge_log(p_before IN DATE) IS",
    "BEGIN",
    "  DELETE FROM app_log WHERE logged_at < p_before;",
    "  /* EXCEPTION WHEN OTHERS THEN NULL; was removed */",
    "END purge_log;",
]
RANKS = [
    "SELECT department_id, employee_id",
    "  FROM (SELECT e.*, ROW_NUMBER() OVER (PARTITION BY department_id ORDER BY hire_date) rn",
    "          FROM employees e)",
    " WHERE rn = 1",
]

# Trial 20: what the handler lacks, described; nothing recommended.
T20 = (
    "`WHEN OTHERS THEN NULL` catches every exception raised by the statements inside the "
    "`BEGIN` block and throws it away. It does not log the error, re-raise it, return a "
    "status, or otherwise make the failure visible to the caller. The procedure simply "
    "finishes normally after an error."
)
T16 = (
    "The handler does nothing: it neither logs the error nor re-raises it. There is no "
    "logging, no re-raise, and no return status."
)


@pytest.mark.parametrize("answer", [T20, T16])
def test_a_described_but_unremedied_handler_is_flagged(answer: str) -> None:
    [finding] = missing_remedies("explain", SWALLOWS, answer)
    assert finding.kind == "missing_remedy"
    assert finding.claim == "source line 5: WHEN OTHERS THEN"
    assert "the remedy is to log it and re-raise it" in finding.problem
    # The finding quotes the task and asks; it does not write the change itself.
    assert "Say what to change, or say why no change is recommended." in finding.problem


@pytest.mark.parametrize(
    "answer",
    [
        # 2026-10-18 (a reviewed pass).
        "The general remedy is to log the error and then re-raise it, for example by using "
        "`RAISE` or `RAISE_APPLICATION_ERROR`, so the caller can see that the operation failed.",
        # 2026-10-17 (a reviewed pass).
        "The safer general remedy is to log the failure and then re-raise it.",
        "You should log the error and re-raise it with RAISE.",
        "- **Fix:** Re-raise after logging, so the caller sees the failure.",
        "Replace `NULL;` with a call to your logger followed by `RAISE;`.",
        "```plsql\nEXCEPTION\n  WHEN OTHERS THEN\n    log_error(SQLERRM);\n    RAISE;\n```",
        "No change is recommended: this procedure is a best-effort cleanup by design.",
    ],
)
def test_a_recommended_remedy_or_a_stated_decision_passes(answer: str) -> None:
    assert missing_remedies("explain", SWALLOWS, f"{T20}\n\n{answer}") == []


@pytest.mark.parametrize(
    "answer",
    [
        # A modal elsewhere in a sentence that says the code lacks it.
        "It does not re-raise the error, so the caller should not assume success.",
        "```plsql\nBEGIN\n  -- RAISE would go here\n  NULL;\nEXCEPTION WHEN OTHERS THEN NULL;\nEND;\n```",
    ],
)
def test_a_negated_or_commented_re_raise_is_no_remedy(answer: str) -> None:
    assert len(missing_remedies("explain", SWALLOWS, answer)) == 1


@pytest.mark.parametrize("source", [RERAISES, COMMENTED, ["SELECT 1 FROM dual"]])
def test_a_source_without_a_swallowing_handler_owes_nothing(source: list[str]) -> None:
    assert obligations(source) == []
    assert missing_remedies("explain", source, T20) == []


def test_only_the_explain_task_asks_for_these_remedies() -> None:
    assert missing_remedies("diagnose", SWALLOWS, T20) == []


def test_a_ranking_owes_how_ties_are_broken() -> None:
    [owed] = obligations(RANKS)
    assert (owed.kind, owed.line) == ("row_ties", 2)
    [finding] = missing_remedies("explain", RANKS, "It returns one row per department.")
    assert "say how ties are broken" in finding.problem
    answered = (
        "Employees hired on the same day tie, so the row returned is not deterministic: add "
        "employee_id to the ORDER BY, or use RANK to keep every tied row."
    )
    assert missing_remedies("explain", RANKS, answered) == []


# -- C35-A corrections --------------------------------------------------------------------


@pytest.mark.parametrize(
    "answer",
    [
        # The review's reproduction: a decision with no reason.
        "No change is recommended.",
        "No code change is needed.",
        "No change is recommended for this procedure.",
        "No change is required. The procedure deletes old rows.",
        # Sync Point 1 finding 2: a connective with no reason after it.
        "No change is recommended because.",
        "No change is recommended because: ",
        "No change is recommended since.",
        "No change is recommended: .",
        "No change is recommended by design.",
        "No change is recommended. Because.",
        "No change is recommended. This is by design.",
    ],
)
def test_no_change_without_a_reason_is_no_answer(answer: str) -> None:
    [finding] = missing_remedies("explain", SWALLOWS, f"{T20}\n\n{answer}")
    assert finding.kind == "missing_remedy"


@pytest.mark.parametrize(
    "answer",
    [
        "No change is recommended: this procedure is a best-effort cleanup by design.",
        "No change is needed because a failed purge is retried by the next nightly run.",
        "No change is recommended, since the caller logs and retries every failure.",
        "No change is recommended. This is by design: the purge must never fail the batch.",
        "No change is recommended — the handler is intended to make the purge best-effort.",
        "No change is recommended because: the nightly run retries every purge.",
        "The handler is deliberate, so no change is recommended for the purge.",
        "No change is recommended. Because the purge is best-effort, a failure must not stop it.",
    ],
)
def test_no_change_with_its_reason_is_an_answer(answer: str) -> None:
    assert missing_remedies("explain", SWALLOWS, f"{T20}\n\n{answer}") == []


# Sync Point 1B: the review's two counterexamples and its control.
IGNORE_RETURN = (
    "PROCEDURE p(l_ignore IN BOOLEAN) IS\nBEGIN\n  NULL;\nEXCEPTION\n  WHEN OTHERS THEN\n"
    "    IF l_ignore THEN RETURN; END IF;\n    RAISE;\nEND p;"
)
IGNORE_GOTO = (
    "BEGIN\n  NULL;\nEXCEPTION\n  WHEN OTHERS THEN\n    IF l_ignore THEN GOTO done; END IF;\n"
    "    RAISE;\n    <<done>> NULL;\nEND;"
)
DEBUG_LOGS = (
    "BEGIN\n  NULL;\nEXCEPTION\n  WHEN OTHERS THEN\n    IF l_debug THEN NULL; END IF;\n"
    "    RAISE;\nEND;"
)


@pytest.mark.parametrize(
    "code",
    [
        # The review's reproduction: RAISE before EXCEPTION, and the handler swallows.
        "BEGIN\n  RAISE;\nEXCEPTION\n  WHEN OTHERS THEN\n    NULL;\nEND;",
        # A raise in one handler; another still swallows.
        "BEGIN\n  NULL;\nEXCEPTION\n  WHEN NO_DATA_FOUND THEN\n    RAISE;\n"
        "  WHEN OTHERS THEN\n    NULL;\nEND;",
        # RAISE_APPLICATION_ERROR in the body, not in the handler.
        "BEGIN\n  IF p_before IS NULL THEN\n    RAISE_APPLICATION_ERROR(-20001, 'x');\n  END IF;\n"
        "EXCEPTION\n  WHEN OTHERS THEN\n    log_error(SQLERRM);\nEND;",
        # A RAISE only in a string literal inside the handler.
        "BEGIN\n  NULL;\nEXCEPTION\n  WHEN OTHERS THEN\n    log_error('RAISE');\nEND;",
        # Code with no handler at all.
        "BEGIN\n  RAISE;\nEND;",
        # Sync Point 1 finding 3, the review's reproduction: a raise on no known path.
        "BEGIN\n  NULL;\nEXCEPTION\n  WHEN OTHERS THEN IF 1=0 THEN RAISE; ELSE NULL; END IF;\nEND;",
        # A raise on one branch only.
        "BEGIN\n  NULL;\nEXCEPTION\n  WHEN OTHERS THEN\n    IF SQLCODE = -1 THEN\n      RAISE;\n"
        "    END IF;\nEND;",
        # A raise in one CASE branch.
        "BEGIN\n  NULL;\nEXCEPTION\n  WHEN OTHERS THEN\n    CASE WHEN SQLCODE = -1 THEN RAISE;"
        " ELSE NULL; END CASE;\nEND;",
        # A raise inside a nested block that swallows it.
        "BEGIN\n  NULL;\nEXCEPTION\n  WHEN OTHERS THEN\n    BEGIN\n      RAISE;\n    EXCEPTION\n"
        "      WHEN OTHERS THEN NULL;\n    END;\nEND;",
        # The outer handler raises, but a nested one swallows: every handler must propagate.
        "BEGIN\n  NULL;\nEXCEPTION\n  WHEN OTHERS THEN\n    BEGIN\n      log_error(SQLERRM);\n"
        "    EXCEPTION\n      WHEN OTHERS THEN NULL;\n    END;\n    RAISE;\nEND;",
        # A raise after the handler has returned.
        "BEGIN\n  NULL;\nEXCEPTION\n  WHEN OTHERS THEN\n    RETURN;\n    RAISE;\nEND;",
        # Sync Point 1B: a branch that returns or jumps past the later raise swallows.
        IGNORE_RETURN,
        IGNORE_GOTO,
        # A RETURN in a nested block's handler leaves the outer handler too.
        "BEGIN\n  NULL;\nEXCEPTION\n  WHEN OTHERS THEN\n    BEGIN\n      log_error(SQLERRM);\n"
        "    EXCEPTION\n      WHEN OTHERS THEN RETURN;\n    END;\n    RAISE;\nEND;",
        # An EXIT out of a loop around the handler skips the raise.
        "LOOP\n  BEGIN\n    NULL;\n  EXCEPTION\n    WHEN OTHERS THEN\n      IF l_ignore THEN EXIT;"
        " END IF;\n      RAISE;\n  END;\nEND LOOP;",
    ],
)
def test_replacement_code_counts_only_when_its_handler_propagates(code: str) -> None:
    assert len(missing_remedies("explain", SWALLOWS, f"{T20}\n\n```plsql\n{code}\n```")) == 1


@pytest.mark.parametrize(
    "code",
    [
        "BEGIN\n  NULL;\nEXCEPTION\n  WHEN OTHERS THEN\n    log_error(SQLERRM);\n    RAISE;\nEND;",
        "BEGIN\n  NULL;\nEXCEPTION\n  WHEN OTHERS THEN\n    log_error(SQLERRM);\n"
        "    RAISE_APPLICATION_ERROR(-20001, 'purge failed', TRUE);\nEND;",
        "BEGIN\n  NULL;\nEXCEPTION\n  WHEN NO_DATA_FOUND THEN\n    RAISE;\n"
        "  WHEN OTHERS THEN\n    log_error(SQLERRM);\n    RAISE purge_failed;\nEND;",
        # Unconditional after a branch: the IF logs, then the handler raises on every path.
        "BEGIN\n  NULL;\nEXCEPTION\n  WHEN OTHERS THEN\n    IF SQLCODE = -1 THEN\n"
        "      log_error('duplicate');\n    ELSE\n      NULL;\n    END IF;\n    RAISE;\nEND;",
        # A declared exception, a CASE expression and a nested block before the raise.
        "DECLARE\n  e_purge EXCEPTION;\nBEGIN\n  NULL;\nEXCEPTION\n  WHEN OTHERS THEN\n"
        "    v := CASE WHEN SQLCODE = -1 THEN 'dup' ELSE 'other' END;\n"
        "    BEGIN\n      log_error(v);\n    END;\n    RAISE;\nEND;",
        "BEGIN\n  NULL;\nEXCEPTION\n  WHEN OTHERS THEN NULL; RAISE;\nEND;",
        # Sync Point 1B's control: a conditional step that does not leave, then the raise.
        DEBUG_LOGS,
        # An EXIT that leaves only a loop inside the handler.
        "BEGIN\n  NULL;\nEXCEPTION\n  WHEN OTHERS THEN\n    FOR i IN 1 .. 3 LOOP\n"
        "      EXIT WHEN i = 2;\n    END LOOP;\n    RAISE;\nEND;",
    ],
)
def test_replacement_code_whose_handlers_propagate_counts(code: str) -> None:
    assert missing_remedies("explain", SWALLOWS, f"{T20}\n\n```plsql\n{code}\n```") == []


def test_a_conditional_raise_in_the_source_still_owes_nothing() -> None:
    # The source side is unchanged: a handler that raises on some path is not the
    # swallowing handler the task names. Only replacement code must propagate always.
    source = [
        "BEGIN",
        "  NULL;",
        "EXCEPTION",
        "  WHEN OTHERS THEN",
        "    IF SQLCODE = -1 THEN RAISE; END IF;",
        "END;",
    ]
    assert obligations(source) == []


@pytest.mark.parametrize("code", [IGNORE_RETURN, IGNORE_GOTO])
def test_a_branch_that_returns_or_jumps_raises_but_does_not_propagate(code: str) -> None:
    [handler] = handlers(code.split("\n"))
    assert (handler.raises, handler.propagates) == (True, False)


def test_a_conditional_step_that_stays_in_the_handler_still_propagates() -> None:
    [handler] = handlers(DEBUG_LOGS.split("\n"))
    assert (handler.raises, handler.propagates) == (True, True)


# -- Sync Point 1C: labelled loop transfers -------------------------------------------------


def in_outer_loop(statements: str) -> str:
    """A handler inside a loop labelled outer_loop, running ``statements``."""
    return (
        "<<outer_loop>>\nLOOP\n  BEGIN\n    NULL;\n  EXCEPTION\n    WHEN OTHERS THEN\n"
        f"{statements}\n  END;\nEND LOOP;"
    )


# A transfer to the loop around the handler skips the raise.
LEAVES_OUTER = {
    "exit_outer_from_inner": in_outer_loop(
        "      FOR i IN 1 .. 3 LOOP\n        EXIT outer_loop WHEN l_ignore;\n"
        "      END LOOP;\n      RAISE;"
    ),
    "continue_outer_from_inner": in_outer_loop(
        "      FOR i IN 1 .. 3 LOOP\n        CONTINUE outer_loop WHEN l_ignore;\n"
        "      END LOOP;\n      RAISE;"
    ),
    "exit_outer_at_handler_level": in_outer_loop(
        "      EXIT outer_loop WHEN l_ignore;\n      RAISE;"
    ),
    "inner_label_names_the_outer": in_outer_loop(
        "      <<inner_loop>> LOOP\n        EXIT outer_loop;\n      END LOOP;\n      RAISE;"
    ),
}
# A transfer to a loop inside the handler leaves only that loop; the raise still runs.
STAYS_INNER = {
    "unlabelled_exit": in_outer_loop(
        "      FOR i IN 1 .. 3 LOOP\n        EXIT WHEN l_done;\n      END LOOP;\n      RAISE;"
    ),
    "unlabelled_continue": in_outer_loop(
        "      FOR i IN 1 .. 3 LOOP\n        CONTINUE WHEN l_skip;\n      END LOOP;\n      RAISE;"
    ),
    "labelled_exit": in_outer_loop(
        "      <<inner_loop>> FOR i IN 1 .. 3 LOOP\n        EXIT inner_loop WHEN l_done;\n"
        "      END LOOP;\n      RAISE;"
    ),
    "labelled_continue": in_outer_loop(
        "      <<inner_loop>> FOR i IN 1 .. 3 LOOP\n        CONTINUE inner_loop WHEN l_skip;\n"
        "      END LOOP;\n      RAISE;"
    ),
    # A WHILE header with a comparison, and an EXIT from a deeper loop to the labelled one.
    "labelled_while_from_deeper": in_outer_loop(
        "      <<inner_loop>> WHILE l_n < 3 LOOP\n        LOOP\n          EXIT inner_loop;\n"
        "        END LOOP;\n      END LOOP;\n      RAISE;"
    ),
}


@pytest.mark.parametrize("code", LEAVES_OUTER.values(), ids=LEAVES_OUTER.keys())
def test_a_transfer_to_a_loop_around_the_handler_does_not_propagate(code: str) -> None:
    # One handler: "EXIT outer_loop WHEN" opens no handler of its own.
    [handler] = handlers(code.split("\n"))
    assert (handler.raises, handler.propagates) == (True, False)
    fenced = f"{T20}\n\n```plsql\n{code}\n```"
    [finding] = missing_remedies("explain", SWALLOWS, fenced)
    assert finding.kind == "missing_remedy"


@pytest.mark.parametrize("code", STAYS_INNER.values(), ids=STAYS_INNER.keys())
def test_a_transfer_within_an_inner_loop_still_propagates(code: str) -> None:
    [handler] = handlers(code.split("\n"))
    assert (handler.raises, handler.propagates) == (True, True)
    assert missing_remedies("explain", SWALLOWS, f"{T20}\n\n```plsql\n{code}\n```") == []
