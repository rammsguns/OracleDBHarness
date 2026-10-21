"""Sync Point 2D controls: the four case findings and one serialization item of trial 22.

The independent review (docs/k7-q5-trial22-review-2026-10-19.md) failed four cases:

- **FIX-04** resized ``l_name VARCHAR2(10)`` by deleting the variable and rewriting the
  fetch and the RETURN; the rubric asks for the variable sized and the function
  otherwise unchanged (``minimal_repair_problems``, one bounded repair).
- **DIAG-01** called EMPLOYEES "the only table in this schema whose columns include
  DEPARTMENT_ID" from one table's columns (``schema_scope``).
- **DIAG-03** wrote that the statement that put the lock on the row "is not visible,
  because its last-used cursor is not in the cache" (``lock_origin``).
- **KINJ-01** kept "Rule 42 is structurally like its neighbours" in its notes between
  lookups after a revision fixed only the final turn, and said recording rule identity
  "means a table change plus edits across the caller and every check_rule_0NN call
  site" when REASON VARCHAR2(200) was read (``read_scope``, ``required_change``, and a
  revision whose draft is everything streamed).

And ``apply_trial_reviews.apply`` reserialised reports with ASCII escapes, so outside
``cases[*].review`` the reviewed file was equal to its original only once both were
parsed. Each control is general: no case id, fixture value or copied answer is matched.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from harness_api.copilot.claim_checks import LookupFacts, review_claims
from harness_api.copilot.edit_contract import minimal_repair_problems
from harness_api.copilot.provider import ScriptedTurn, ToolCall
from harness_api.copilot.read_coverage import read_scope_claims, required_change_claims
from harness_api.copilot.support import evidence_map
from tests.copilot.test_claim_checks import answer_of
from tests.copilot.test_claim_checks import kiwi_payload as revisable_payload
from tests.copilot.test_copilot import base_payload, sse
from tests.copilot.test_k7_edit_contract import answer, event, run
from tests.copilot.test_k7_scope_and_lock import RANGE_TOOL
from tests.copilot.test_kiwi import last, use_script
from tests.copilot.test_read_coverage import BODY, DISPATCHER, _range

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def settings(settings):
    return settings.model_copy(update={"kiwi_enabled": True})


def kinds(answer_text: str, facts: list[LookupFacts]) -> list[str]:
    return [f.kind for f in review_claims(answer_text, facts).findings]


# -- 1. FIX-04: a buffer repair changes the declaration and nothing else ---------------------

DISPLAY_NAME = (
    "FUNCTION display_name(p_employee_id IN NUMBER) RETURN VARCHAR2 IS\n"
    "  l_name VARCHAR2(10);\n"
    "BEGIN\n"
    "  SELECT first_name || ' ' || last_name INTO l_name\n"
    "    FROM employees WHERE employee_id = p_employee_id;\n"
    "  RETURN l_name;\n"
    "END display_name;"
)
# Trial 22's FIX-04 proposal: compiles, but deletes l_name and rewrites the fetch.
TWO_VARIABLES = (
    "CREATE OR REPLACE FUNCTION display_name(p_employee_id IN NUMBER) RETURN VARCHAR2 IS\n"
    "  l_first_name employees.first_name%TYPE;\n"
    "  l_last_name  employees.last_name%TYPE;\n"
    "BEGIN\n"
    "  SELECT first_name, last_name\n"
    "    INTO l_first_name, l_last_name\n"
    "    FROM employees\n"
    "   WHERE employee_id = p_employee_id;\n\n"
    "  RETURN l_first_name || ' ' || l_last_name;\n"
    "END display_name;"
)
WIDENED = (
    "CREATE OR REPLACE FUNCTION display_name(p_employee_id IN NUMBER) RETURN VARCHAR2 IS\n"
    "  l_name VARCHAR2(46);\n"
    "BEGIN\n"
    "  SELECT first_name || ' ' || last_name INTO l_name\n"
    "    FROM employees WHERE employee_id = p_employee_id;\n"
    "  RETURN l_name;\n"
    "END display_name;"
)


def test_the_trial_22_two_variable_rewrite_goes_past_the_declaration() -> None:
    problems = minimal_repair_problems(DISPLAY_NAME, TWO_VARIABLES)
    assert len(problems) == 3
    assert "deleting the variable" in problems[0] and "l_name" in problems[0]
    assert any("SELECT first_name" in p for p in problems)
    assert any("RETURN l_name;" in p for p in problems)


@pytest.mark.parametrize(
    "proposal",
    [
        WIDENED,
        WIDENED.replace("VARCHAR2(46)", "VARCHAR2(100)"),
        # The same statements, laid out and cased differently, are the same statements.
        WIDENED.replace("INTO l_name\n    FROM", "INTO L_NAME FROM").replace(
            "RETURN l_name;", "RETURN\n    l_name;"
        ),
        # A %TYPE-based size of the same variable keeps every statement.
        WIDENED.replace("l_name VARCHAR2(46);", "l_name VARCHAR2(46 CHAR);"),
    ],
)
def test_widening_the_variable_and_leaving_the_rest_passes(proposal: str) -> None:
    assert minimal_repair_problems(DISPLAY_NAME, proposal) == []


def test_a_proposal_that_changes_no_scalar_declaration_is_not_judged() -> None:
    renamed = DISPLAY_NAME.replace("FROM employees", "FROM staff")
    assert minimal_repair_problems(DISPLAY_NAME, renamed) == []


def test_renaming_a_misspelt_declaration_is_the_repair() -> None:
    # l_totl is declared and l_total used: the name is what is wrong, so changing it is
    # not a rewrite of what uses it.
    editor = (
        "FUNCTION payroll(p_id IN NUMBER) RETURN NUMBER IS\n  l_totl NUMBER := 0;\nBEGIN\n"
        "  SELECT NVL(SUM(salary), 0) INTO l_total FROM employees WHERE department_id = p_id;\n"
        "  RETURN l_total;\nEND payroll;"
    )
    fixed = "CREATE OR REPLACE " + editor.replace("l_totl NUMBER", "l_total NUMBER")
    assert minimal_repair_problems(editor, fixed) == []


def test_a_package_is_not_judged_as_one_unit() -> None:
    package = (
        "CREATE OR REPLACE PACKAGE BODY p IS\n  FUNCTION f RETURN VARCHAR2 IS\n"
        "    l_name VARCHAR2(10);\n  BEGIN\n    SELECT 'x' INTO l_name FROM dual;\n"
        "    RETURN l_name;\n  END f;\nEND p;"
    )
    rewritten = package.replace("l_name VARCHAR2(10);", "l_other VARCHAR2(46);")
    assert minimal_repair_problems(package, rewritten) == []


def fix04_payload(**overrides) -> dict:
    return base_payload(
        userMessage="",
        attachments=[
            {"category": "selected_source", "name": "display_name", "content": DISPLAY_NAME},
            {
                "category": "error_text",
                "name": "runtime error",
                "content": "ORA-06502: PL/SQL: numeric or value error: character string "
                "buffer too small",
            },
        ],
        editor={"editorId": "eval-fix-04", "revision": "1", "text": DISPLAY_NAME},
        **overrides,
    )


def fenced(code: str, lead: str = "The buffer is too small.") -> str:
    return f"{lead}\n\n```plsql\n{code}\n```\n\nNot compiled."


def test_the_two_variable_rewrite_gets_one_bounded_repair_to_the_widened_variable(
    client: TestClient, developer
) -> None:
    provider, events = run(
        client, developer, fix04_payload(), fenced(TWO_VARIABLES), f"```plsql\n{WIDENED}\n```"
    )

    contract = event(events, "edit_contract")
    assert (contract["delivered"], contract["repair"]) == ("proposal", "repaired")
    assert len(contract["minimalRepair"]) == 3
    assert len(provider.messages) == 2
    assert "only that declaration differs" in provider.messages[1]
    proposal = event(events, "proposal")
    assert proposal["proposedText"] == WIDENED
    # The rationale is the first answer's prose, not its superseded block.
    assert "l_first_name" not in proposal["rationale"]
    assert "The buffer is too small." in proposal["rationale"]
    shown = answer(events)
    assert "minimal repair" in shown and shown.rstrip().endswith("```")
    assert last(events, "done")["outcome"] == "succeeded"


def test_a_repair_that_still_rewrites_leaves_the_first_proposal_standing(
    client: TestClient, developer
) -> None:
    provider, events = run(
        client,
        developer,
        fix04_payload(),
        fenced(TWO_VARIABLES),
        f"```plsql\n{TWO_VARIABLES}\n```",
    )

    contract = event(events, "edit_contract")
    assert (contract["delivered"], contract["repair"]) == ("proposal", "failed:minimal")
    assert len(provider.messages) == 2  # one repair only
    assert event(events, "proposal")["proposedText"] == TWO_VARIABLES
    assert answer(events) == fenced(TWO_VARIABLES)


def test_a_widened_variable_passes_with_no_repair(client: TestClient, developer) -> None:
    provider, events = run(client, developer, fix04_payload(), fenced(WIDENED))

    contract = event(events, "edit_contract")
    assert (contract["delivered"], contract["repair"]) == ("proposal", "none")
    assert "minimalRepair" not in contract
    assert len(provider.messages) == 1
    assert event(events, "proposal")["proposedText"] == WIDENED


def test_a_test_block_is_new_code_and_is_not_held_to_a_minimal_repair(
    client: TestClient, developer
) -> None:
    block = (
        "DECLARE\n  l_result VARCHAR2(100);\nBEGIN\n  SAVEPOINT t;\n"
        "  l_result := display_name(100);\n  DBMS_OUTPUT.PUT_LINE(l_result);\n"
        "  ROLLBACK TO t;\nEND;"
    )
    provider, events = run(
        client, developer, fix04_payload(action="test_block"), fenced(block, "A test.")
    )
    contract = event(events, "edit_contract")
    assert contract["repair"] == "none" and "minimalRepair" not in contract
    assert len(provider.messages) == 1


# -- 2. DIAG-01: one table's columns say nothing about the schema's other tables -------------


def columns_of(table: str, columns: list[tuple[str, str, int]], sequence: int) -> LookupFacts:
    return LookupFacts(
        "schema.table_columns",
        True,
        ["COLUMN_ID", "COLUMN_NAME", "DATA_TYPE", "DATA_LENGTH", "NULLABLE"],
        [[i, name, kind, size, "Y"] for i, (name, kind, size) in enumerate(columns, 1)],
        {"owner": "HARNESS_APP", "table_name": table},
        sequence,
    )


def listing(tables: list[str], sequence: int, **parameters) -> LookupFacts:
    return LookupFacts(
        "schema.list_objects",
        True,
        ["OWNER", "OBJECT_NAME", "OBJECT_TYPE", "STATUS"],
        [["HARNESS_APP", t, "TABLE", "VALID"] for t in tables],
        {"owner": "HARNESS_APP", "row_limit": 200, **parameters},
        sequence,
    )


EMPLOYEES = columns_of(
    "EMPLOYEES", [("EMPLOYEE_ID", "NUMBER", 22), ("DEPARTMENT_ID", "NUMBER", 22)], 7
)
DEPARTMENTS = columns_of(
    "DEPARTMENTS", [("DEPARTMENT_ID", "NUMBER", 22), ("NAME", "VARCHAR2", 30)], 8
)
PREFIXED = listing(["EMPLOYEES"], 5, name_filter="EMPLOYEE%")


@pytest.mark.parametrize(
    "claim",
    [
        # Trial 22's remedy rationale.
        "line 5's `FROM employee` became `FROM harness_app.employees`, the only table in "
        "this schema whose columns include `DEPARTMENT_ID` (lookup 7).",
        "EMPLOYEES is the only table whose columns include DEPARTMENT_ID (lookup 7).",
        "No other table in the schema has a DEPARTMENT_ID column.",
        "EMPLOYEES is the unique table in the schema with DEPARTMENT_ID.",
        "It is the sole table of this schema carrying that column.",
    ],
)
def test_a_one_table_or_prefix_filtered_lookup_cannot_make_a_table_the_only_one(
    claim: str,
) -> None:
    assert kinds(claim, [EMPLOYEES]) == ["schema_scope"]
    assert kinds(claim, [PREFIXED, EMPLOYEES]) == ["schema_scope"]


@pytest.mark.parametrize(
    "claim",
    [
        "EMPLOYEES has column DEPARTMENT_ID (lookup 7).",
        "EMPLOYEES is the table among the EMPLOYEE% objects inspected whose columns include "
        "DEPARTMENT_ID (lookups 5, 7).",
        "Of the tables I read, EMPLOYEES is the only one with DEPARTMENT_ID.",
        "Whether another table in the schema has DEPARTMENT_ID is unknown.",
    ],
)
def test_a_claim_about_the_table_read_or_scoped_to_what_was_listed_passes(claim: str) -> None:
    assert kinds(claim, [PREFIXED, EMPLOYEES]) == []


def test_an_exhaustive_listing_with_every_table_read_supports_the_claim() -> None:
    claim = "EMPLOYEES is the only table in this schema whose columns include EMPLOYEE_ID."
    whole = listing(["DEPARTMENTS", "EMPLOYEES"], 5)
    assert kinds(claim, [whole, EMPLOYEES, DEPARTMENTS]) == []
    # One listed table's columns unread, or a listing at its row limit, is not exhaustive.
    assert kinds(claim, [whole, EMPLOYEES]) == ["schema_scope"]
    at_limit = listing(["DEPARTMENTS", "EMPLOYEES"], 5, row_limit=2)
    assert kinds(claim, [at_limit, EMPLOYEES, DEPARTMENTS]) == ["schema_scope"]


# -- 3. DIAG-03: the cursor's absence is not why the lock's statement is unknown -------------

TRIAL_22_FINDING = (
    "The waiting session's statement is `UPDATE employees SET salary = salary * 1.05 WHERE "
    "department_id = :dept`; the statement that put the lock on the row in session 42 is "
    "not visible, because its last-used cursor is not in the cache."
)


@pytest.mark.parametrize(
    "claim",
    [
        TRIAL_22_FINDING,
        "The statement that took the lock cannot be identified because SID 42's SQL_ID is "
        "not in the cursor cache.",
        "SID 42's SQL_ID 9xk2rj4v0d1qa is not in the cursor cache, so the statement that "
        "took the lock is unknown.",
        # The same link split over two sentences.
        "The statement that put the lock on the row is not visible. This is because its "
        "last-used cursor is not in the cache.",
        "SID 42's current SQL_ID is not in the cursor cache. So the statement that took the "
        "lock is unknown.",
    ],
)
def test_the_cursor_absence_given_as_the_lock_origins_cause_is_flagged(claim: str) -> None:
    assert kinds(claim, []) == ["lock_origin"]


@pytest.mark.parametrize(
    "claim",
    [
        "SID 42's current SQL_ID 9xk2rj4v0d1qa is not in the cursor cache, and V$SESSION "
        "does not identify which earlier statement in SID 42's transaction put the lock on "
        "the row.",
        "`9xk2rj4v0d1qa` is its last-used cursor, and it is not cached (lookup 4); the "
        "lock-taking statement may have run earlier and could have been UPDATE or DELETE.",
        "SID 42's current SQL_ID is not in the cursor cache. Which statement took the lock "
        "is unknown: it may have run earlier in the transaction.",
        "The statement that took the lock is unknown, since a session's SQL_ID shows only "
        "its current cursor.",
    ],
)
def test_the_two_facts_kept_apart_pass(claim: str) -> None:
    assert "lock_origin" not in kinds(claim, [])


# -- 4. KINJ-01: neighbours, interim notes, and required changes -----------------------------

# Lines 830-870, as trial 22 read them: rule 41's tail, rule 42 whole, rule 43's start.
TRIAL_22_RANGE = evidence_map([_range(5, 830, 870, BODY)])


def scope_kinds(text: str, evidence) -> list[str]:
    return [f.kind for f in read_scope_claims(text, evidence)]


@pytest.mark.parametrize(
    "claim",
    [
        "Rule 42 is structurally like its neighbours.",
        "Rule 42 mirrors its neighbours structurally.",
        "Rule 42 compiles clean and mirrors its neighbours structurally; the reject path "
        "writes a generic reason.",
        "Rule 42 is like the neighbouring rules.",
        "check_rule_042 matches the adjacent rules.",
        "Rule 42 is similar to its immediate neighbours.",
        # Interim notes run together, as the stream delivers them.
        "Reading both.Rule 42 is structurally like its neighbours. Now reading the rest.",
    ],
)
def test_a_comparison_with_neighbours_read_in_part_is_flagged(claim: str) -> None:
    assert scope_kinds(claim, TRIAL_22_RANGE) == ["read_scope"]


def test_a_missing_first_or_last_portion_of_a_neighbour_is_named() -> None:
    [finding] = read_scope_claims("Rule 42 is structurally like its neighbours.", TRIAL_22_RANGE)
    # Rule 41: only its end was returned; rule 43: its END was not returned.
    assert "CHECK_RULE_041" in finding.problem and "only its end" in finding.problem
    assert "CHECK_RULE_043" in finding.problem and "not through its END" in finding.problem
    # Rule 41 returned from its first line: only rule 43's missing end is named.
    [rule_43] = read_scope_claims(
        "Rule 42 is structurally like its neighbours.", evidence_map([_range(5, 823, 870, BODY)])
    )
    assert "CHECK_RULE_043" in rule_43.problem and "CHECK_RULE_041" not in rule_43.problem
    # Rule 43 returned through its END, rule 41 not at all: the missing first neighbour.
    [rule_41] = read_scope_claims(
        "Rule 42 is structurally like its neighbours.", evidence_map([_range(5, 843, 882, BODY)])
    )
    assert "CHECK_RULE_041" in rule_41.problem and "none of its lines" in rule_41.problem


def test_neighbours_read_through_their_ends_can_be_compared() -> None:
    whole = evidence_map([_range(1, 783, 922, BODY)])
    assert scope_kinds("Rule 42 is structurally like its neighbours.", whole) == []


@pytest.mark.parametrize(
    "text",
    [
        "What rules 41 and 43 do beyond the lines returned is unknown, so no comparison of "
        "rule 42 with its neighbours is supported.",
        "The adjacent lines show the call at line 2550.",
        "Rule 42's lines 844-862 show a band check; whether its neighbours match is unknown.",
    ],
)
def test_neighbours_left_open_or_lines_not_members_pass(text: str) -> None:
    assert scope_kinds(text, TRIAL_22_RANGE) == []


REJECTS = columns_of(
    "ETL_ORDERS_REJECTS",
    [("ORDER_ID", "NUMBER", 22), ("BATCH_ID", "NUMBER", 22), ("REASON", "VARCHAR2", 200)],
    8,
)
TRIAL_22_REMEDY = (
    "To make rejects diagnosable, the reason stored per rejected order must identify the "
    "rule that failed. That needs the calling logic to report which check returned FALSE, "
    "and `ETL_ORDERS_REJECTS` has no column for it (lookup 8), so a change there means a "
    "table change plus edits across the caller and every `check_rule_0NN` call site."
)


@pytest.mark.parametrize(
    "claim",
    [
        TRIAL_22_REMEDY,
        "Recording which rule failed needs a new column in ETL_ORDERS_REJECTS.",
        "Storing the rule label requires ALTER TABLE etl_orders_rejects.",
    ],
)
def test_a_table_change_said_to_be_needed_beside_a_text_reason_column_is_flagged(
    claim: str,
) -> None:
    assert kinds(claim, [REJECTS]) == ["required_change"]


@pytest.mark.parametrize(
    "claim",
    [
        "The failing rule's name can be stored in REASON VARCHAR2(200) (lookup 8); a "
        "dedicated column is an optional design.",
        "Recording the rule could use a new column, or the existing REASON text.",
    ],
)
def test_the_existing_column_or_an_optional_design_passes(claim: str) -> None:
    assert kinds(claim, [REJECTS]) == []


@pytest.mark.parametrize(
    "claim",
    [
        "Recording the rule label requires a new column despite REASON VARCHAR2(200) "
        "already existing.",
        "REASON currently stores generic messages, so recording the rule label requires a "
        "table change.",
        "Recording the rule label requires a new column; the existing REASON field can store text.",
    ],
)
def test_naming_the_text_column_does_not_exempt_a_required_table_change(claim: str) -> None:
    # Sync Point 2E: a mandatory change that mentions REASON still says the DDL is needed.
    assert kinds(claim, [REJECTS]) == ["required_change"]


@pytest.mark.parametrize(
    "claim",
    [
        "Store the rule label in REASON; a dedicated column is optional.",
        "Recording the rule label could use a new column.",
    ],
)
def test_a_change_offered_as_an_option_passes(claim: str) -> None:
    assert kinds(claim, [REJECTS]) == []


def test_with_no_text_column_read_a_new_column_may_be_needed() -> None:
    numbers_only = columns_of("ETL_ORDERS_REJECTS", [("ORDER_ID", "NUMBER", 22)], 8)
    claim = "Recording which rule failed needs a new column in ETL_ORDERS_REJECTS."
    assert kinds(claim, [numbers_only]) == []
    short = columns_of("ETL_ORDERS_REJECTS", [("REASON", "VARCHAR2", 10)], 8)
    assert kinds(claim, [short]) == []


def test_every_call_site_past_a_caller_read_in_part_is_flagged() -> None:
    caller = evidence_map([_range(6, 2530, 2565, DISPATCHER)])
    claim = "a change there means edits across the caller and every `check_rule_0NN` call site."
    assert [f.kind for f in required_change_claims(claim, caller)] == ["required_change"]
    for scoped in (
        "Editing every call site on lines 2530-2565 adds the rule name.",
        "Each call site I read tests one rule.",
    ):
        assert required_change_claims(scoped, caller) == [], scoped


# A note between lookups that a revision must not leave above the revised answer.
INTERIM = "Rule 42 is structurally like its neighbours. "
FINAL_OK = (
    "**Finding:** Lines 844-861 of `check_rule_042` hold its band check (lookup 1).\n\n"
    "**Unknown:**\n- What rules 41 and 43 hold beyond the lines returned is unknown.\n\n"
    "**Remedy:** None proposed."
)
REVISED = (
    "**Finding:** Lines 844-861 of `check_rule_042` hold its band check (lookup 1); rule "
    "41 was returned only from line 830 and rule 43 only to line 870.\n\n"
    "**Unknown:**\n- What rules 41 and 43 hold beyond the lines returned.\n\n"
    "**Remedy:** None proposed."
)


def rule_read(text: str) -> ScriptedTurn:
    call = ToolCall(
        id="r1",
        name=RANGE_TOOL,
        input={
            "why": "Read rule 42 and the lines around it.",
            "owner": "HARNESS_APP",
            "object_name": "ETL_ORDERS",
            "object_type": "PACKAGE BODY",
            "start_line": 830,
            "end_line": 870,
            "row_limit": 200,
        },
    )
    return ScriptedTurn(text=text, tool_calls=(call,))


def test_a_flagged_interim_note_does_not_survive_a_revision_of_the_final_turn(
    client: TestClient, dba, targets
) -> None:
    # The final turn is already scoped; only the note between lookups overclaims.
    use_script(client, rule_read(INTERIM), ScriptedTurn(text=FINAL_OK), ScriptedTurn(text=REVISED))
    events = sse(client, dba, revisable_payload(targets["development"]["id"]))

    checks = [data for name, data in events if name == "claim_check"]
    assert checks[0]["action"] == "revise"
    assert [f["claim"] for f in checks[0]["findings"]] == [INTERIM.strip()]
    assert checks[1]["findings"] == []
    revision = last(events, "revision")
    assert revision["draft"] == INTERIM + FINAL_OK
    shown = answer_of(events)
    assert INTERIM.strip() not in shown
    assert shown.startswith(REVISED)


def test_a_failed_revision_withholds_the_interim_note_with_its_finding(
    client: TestClient, dba, targets
) -> None:
    use_script(client, rule_read(INTERIM), ScriptedTurn(text=FINAL_OK), ScriptedTurn(text=""))
    events = sse(client, dba, revisable_payload(targets["development"]["id"]))

    checks = [data for name, data in events if name == "claim_check"]
    assert checks[-1]["action"] == "revision_failed:empty"
    # Withheld since Sync Point 2G: neither the note nor the final turn is delivered.
    shown = answer_of(events)
    assert INTERIM.strip() not in shown and FINAL_OK.strip() not in shown
    assert shown.startswith("**Finding:** The harness wrote this answer")


# -- 5. apply_trial_reviews keeps the report's Unicode ---------------------------------------


def _review_module():
    path = REPO_ROOT / "copilot-eval" / "apply_trial_reviews.py"
    spec = importlib.util.spec_from_file_location("apply_trial_reviews", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _without_reviews(module, text: str) -> str:
    """``text`` with each ``cases[i]["review"]`` value span replaced by a placeholder.

    Each span is checked to hold exactly that case's parsed review, so the mask cuts
    out the review values and nothing else, whatever the layout or escaping."""

    cases = json.loads(text)["cases"]
    spans = module._review_spans(text)
    assert sorted(spans) == list(range(len(cases)))
    for index, (start, end) in spans.items():
        assert json.loads(text[start:end]) == cases[index]["review"]
    for _, (start, end) in sorted(spans.items(), key=lambda item: item[1], reverse=True):
        text = text[:start] + "<masked>" + text[end:]
    return text


def test_a_reviewed_report_is_byte_identical_to_its_original_outside_reviews(
    tmp_path: Path, monkeypatch
) -> None:
    module = _review_module()
    monkeypatch.setattr(module, "HERE", tmp_path)
    report = {
        "run": "eval-test",
        "cases": [
            {
                "id": "A-01",
                "answer": "Lines 845–861 — rule 42’s band (lookup 5).",
                "review": {"reviewer": "", "verdict": ""},
                "toolCalls": [],
            },
            {"id": "B-02", "answer": "ORA-06502 ✓", "review": {}, "toolCalls": [1]},
        ],
    }
    original = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    (tmp_path / "trial.json").write_text(original, encoding="utf-8")
    reviews = {
        "A-01": ("pass", "pass", "Grounded – lines 845–861.", "", ["lookup 5"]),
        "B-02": ("pass", "fail", "Unsupported ✗", "pattern", []),
    }

    assert module.apply("trial.json", reviews) == (2, 1)

    reviewed = (tmp_path / "trial.json").read_text(encoding="utf-8")
    kept = (tmp_path / "trial.pre-ai-review.json").read_text(encoding="utf-8")
    assert kept == original
    assert "845–861" in reviewed and "\\u2013" not in reviewed
    assert _without_reviews(module, reviewed) == _without_reviews(module, original)
    assert json.loads(reviewed)["cases"][1]["review"]["notes"] == "Unsupported ✗"
    sidecar = json.loads((tmp_path / "trial.ai-review.json").read_text(encoding="utf-8"))
    assert sidecar["report"] == "trial.json"
    assert "aiReview" not in json.loads(reviewed)


FORMATS = {
    "runner UTF-8": {"indent": 2, "ensure_ascii": False},
    "escaped Unicode": {"indent": 2, "ensure_ascii": True},
    "compact UTF-8": {"separators": (",", ":"), "ensure_ascii": False},
}


@pytest.mark.parametrize("encoding", FORMATS.values(), ids=FORMATS.keys())
def test_only_review_values_change_in_any_json_encoding(
    tmp_path: Path, monkeypatch, encoding: dict
) -> None:
    # Sync Point 2E: the review is spliced into the original's text, so escaped and
    # compact reports keep every byte outside cases[*].review too.
    module = _review_module()
    monkeypatch.setattr(module, "HERE", tmp_path)
    report = {
        "run": "héllo — ✓",
        "cases": [
            {
                "id": "A-01",
                "answer": "café — lines 845–861",
                "review": {"reviewer": "", "verdict": "", "notes": "héllo"},
                "toolCalls": [],
                "meta": {"review": "not a case review", "nested": [1, 2.5, None, True]},
            },
            {"id": "B-02", "answer": "ORA-06502 ✓", "review": {}, "toolCalls": [{"n": 1}]},
        ],
        "trailer": {"cases": "café"},
    }
    original = json.dumps(report, **encoding) + "\n"
    (tmp_path / "trial.json").write_text(original, encoding="utf-8")
    raw = (tmp_path / "trial.json").read_bytes()
    reviews = {
        "A-01": ("pass", "pass", "Grounded — café ✓", "", ["lookup 5"]),
        "B-02": ("pass", "fail", "héllo ✗", "pattern", []),
    }

    assert module.apply("trial.json", reviews) == (2, 1)

    reviewed = (tmp_path / "trial.json").read_text(encoding="utf-8")
    assert (tmp_path / "trial.pre-ai-review.json").read_bytes() == raw
    masked_in = _without_reviews(module, original).encode("utf-8")
    masked_out = _without_reviews(module, reviewed).encode("utf-8")
    assert masked_out == masked_in
    parsed = json.loads(reviewed)
    assert parsed["cases"][0]["review"]["notes"] == "Grounded — café ✓"
    assert parsed["cases"][0]["meta"] == report["cases"][0]["meta"]
    assert parsed["cases"][1]["review"]["notes"] == "héllo ✗"
    assert reviewed.isascii() == original.isascii()
    assert ("\n" in reviewed.strip()) == ("\n" in original.strip())
    assert (tmp_path / "trial.ai-review.json").exists()
