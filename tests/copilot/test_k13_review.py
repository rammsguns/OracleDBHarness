"""Independent review probes and held-out regressions, entirely offline."""

import json
from pathlib import Path

import pytest

from harness_api.copilot.claim_checks import (
    application_error_claims,
    compiler_error_position_claims,
)
from harness_api.copilot.edit_contract import (
    ambiguity_handler_problems,
    anchor_repair_message,
)
from tests.copilot.test_k13 import ERRORS

PROBES = json.loads(Path(__file__).with_name("k13_review_probes.json").read_text())


@pytest.mark.parametrize("probe", PROBES, ids=[p["id"] for p in PROBES])
def test_review_probe(probe: dict) -> None:
    text = probe["text"]
    if probe["detector"] == "ambiguity_handler":
        flagged = bool(ambiguity_handler_problems(text))
    elif probe["detector"] == "application_error_number":
        flagged = bool(
            application_error_claims(
                text,
                code=probe["id"]
                in {
                    *(f"LEGIT_AEN_0{i}" for i in range(1, 7)),
                    *(f"DEFECT_AEN_0{i}" for i in range(1, 6)),
                },
            )
        )
    else:
        flagged = bool(compiler_error_position_claims(text, [ERRORS]))
    assert flagged == probe["flag"]


@pytest.mark.parametrize(
    "message",
    [
        "'Invalid ID'",
        "'Incorrect status'",
        "'Cannot continue'",
        "q'[Wrong customer]'",
        "q'{not permitted}'",
        "nq'<must be negative>'",
        "'It''s wrong'",
    ],
)
def test_message_words_are_not_prose_warnings(message: str) -> None:
    assert application_error_claims(f"Call RAISE_APPLICATION_ERROR(20003, {message}) to report it.")


@pytest.mark.parametrize(
    "warning",
    [
        "not valid",
        "not allowed",
        "not permitted",
        "should not be used",
        "must be negative",
        "not supported",
        "not legal",
        "not acceptable",
        "must not be used",
        "may not be used",
        "need to be negative",
        "should be negative",
    ],
)
def test_general_prose_negations(warning: str) -> None:
    assert not application_error_claims(
        f"RAISE_APPLICATION_ERROR(20004, 'err'): numbers are {warning}."
    )
    assert application_error_claims(f"RAISE_APPLICATION_ERROR(20004, '{warning}');", code=True)


@pytest.mark.parametrize(
    "source,expected",
    [
        (
            "PROCEDURE p IS FUNCTION f RETURN NUMBER IS BEGIN RETURN 1; END f; BEGIN NULL; EXCEPTION WHEN OTHERS THEN NULL; END p;",
            0,
        ),
        (
            "PROCEDURE p IS FUNCTION f RETURN NUMBER IS BEGIN RETURN 1; EXCEPTION WHEN OTHERS THEN RETURN NULL; END f; BEGIN NULL; EXCEPTION WHEN OTHERS THEN NULL; END p;",
            1,
        ),
        (
            "FUNCTION f RETURN NUMBER IS PROCEDURE p IS BEGIN NULL; END; BEGIN RETURN 1; EXCEPTION WHEN OTHERS THEN RETURN NULL; END;",
            1,
        ),
        (
            "PROCEDURE p IS FUNCTION f RETURN NUMBER IS BEGIN IF 1=1 THEN RETURN 1; END IF; RETURN 2; END; BEGIN BEGIN NULL; EXCEPTION WHEN OTHERS THEN NULL; END; END;",
            0,
        ),
        (
            "PROCEDURE p IS FUNCTION f RETURN NUMBER; FUNCTION f RETURN NUMBER IS BEGIN RETURN CASE WHEN 1=1 THEN 1 ELSE 0 END; END; BEGIN NULL; EXCEPTION WHEN OTHERS THEN NULL; END;",
            0,
        ),
        (
            "FUNCTION f RETURN NUMBER IS PROCEDURE p IS FUNCTION g RETURN NUMBER IS BEGIN RETURN 1; END; BEGIN NULL; EXCEPTION WHEN OTHERS THEN NULL; END; BEGIN RETURN 1; EXCEPTION WHEN OTHERS THEN RETURN 0; END;",
            1,
        ),
    ],
)
def test_routine_body_ownership(source: str, expected: int) -> None:
    assert len(ambiguity_handler_problems(source)) == expected


@pytest.mark.parametrize(
    "problems,has_anchor",
    [
        (["handler hides ambiguous rows"], False),
        (["error number must be in -20999..-20000"], False),
        (["declares a collection variable inline"], False),
        (["handler hides ambiguous rows", "invalid application error number"], False),
        (["`APP.STATUS%TYPE` names owner without its table"], True),
        (["`APP.T.C%ROWTYPE` anchors to a column", "handler hides ambiguous rows"], True),
    ],
)
def test_focused_repair(problems: list[str], has_anchor: bool) -> None:
    message = anchor_repair_message("request", "answer", problems)
    assert ("A column anchor is" in message) == has_anchor
    assert ("Correct the listed PL/SQL semantic problems" in message) != has_anchor
    assert all(problem in message for problem in problems)
