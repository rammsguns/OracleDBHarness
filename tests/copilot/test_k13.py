"""K13 retained failures and independent semantic variants; no provider or database."""

import pytest

from harness_api.copilot.claim_checks import (
    LookupFacts,
    application_error_claims,
    compiler_error_position_claims,
    review_claims,
    review_plain_claims,
)
from harness_api.copilot.edit_contract import ambiguity_handler_problems, proposal_semantic_problems

RETAINED_PROPOSAL = """CREATE OR REPLACE FUNCTION salary_for(p_last_name IN VARCHAR2)
  RETURN NUMBER
IS
  l_salary employees.salary%TYPE;
BEGIN
  SELECT salary INTO l_salary FROM employees WHERE last_name = p_last_name;
  RETURN l_salary;
EXCEPTION
  WHEN TOO_MANY_ROWS THEN
    RETURN NULL;
END salary_for;"""


def function(handler: str) -> str:
    return f"FUNCTION f RETURN NUMBER IS x NUMBER; BEGIN RETURN 1; EXCEPTION {handler} END;"


@pytest.mark.parametrize(
    "handler",
    [
        "WHEN TOO_MANY_ROWS THEN RETURN NULL;",
        "when too_many_rows then return -1;",
        "WHEN TOO_MANY_ROWS THEN RETURN q'[RAISE; WHEN OTHERS THEN]';",
        "WHEN TOO_MANY_ROWS THEN x := NULL; NULL;",
        "WHEN TOO_MANY_ROWS THEN x := 42;",
        "WHEN TOO_MANY_ROWS THEN result(1) := NULL; NULL;",
        "WHEN TOO_MANY_ROWS THEN :result := -1; NULL;",
        "WHEN TOO_MANY_ROWS THEN /* RAISE; */ RETURN 'RAISE';",
        "WHEN TOO_MANY_ROWS THEN -- RAISE\n RETURN 0;",
        "WHEN OTHERS THEN RETURN NULL;",
        "when others then x := 0; null;",
        "WHEN NO_DATA_FOUND OR TOO_MANY_ROWS THEN RETURN 0;",
    ],
)
def test_ambiguity_sentinel(handler: str) -> None:
    assert ambiguity_handler_problems(function(handler))


@pytest.mark.parametrize(
    "handler",
    [
        "WHEN TOO_MANY_ROWS THEN RAISE;",
        "WHEN TOO_MANY_ROWS THEN RAISE_APPLICATION_ERROR(-20001, 'key=' || x);",
        "WHEN TOO_MANY_ROWS THEN log_error; RAISE;",
        "WHEN TOO_MANY_ROWS THEN x := 0; RAISE;",
        "WHEN NO_DATA_FOUND THEN RETURN NULL;",
        "WHEN NO_DATA_FOUND THEN RETURN -1;",
        "WHEN NO_DATA_FOUND THEN x := NULL; NULL;",
        "WHEN OTHERS THEN log_error; RAISE;",
        "WHEN TOO_MANY_ROWS THEN RAISE named_error;",
        "WHEN TOO_MANY_ROWS THEN dbms_output.put_line('error'); RAISE;",
    ],
)
def test_raising_or_missing_row_handler(handler: str) -> None:
    assert not ambiguity_handler_problems(function(handler))


def test_retained_proposal_and_correct_application_error_proposal() -> None:
    assert ambiguity_handler_problems(RETAINED_PROPOSAL)
    fixed = RETAINED_PROPOSAL.replace(
        "RETURN NULL;", "RAISE_APPLICATION_ERROR(-20001, 'Ambiguous key: ' || p_last_name);"
    )
    assert not proposal_semantic_problems(fixed)
    assert not proposal_semantic_problems(fixed.replace("-20001", "-20002"))
    assert not ambiguity_handler_problems(
        "PROCEDURE p IS BEGIN NULL; EXCEPTION WHEN OTHERS THEN NULL; END;"
    )


@pytest.mark.parametrize(
    "literal", ["20001", "+20001", "0", "-19999", "-21000", "99999", "-30000", "20002", "-1"]
)
@pytest.mark.parametrize(
    "wrapper",
    [
        "Use RAISE_APPLICATION_ERROR({}, …).",
        "```sql\nBEGIN RAISE_APPLICATION_ERROR({}, 'bad'); END;\n```",
    ],
)
def test_invalid_error_numbers(literal: str, wrapper: str) -> None:
    answer = wrapper.format(literal)
    assert application_error_claims(answer)
    assert review_claims(answer, []).findings
    assert review_plain_claims(answer).findings


@pytest.mark.parametrize(
    "answer",
    [
        "find the `RAISE_APPLICATION_ERROR(20001, …)`",
        "Use `raise_application_error ( + 21000, 'message')`.",
    ],
)
def test_retained_application_number_and_spacing(answer: str) -> None:
    assert application_error_claims(answer)


@pytest.mark.parametrize(
    "text",
    [
        "RAISE_APPLICATION_ERROR(-20000, 'ok');",
        "RAISE_APPLICATION_ERROR(-20999, 'ok');",
        "RAISE_APPLICATION_ERROR(-20001, 'ok');",
        "RAISE_APPLICATION_ERROR(-20002, 'ok');",
        "RAISE_APPLICATION_ERROR(error_code, 'unknown');",
        "RAISE_APPLICATION_ERROR(:code, 'unknown');",
        "RAISE_APPLICATION_ERROR(-20000 - offset, 'unknown');",
        "-- RAISE_APPLICATION_ERROR(20001, 'comment');",
        "x := q'[RAISE_APPLICATION_ERROR(20001, 'literal')]';",
        "x := 'RAISE_APPLICATION_ERROR(20001, ''literal'')';",
    ],
)
def test_valid_or_unchecked_application_numbers(text: str) -> None:
    assert not application_error_claims(text, code=True)


@pytest.mark.parametrize(
    "word",
    [
        "invalid",
        "illegal",
        "incorrect",
        "wrong",
        "Do not use",
        "Don't use",
        "Never use",
        "ORA-21000 results from",
    ],
)
def test_warning_about_bad_call_is_legitimate(word: str) -> None:
    assert not application_error_claims(f"{word} RAISE_APPLICATION_ERROR(20001, …).")


ERRORS = LookupFacts(
    "schema.object_errors",
    True,
    ["OWNER", "NAME", "TYPE", "LINE", "POSITION", "TEXT"],
    [
        ["APP", "REPORT", "PACKAGE BODY", 5, 39, "ORA-00942"],
        ["APP", "REPORT", "PACKAGE BODY", 5, 5, "SQL Statement ignored"],
    ],
    {"owner": "APP", "object_name": "REPORT"},
)


@pytest.mark.parametrize(
    "text",
    [
        "both compiler errors point at line 5, position 39: `ORA-00942: table or view does not exist`.",
        "Both errors are at 5:39.",
        "All compiler errors occur at line 5 column 39.",
        "Each error points to 5:39.",
        "Every compiler error is located at line 5, position 39.",
        "The errors all appear at 5:39.",
        "Both of the errors are at line 5 position 39.",
        "All compilation errors originate at 5:39.",
        "Each of the compiler errors is on line 5, column 39.",
    ],
)
def test_collective_error_position(text: str) -> None:
    assert compiler_error_position_claims(text, [ERRORS])
    assert any(f.kind == "compiler_error_position" for f in review_claims(text, [ERRORS]).findings)


@pytest.mark.parametrize(
    "text",
    [
        "Both errors are on line 5.",
        "The first error is at line 5, position 39.",
        "The errors occur at 5:39 and 5:5.",
        "Both errors occur at 5:39 and 5:5.",
        "Not all compiler errors point at 5:39.",
        "Both errors do not point at 5:39.",
        "Both packages have errors at 5:39.",
        "Are both errors at 5:39?",
        "Whether both errors are at 5:39 is unknown.",
        "Both errors have different positions on line 5.",
        "```text\nBoth errors are at 5:39.\n```",
    ],
)
def test_individual_shared_line_and_qualified_positions(text: str) -> None:
    assert not compiler_error_position_claims(text, [ERRORS])


def test_positions_require_successful_rows_for_one_object() -> None:
    from dataclasses import replace

    answer = "Both compiler errors are at 5:39."
    assert not compiler_error_position_claims(answer, [])
    assert not compiler_error_position_claims(answer, [replace(ERRORS, succeeded=False)])
    assert not compiler_error_position_claims(answer, [replace(ERRORS, rows=[ERRORS.rows[0]])])
    assert not compiler_error_position_claims(
        answer, [replace(ERRORS, rows=[ERRORS.rows[0], ERRORS.rows[0]])]
    )
    separate = [ERRORS.rows[0], ["APP", "OTHER", "PACKAGE BODY", 5, 5, "other"]]
    assert not compiler_error_position_claims(answer, [replace(ERRORS, rows=separate)])


def test_retained_compiler_finding_verbatim() -> None:
    sentence = (
        "`HARNESS_APP.EMPLOYEE_REPORT` is INVALID only as its PACKAGE BODY "
        "(the specification is VALID), and both compiler errors point at line 5, "
        "position 39: `ORA-00942: table or view does not exist`."
    )
    assert compiler_error_position_claims(sentence, [ERRORS])


def test_unrelated_invalid_sentence_does_not_excuse_bad_call() -> None:
    assert application_error_claims("The body is invalid. Use RAISE_APPLICATION_ERROR(20001, …).")
    assert application_error_claims("RAISE_APPLICATION_ERROR(2.1e4, 'bad');", code=True)
    assert not application_error_claims("RAISE_APPLICATION_ERROR(-2.0001e4, 'ok');", code=True)


def test_package_procedure_after_function_does_not_become_function_handler() -> None:
    assert not ambiguity_handler_problems(
        "CREATE PACKAGE BODY p AS FUNCTION f RETURN NUMBER IS BEGIN RETURN 1; END; "
        "PROCEDURE p IS BEGIN NULL; EXCEPTION WHEN OTHERS THEN NULL; END; END;"
    )


@pytest.mark.parametrize(
    "first",
    [
        RETAINED_PROPOSAL,
        RETAINED_PROPOSAL.replace("RETURN NULL;", "RAISE_APPLICATION_ERROR(20001, 'ambiguous');"),
    ],
)
def test_proposal_semantics_use_one_bounded_repair(client, developer, first: str) -> None:
    from tests.copilot.test_k7_edit_contract import fix01_payload, run

    fixed = RETAINED_PROPOSAL.replace(
        "RETURN NULL;", "RAISE_APPLICATION_ERROR(-20001, 'Ambiguous key: ' || p_last_name);"
    )
    payload = fix01_payload(action="propose")
    payload["editor"]["text"] = RETAINED_PROPOSAL.split("EXCEPTION")[0] + "END salary_for;"
    payload["attachments"] = []
    provider, events = run(
        client,
        developer,
        payload,
        f"```sql\n{first}\n```",
        f"```sql\n{fixed}\n```",
    )
    contract = next(data for kind, data in events if kind == "edit_contract")
    assert contract["repair"] == "repaired"
    assert contract["anchorRepair"]
    assert len(provider.messages) == 2
    assert next(data for kind, data in events if kind == "proposal")["proposedText"] == fixed


def test_retained_application_call_sentence_verbatim() -> None:
    assert application_error_claims(
        "Once the owner of the called unit is known, read its body in full and find the "
        "`RAISE_APPLICATION_ERROR(20001, 'destination unavailable')`; if that handler hides "
        "an underlying error, the fix there is to log it and re-raise it."
    )
