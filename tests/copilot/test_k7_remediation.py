"""K-7 remediation (docs/k7-remediation-plan.md): complete proposals and kept evidence.

Two things are checked here with the fixture provider. First, that a proposal is looked
at as the whole unit it replaces, and that a fragment is reported rather than called
compilable. Second, that the opt-in evidence capture keeps what the model was shown,
bounded and explicit about what is missing, and does nothing when it is off. The prompt
rules for Oracle code are checked for what they say, not for a canned answer.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from harness_api.copilot.context import KIWI_SYSTEM_PROMPT, SYSTEM_PROMPT
from harness_api.copilot.proposal import check_proposal, fragments
from harness_api.copilot.provider import FakeProvider, ScriptedTurn
from harness_api.copilot.toolbox import ToolOutcome
from tests.copilot.eval.cases import REVIEWED_CATEGORIES
from tests.copilot.test_copilot import base_payload, sse
from tests.copilot.test_kiwi import STATUS, kiwi_payload, status_call, use_script
from tests.copilot.test_kiwi import settings as settings  # Kiwi on, evidence capture off

REPO_ROOT = Path(__file__).resolve().parents[2]

# -- the structural check ---------------------------------------------------------------

STANDALONE = "CREATE OR REPLACE FUNCTION f RETURN NUMBER IS BEGIN RETURN 1; END f;"


@pytest.mark.parametrize(
    "text",
    [
        "FUNCTION f RETURN NUMBER IS BEGIN RETURN 1; END f;",
        "procedure p is begin null; end p;",
        "-- fixed\nFUNCTION f RETURN NUMBER IS BEGIN RETURN 1; END f;",
        "PRAGMA AUTONOMOUS_TRANSACTION;",
    ],
)
def test_a_bare_member_is_a_fragment_and_is_never_called_compilable(text: str) -> None:
    result = check_proposal(text)
    assert result.complete is False
    assert result.kind == "fragment"
    assert "CREATE OR REPLACE" in result.problems[0]
    assert result.as_dict()["compiled"] is False
    assert "Nothing was compiled" in result.as_dict()["note"]


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        (STANDALONE, "plsql_source"),
        (
            "CREATE OR REPLACE PACKAGE BODY p AS\nPROCEDURE a IS BEGIN NULL; END a;\nEND p;",
            "plsql_source",
        ),
        ("CREATE OR REPLACE PACKAGE p AS PROCEDURE a; END p;", "plsql_source"),
        ("DECLARE v NUMBER; BEGIN v := 1; END;", "plsql_block"),
        ("SELECT * FROM employees WHERE employee_id = :id", "query"),
        ("WITH x AS (SELECT 1 FROM dual) SELECT * FROM x", "query"),
        ("CREATE OR REPLACE VIEW v AS SELECT 1 AS a FROM dual", "ddl"),
        ("UPDATE employees SET salary = salary WHERE 1 = 0", "dml"),
    ],
)
def test_whole_statements_and_units_pass_without_being_called_compiled(
    text: str, kind: str
) -> None:
    result = check_proposal(text)
    assert result.complete is True, result.problems
    assert result.kind == kind
    assert result.as_dict()["compiled"] is False


def test_a_package_member_inside_its_package_is_not_flagged_for_its_inner_functions() -> None:
    body = (
        "CREATE OR REPLACE PACKAGE BODY pkg AS\n"
        "  FUNCTION helper RETURN NUMBER IS BEGIN RETURN 1; END helper;\n"
        "END pkg;"
    )
    assert check_proposal(body).complete is True


def test_a_property_after_is_is_reported() -> None:
    text = "CREATE OR REPLACE PROCEDURE p IS AUTHID CURRENT_USER BEGIN NULL; END p;"
    result = check_proposal(text)
    assert result.complete is False
    assert "before IS/AS" in result.problems[0]
    ok = "CREATE OR REPLACE PROCEDURE p AUTHID CURRENT_USER IS BEGIN NULL; END p;"
    assert check_proposal(ok).complete is True


def test_unclosed_or_multiple_statements_are_reported_not_prepared() -> None:
    assert check_proposal("SELECT 1 FROM dual; SELECT 2 FROM dual").complete is False
    assert check_proposal("Here is the fix, replace the name").complete is False
    assert check_proposal("").complete is False


@pytest.mark.parametrize(
    "text",
    ["BEGIN NULL; END SELECT 1 FROM dual;", "BEGIN NULL; END 5;", "BEGIN NULL; END a b;"],
)
def test_only_an_optional_name_may_sit_between_the_final_end_and_its_semicolon(
    text: str,
) -> None:
    result = check_proposal(text)
    assert result.complete is False
    assert "optional unit name" in result.problems[0]


@pytest.mark.parametrize(
    "text",
    [
        "BEGIN NULL; END;",
        "CREATE OR REPLACE PROCEDURE p IS BEGIN NULL; END p;",
        'CREATE OR REPLACE PACKAGE BODY "My Pkg" AS END "My Pkg";',
        "CREATE OR REPLACE PROCEDURE p IS BEGIN FOR i IN 1 .. 3 LOOP NULL; END LOOP; END p;",
    ],
)
def test_named_and_quoted_endings_stay_complete(text: str) -> None:
    assert check_proposal(text).complete is True


def test_fragments_reports_sql_with_several_statements_but_not_error_text() -> None:
    found = fragments(
        [
            "SELECT 1 FROM dual; SELECT 2 FROM dual;",
            "ORA-00942: table or view does not exist",
            "SELECT 1 FROM dual",
            "SET SERVEROUTPUT ON;\nBEGIN NULL; END;",
        ]
    )
    assert [f["block"] for f in found] == [1, 4]
    assert "More than one statement" in found[0]["problems"][0]


def test_fragments_reports_only_recognisable_fragments_in_an_answer() -> None:
    found = fragments(
        [
            "ORA-00942: table or view does not exist",  # quoted error text, unlabelled
            "FUNCTION f RETURN NUMBER IS BEGIN RETURN 1; END f;",
            STANDALONE,
        ]
    )
    assert [f["block"] for f in found] == [2]


# -- proposals through the service -------------------------------------------------------


def test_a_fragment_proposal_carries_a_validation_that_says_it_is_incomplete(
    client: TestClient, developer
) -> None:
    events = _run_plain(
        client,
        developer,
        "The table is EMPLOYEES.\n\n```plsql\nFUNCTION headcount RETURN NUMBER IS\nBEGIN RETURN 1; END headcount;\n```",
    )
    proposal = next(d for n, d in events if n == "proposal")
    assert proposal["validation"]["complete"] is False
    assert proposal["validation"]["compiled"] is False
    assert "code_check" not in [n for n, _ in events]


def test_a_complete_proposal_validates(client: TestClient, developer) -> None:
    events = _run_plain(client, developer, "Renamed.\n\n```plsql\n" + STANDALONE + "\n```")
    proposal = next(d for n, d in events if n == "proposal")
    assert proposal["validation"]["complete"] is True
    assert proposal["validation"]["problems"] == []


def test_a_sql_block_that_only_quotes_the_source_is_not_the_proposal(
    client: TestClient, developer
) -> None:
    # Seen in a live run: the faulty line quoted in a sql block before the fix.
    quoted = "SELECT COUNT(*) INTO l_count FROM employee\n  WHERE department_id = p_department_id;"
    events = _run_plain(
        client,
        developer,
        f"The line is:\n\n```sql\n{quoted}\n```\n\nThe fix:\n\n```plsql\n{STANDALONE}\n```",
    )
    proposal = next(d for n, d in events if n == "proposal")
    assert proposal["proposedText"] == STANDALONE
    assert quoted in proposal["rationale"]  # the quote stays with its explanation


def test_an_answer_that_only_quotes_offers_no_proposal(client: TestClient, developer) -> None:
    events = _run_plain(
        client, developer, "```sql\nPL/SQL: ORA-00942: table or view does not exist\n```"
    )
    assert "proposal" not in [n for n, _ in events]
    quoted_line = (
        "SELECT COUNT(*) INTO l_count FROM employee WHERE department_id = p_department_id;"
    )
    events = _run_plain(client, developer, f"The line:\n\n```sql\n{quoted_line}\n```")
    assert "proposal" not in [n for n, _ in events]


def _editor_payload(text: str, attachment: str | None = None) -> dict:
    payload = base_payload()
    payload["editor"]["text"] = text
    if attachment is not None:
        payload["attachments"].append(
            {
                "category": "selected_source",
                "name": "reference",
                "content": attachment,
                "provenance": "user supplied",
            }
        )
    return payload


def test_a_deletion_that_is_part_of_the_original_is_still_the_proposal(
    client: TestClient, developer
) -> None:
    # The review's reproduction: the replacement is contained in the old editor text.
    payload = _editor_payload("BEGIN BEGIN NULL; END; END;")
    events = _run_plain(
        client, developer, "Replace the editor with:\n\n```sql\nBEGIN NULL; END;\n```", payload
    )
    proposal = next(d for n, d in events if n == "proposal")
    assert proposal["proposedText"].strip() == "BEGIN NULL; END;"

    # Quoting the redundant line first does not displace the deletion.
    events = _run_plain(
        client,
        developer,
        "The outer block adds nothing:\n\n```sql\nBEGIN NULL; END;\n```\n\nSo:\n\n"
        "```sql\nBEGIN\n  NULL;\nEND;\n```",
        payload,
    )
    proposal = next(d for n, d in events if n == "proposal")
    assert _squash(proposal["proposedText"]) == "BEGIN NULL; END;"


def test_a_fix_already_in_an_attachment_is_still_the_proposal(
    client: TestClient, developer
) -> None:
    fixed = STANDALONE.replace("RETURN 1", "RETURN 2")
    original = STANDALONE
    payload = _editor_payload(original, attachment=f"-- the fix from the ticket\n{fixed}")
    events = _run_plain(
        client,
        developer,
        "The error is:\n\n```sql\nPL/SQL: ORA-00942: table or view does not exist\n```\n\n"
        f"Use the ticket's version:\n\n```sql\n{fixed}\n```",
        payload,
    )
    proposal = next(d for n, d in events if n == "proposal")
    assert proposal["proposedText"].strip() == fixed
    # Repeating the editor unchanged is no change, so no proposal.
    events = _run_plain(client, developer, f"```sql\n{original}\n```", payload)
    assert "proposal" not in [n for n, _ in events]


def test_a_later_example_never_displaces_the_replacement(client: TestClient, developer) -> None:
    # The round-4 review's reproduction: a deletion followed by a fresh example call.
    payload = _editor_payload("BEGIN BEGIN NULL; END; END;")
    events = _run_plain(
        client,
        developer,
        "Replace the editor with:\n\n```sql\nBEGIN NULL; END;\n```\n\n"
        "Example call:\n\n```sql\nBEGIN demo; END;\n```",
        payload,
    )
    proposal = next(d for n, d in events if n == "proposal")
    assert proposal["proposedText"].strip() == "BEGIN NULL; END;"

    # A fix already in an attachment, then an example.
    fixed = STANDALONE.replace("RETURN 1", "RETURN 2")
    payload = _editor_payload(STANDALONE, attachment=f"-- the fix from the ticket\n{fixed}")
    events = _run_plain(
        client,
        developer,
        f"Use the ticket's version:\n\n```sql\n{fixed}\n```\n\n"
        "Then call it:\n\n```sql\nBEGIN DBMS_OUTPUT.PUT_LINE(headcount); END;\n```",
        payload,
    )
    proposal = next(d for n, d in events if n == "proposal")
    assert proposal["proposedText"].strip() == fixed

    # Quoted error and quoted faulty line, then the replacement, then an example.
    quoted = "SELECT COUNT(*) INTO l_count FROM employee WHERE department_id = p_department_id;"
    events = _run_plain(
        client,
        developer,
        "```sql\nPL/SQL: ORA-00942: table or view does not exist\n```\n\n"
        f"The line:\n\n```sql\n{quoted}\n```\n\nThe fix:\n\n```plsql\n{STANDALONE}\n```\n\n"
        "Example:\n\n```sql\nSELECT headcount(10) FROM dual\n```",
    )
    proposal = next(d for n, d in events if n == "proposal")
    assert proposal["proposedText"] == STANDALONE


def _squash(text: str) -> str:
    return " ".join(text.split())


def test_a_fragment_in_an_answer_with_no_editor_is_flagged(client: TestClient, developer) -> None:
    payload = base_payload()
    del payload["editor"]
    events = _run_plain(
        client,
        developer,
        "Use this:\n\n```sql\nPROCEDURE transform(p_id NUMBER) IS BEGIN NULL; END transform;\n```",
        payload,
    )
    assert "proposal" not in [n for n, _ in events]
    check = next(d for n, d in events if n == "code_check")
    assert check["fragments"][0]["kind"] == "fragment"


def _run_plain(client: TestClient, developer, text: str, payload: dict | None = None):
    use_script_plain(client, text)
    return sse(client, developer, payload or base_payload())


class _SaysProvider(FakeProvider):
    """The fixture provider, answering with exactly the text it is given."""

    def __init__(self, text: str) -> None:
        super().__init__()
        self._text = text

    async def stream(self, system: str, user_message: str):
        yield self._text


def use_script_plain(client: TestClient, text: str) -> None:
    client.app.state.harness.copilot._provider = _SaysProvider(text)


# -- the prompt's rules, checked for what they say ---------------------------------------


def test_both_prompts_carry_the_code_rules() -> None:
    for prompt in (SYSTEM_PROMPT, KIWI_SYSTEM_PROMPT):
        assert "CREATE OR REPLACE" in prompt
        assert "FETCH cur BULK COLLECT INTO" in prompt
        assert "LIMIT belongs to FETCH" in prompt
        assert "coll.COUNT = 0" in prompt  # the last partial batch is processed
        assert "SAVEPOINT" in prompt and "ROLLBACK TO" in prompt
        assert "narrowest direct object grant" in prompt
        assert "AUTHID CURRENT_USER is not a way around" in prompt
        assert "DBMS_SCHEDULER.DISABLE" in prompt
        assert "DBMS_ROWID" in prompt
        assert "re-raise" in prompt
        assert "AWR, ASH or SQL Monitor" in prompt  # named only to rule it out
        assert "never copy its code into" in prompt  # a test block calls the stored unit
        assert "scalar subqueries included, are allowed only inside SQL statements" in prompt
        assert "a RETURN or an assignment" in prompt
        assert "SET SERVEROUTPUT ON" in prompt  # named only to keep it out of proposals
        assert "SELECT cannot be granted on a procedure" in prompt
        assert "a package body only with DEBUG" in prompt
        assert "FORALL takes exactly one INSERT, UPDATE, DELETE or MERGE" in prompt
        assert "a key that matches no row as well as one that does" in prompt
        assert "handle TOO_MANY_ROWS explicitly" in prompt
        assert "pick one that cannot occur" in prompt  # instead of MAX(id)+1
        # Trial 3 TEST-01 printed success whatever its flags said (C12).
        assert "never a fixed success message" in prompt
        assert "a grant on ALL_SOURCE itself shows nothing more" in prompt
        assert "RAISE_APPLICATION_ERROR when the counter is not zero" in prompt
        # Trial 4: TEST-01 hid a check behind an IF and a dead NO_DATA_FOUND handler, and
        # had no handler for an unexpected error (C12).
        assert "no IF hides a check without an ELSE" in prompt
        assert "never raises NO_DATA_FOUND" in prompt
        assert "WHEN OTHERS THEN print the error with DBMS_OUTPUT, ROLLBACK TO" in prompt
        # Trial 4's grounding failures, each a general Oracle fact (DIAG-02/03, KINJ-01,
        # DIAG-01), and the rule against side remarks the answer does not need.
        assert "MAX_FAILURES NULL means failures never disable the job" in prompt
        assert "DBMS_SESSION acts on the caller's own" in prompt
        assert "ID1 and ID2 identify an undo" in prompt
        assert "read consistency" in prompt
        assert "do not count characters in the source" in prompt
        assert "must test exactly what its labels" in prompt
        assert "leave out the ones the answer can do without" in prompt
        # Trial 5: INJECTION-01 offered a grant alone for an unqualified name, DIAG-01
        # called an ORA-00942 statement unparsable, DIAG-03 read a logon time as a
        # transaction's age and KINJ-01 counted rules past the lines it read.
        assert "A grant alone does not make an unqualified" in prompt
        assert "not that the statement failed to parse" in prompt
        assert "A session's logon time is not when its transaction began" in prompt
        assert "may hold more than the lines you saw" in prompt
        # Trial 5 EXPLAIN-06 listed column mismatches among errors WHEN OTHERS catches.
        # Trials 8-9 did it again; round 9 asked to keep the runtime cases, so the rule
        # is scoped to definer's rights and names what a handler can catch.
        assert "In a definer's-rights unit (the default), static SQL" in prompt
        assert "the unit's own handler never sees it either" in prompt
        assert "resolves names and checks privileges again at run time" in prompt
        assert "list only errors that can be raised while its code runs" in prompt


def test_kiwi_answers_are_reread_as_a_reviewer_would() -> None:
    # Trial 6: each failure was one wrong side detail in a correct diagnosis (DIAG-02,
    # DIAG-03, KINJ-01, KINJ-02). One general reread, not a rule per wording.
    assert "reread your draft as a reviewing DBA would" in KIWI_SYSTEM_PROMPT
    assert "called the same unresolved thing everywhere" in KIWI_SYSTEM_PROMPT
    assert "every value the declared type allows" in KIWI_SYSTEM_PROMPT
    assert "agrees with the evidence it explains" in KIWI_SYSTEM_PROMPT
    assert "better left out than guessed" in KIWI_SYSTEM_PROMPT
    # Round 14 (C08): ordinary answers are reread too, but without lookups there is no
    # lookup evidence to cite, so the citation checklist stays Kiwi's.
    assert "reread your draft as a reviewing DBA would. A statement of how" in SYSTEM_PROMPT
    assert "names its lookup or lines" not in SYSTEM_PROMPT


def test_kiwi_cites_the_harness_lookup_number_and_writes_only_grounded_repairs() -> None:
    # Trial 7: DIAG-01 cited four lookups by the wrong numbers, every failing diagnosis
    # failed on an aside it did not need, and KINJ-01 replaced a package member with a
    # standalone procedure that called a helper it had not seen and kept the DELETE its
    # text said it removed.
    from harness_api.copilot.context import ACTION_INSTRUCTIONS

    assert 'the harness line "This is lookup N"' in KIWI_SYSTEM_PROMPT
    assert "a refused or invalid call counts too" in KIWI_SYSTEM_PROMPT
    diagnose = ACTION_INSTRUCTIONS["kiwi.diagnose"]
    assert "at most three next steps" in diagnose
    assert "Leave out background, asides and observations" in diagnose
    assert "never as a separate standalone unit" in diagnose
    assert "describe the change and say what you would need to read" in diagnose
    assert "does exactly what your text says it does" in diagnose


def test_the_default_rights_model_and_a_valid_specification_are_stated() -> None:
    # Trial 8: INJECTION-03 called invoker's rights the default; DIAG-01 explained a
    # VALID specification by contents it had not read (as in trials 2 and 7).
    from harness_api.copilot.context import ACTION_INSTRUCTIONS

    for prompt in (SYSTEM_PROMPT, KIWI_SYSTEM_PROMPT):
        assert "A unit with no AUTHID clause runs with definer's rights" in prompt
        assert "an owner needs no grant on its own objects" in prompt
    diagnose = ACTION_INSTRUCTIONS["kiwi.diagnose"]
    assert "compile errors never invalidate its specification" in diagnose


def test_the_round_10_oracle_facts_are_stated() -> None:
    # Trial 10: TEST-01 expected headcount(NULL) to count IS NULL rows; DIAG-01 read the
    # rights model from a body; DIAG-03 named a nonexistent DML_LOCK_TIMEOUT and an unread
    # statement; KINJ-01 called MOD non-negative and used constants from unread lines.
    from harness_api.copilot.context import ACTION_INSTRUCTIONS

    for prompt in (SYSTEM_PROMPT, KIWI_SYSTEM_PROMPT):
        assert "col = p_arg matches no row when p_arg is NULL" in prompt
        assert "A package's AUTHID clause is in its specification, never its body" in prompt
        assert "unless a lookup shows its statement, the operation is unknown" in prompt
        assert "DDL_LOCK_TIMEOUT applies to DDL waiting for a lock" in prompt
        assert "MOD(-1, 1042) = -1" in prompt
        assert "a value on a line no lookup returned is unknown" in prompt
    assert "a line you cite lies inside a range a lookup returned" in KIWI_SYSTEM_PROMPT
    assert "follows the order of the lines you read" in KIWI_SYSTEM_PROMPT
    assert "a line you cite lies inside" not in SYSTEM_PROMPT
    assert "has no bearing on a compile error" in ACTION_INSTRUCTIONS["kiwi.diagnose"]


def test_a_record_insert_and_a_partly_read_unit_are_stated() -> None:
    # Trial 11: DRAFT-03 wrote INSERT INTO t (cols) VALUES rec(i), which 19c rejects
    # (ORA-03001); KINJ-01 called a body read to line 2985 "~2,600 lines".
    for prompt in (SYSTEM_PROMPT, KIWI_SYSTEM_PROMPT):
        assert "A record insert, INSERT INTO t VALUES rec, takes no column list" in prompt
        assert "VALUES (rec.a, rec.b)" in prompt
        assert "a unit is at least as long as the highest line you read" in prompt
    sql = Path(REPO_ROOT, "oracle/qualification/05_k7_codegen_checks.sql").read_text()
    assert "record: INSERT with a column list and VALUES record is rejected" in sql


def test_kiwi_draws_no_conclusion_between_lookups_and_no_lock_timeout_is_invented() -> None:
    # Trial 12: DIAG-02 and KINJ-02 called ORDER_FEED a package in text written between
    # lookups, DIAG-02 called unread code "proven", DIAG-03 suggested a DML lock timeout.
    assert "Anything you write between lookups is shown" in KIWI_SYSTEM_PROMPT
    assert "draw no conclusion there" in KIWI_SYSTEM_PROMPT
    assert "used only for what a lookup shows" in KIWI_SYSTEM_PROMPT
    assert "between lookups" not in SYSTEM_PROMPT
    for prompt in (SYSTEM_PROMPT, KIWI_SYSTEM_PROMPT):
        # Sync Point 2N: no claim that it is the only lock timeout a session can set.
        assert "DDL_LOCK_TIMEOUT applies to DDL waiting for a lock, not to a DML" in prompt
        assert "only lock timeout" not in prompt
    sql = Path(REPO_ROOT, "oracle/qualification/05_k7_codegen_checks.sql").read_text()
    assert "lock: the only session-settable lock timeout is ddl_lock_timeout" in sql


def test_a_report_names_the_stand_in_it_ran_against(tmp_path: Path) -> None:
    # Round-13 review (C16): a corrected fixture changes what Kiwi's lookups return while
    # the case set stays the same, so each report records the stand-in's file hashes.
    from tests.copilot.eval import runner

    for name in runner.STAND_IN_FILES:
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text("one")
    first = runner.stand_in_fingerprint(tmp_path)
    assert set(first["files"]) == set(runner.STAND_IN_FILES)
    assert first["files"][runner.STAND_IN_FILES[0]] == hashlib.sha256(b"one").hexdigest()
    (tmp_path / runner.STAND_IN_FILES[0]).write_text("two")
    assert runner.stand_in_fingerprint(tmp_path)["sha256"] != first["sha256"]
    assert runner._harness_build()["standIn"] == runner.stand_in_fingerprint()


def test_the_trial_13_oracle_facts_are_stated() -> None:
    # Trial 13: DRAFT-03 wrote VALUES (rec), DIAG-01 explained a dependency row as left
    # over from an earlier body, DIAG-02 and KINJ-02 named the unit that raised ORA-20001
    # without reading it, DIAG-03 called KILL SESSION the only way, and KINJ-01 cited
    # rule 41's constants from lines it had not read.
    from harness_api.copilot.context import ACTION_INSTRUCTIONS

    for prompt in (SYSTEM_PROMPT, KIWI_SYSTEM_PROMPT):
        assert "no parentheses round the record (VALUES (rec) is a type error)" in prompt
        # Round 13: stated as unknown until read, not as a new categorical rule.
        assert "raised a logged error is unknown until you have read the line" in prompt
        assert "report that nothing was found, not what the name therefore is" in prompt
        assert "call either the only way" in prompt
        assert "only ALTER SYSTEM KILL SESSION" not in prompt
        assert "say only what the returned lines show of each" in prompt
    diagnose = ACTION_INSTRUCTIONS["kiwi.diagnose"]
    assert "Every compile" not in diagnose
    assert "report the disagreement under Unknown and do not explain it" in diagnose
    for section in ("Finding:", "Evidence:", "Unknown:", "Remedy:", "Next steps:"):
        assert section in diagnose


def test_kiwi_cites_the_lookup_number_the_harness_gives_and_keeps_diagnoses_short() -> None:
    # Trial 7: DIAG-01 cited four lookups by the wrong numbers, and every failing
    # diagnosis failed on an aside the diagnosis did not need.
    from harness_api.copilot.context import ACTION_INSTRUCTIONS

    assert 'the harness line "This is lookup N"' in KIWI_SYSTEM_PROMPT
    assert "a refused or invalid call counts too" in KIWI_SYSTEM_PROMPT
    diagnose = ACTION_INSTRUCTIONS["kiwi.diagnose"]
    assert "at most three next steps" in diagnose
    assert "Leave out background, asides and observations" in diagnose


def test_the_rules_the_prompt_states_are_in_the_oracle_checks() -> None:
    """Each trial-4 rule that states Oracle behaviour has a check in 05 that shows it."""

    sql = Path(REPO_ROOT, "oracle/qualification/05_k7_codegen_checks.sql").read_text()
    for name in (
        "SELECT MAX(...) INTO with no matching row gives NULL, not NO_DATA_FOUND",
        "an unexpected error is re-raised, drops the block''s row and keeps the caller''s",
        "rows deleted inside the loop are still fetched (read consistency)",
        "with MAX_FAILURES NULL a failed run leaves the job enabled",
        "DBMS_SESSION has no procedure that ends a session",
        "resolve: a direct grant does not make an unqualified name reach another schema",
        "resolve: with the grant, a private synonym resolves the unqualified name",
        "compile: a column-count mismatch in static SQL is a compile error",
        "package: the specification''s AUTHID is the package''s",
        "package: a body with an AUTHID clause is rejected",
        "arith: MOD(-1, 1042) = -1, the sign of the dividend",
        "null: col = a NULL argument matches no row, though a row IS NULL",
        "lock: there is no dml_lock_timeout parameter",
        "record: INSERT ... VALUES (record) in parentheses is rejected",
        "deps: a failed compile keeps no row for the table it no longer names",
        "fixture: the invalid EMPLOYEE_REPORT body depends on its spec, DBMS_OUTPUT and "
        "STANDARD only",
        "count: COUNT(DISTINCT) leaves out NULL",
        "fixture: the EMPLOYEE_REPORT body errors are ORA-00942 at 5:39",
        "fixture: FIX-01''s function errors are ORA-00942 at 4:37",
    ):
        assert name in sql, name
    assert "EXPECTED_CHECKS=68" in Path(REPO_ROOT, "scripts/k7_oracle_checks.sh").read_text()


def test_the_test_block_action_asks_for_every_outcome_and_cleanup() -> None:
    from harness_api.copilot.context import ACTION_INSTRUCTIONS

    text = ACTION_INSTRUCTIONS["test_block"]
    assert "roll back to it and re-raise" in text
    assert "including one whose setup found nothing to test" in text
    # Trial 5 TEST-01 put the block in a text fence, so no proposal was taken.
    assert "one ```sql fence, never ```text" in text


def test_the_diagnose_playbook_does_not_name_a_job_action_prefix_a_schema() -> None:
    from harness_api.copilot.context import ACTION_INSTRUCTIONS

    text = ACTION_INSTRUCTIONS["kiwi.diagnose"]
    # Trials 1-3 called ORDER_FEED (from JOB_ACTION ORDER_FEED.EXPORT_DAY) a schema.
    assert "not a schema named in its action" not in text
    assert "may be a schema or a package" in text and "do not call it either one" in text
    assert "an application error's message is the code's own words" in text


def test_the_test_block_action_asks_for_the_stored_unit_and_the_not_run_note() -> None:
    from harness_api.copilot.context import ACTION_INSTRUCTIONS

    text = ACTION_INSTRUCTIONS["test_block"]
    assert "rather than copying" in text
    assert "not run the block" in text


def test_the_date_rule_keeps_the_original_meaning_for_a_bind_with_a_time() -> None:
    # col >= :d AND col < :d + 1 AND :d = TRUNC(:d) matches nothing for a bind with a time
    # of day, as TRUNC(col) = :d does. Check that claim on plain datetimes.
    from datetime import datetime, timedelta

    def original(col: datetime, d: datetime) -> bool:
        return col.replace(hour=0, minute=0, second=0, microsecond=0) == d

    def rewrite(col: datetime, d: datetime) -> bool:
        midnight = d.replace(hour=0, minute=0, second=0, microsecond=0)
        return col >= d and col < d + timedelta(days=1) and d == midnight

    cols = [datetime(2026, 10, 16, h, m) for h in (0, 12, 23) for m in (0, 59)]
    for d in (datetime(2026, 10, 16), datetime(2026, 10, 16, 12), datetime(2026, 10, 17)):
        assert [original(c, d) for c in cols] == [rewrite(c, d) for c in cols]


def _batches(rows: int, limit: int) -> list[int]:
    """The loop the prompt prescribes: fetch LIMIT, exit on an empty batch, process each."""

    seen, position = [], 0
    while True:
        batch = rows - position if rows - position < limit else limit
        position += batch
        if batch == 0:
            break
        seen.append(batch)
    return seen


@pytest.mark.parametrize(
    ("rows", "limit", "expected"),
    [
        (0, 3, []),
        (1, 3, [1]),
        (3, 3, [3]),
        (4, 3, [3, 1]),
        (9, 3, [3, 3, 3]),
        (10, 3, [3, 3, 3, 1]),
    ],
)
def test_the_prescribed_bulk_loop_processes_every_row_including_the_last_partial_batch(
    rows: int, limit: int, expected: list[int]
) -> None:
    assert _batches(rows, limit) == expected
    assert sum(_batches(rows, limit)) == rows


def test_the_rule_band_example_is_evaluated_at_its_boundaries() -> None:
    assert (500 + 3) * 100 == 50300  # the example the prompt gives


# -- evidence capture --------------------------------------------------------------------


def _outcome(**overrides) -> ToolOutcome:
    base = dict(
        call_id="c1",
        tool_name=STATUS,
        operation_id="schema.object_source_range",
        status="succeeded",
        parameters={"owner": "HARNESS_APP", "object_name": "PKG", "start_line": 1},
        why="read it",
        row_count=2,
        result_bytes=100,
        content="----- BEGIN UNTRUSTED TOOL_RESULT x -----\nbody\n----- END -----",
        columns=["LINE", "TEXT"],
        rows=[[5, "a"], [9, "b"]],
        sequence=3,
    )
    base.update(overrides)
    return ToolOutcome(**base)


def test_evidence_records_the_lookup_its_lines_and_what_the_model_saw() -> None:
    item = _outcome().evidence(1000, target="prf_1")
    assert item["lookup"] == 3
    assert item["callId"] == "c1"
    assert item["target"] == "prf_1"
    assert item["schema"] == "HARNESS_APP"
    assert item["sourceLines"] == [5, 9]
    assert item["contentTruncated"] is False
    assert "body" in item["content"]
    assert "rows" not in item


def test_evidence_is_bounded_and_says_when_it_was_cut() -> None:
    item = _outcome(content="x" * 5000).evidence(100)
    assert len(item["content"].encode()) <= 100
    assert item["contentTruncated"] is True


def test_evidence_keeps_refusals_errors_and_lookup_truncation_explicit() -> None:
    refused = _outcome(
        status="refused", error_code="permission_denied", columns=[], rows=[], row_count=None
    ).evidence(1000)
    assert refused["status"] == "refused"
    assert refused["errorCode"] == "permission_denied"
    assert refused["sourceLines"] is None
    assert _outcome(truncated=True).evidence(1000)["lookupTruncated"] is True


def _kiwi_events(client, developer, targets):
    use_script(
        client,
        ScriptedTurn(text="", tool_calls=(status_call(),)),
        ScriptedTurn(text="Valid, per lookup 1."),
    )
    return sse(client, developer, kiwi_payload(targets["development"]["id"]))


def test_no_evidence_is_streamed_unless_the_evaluation_switches_it_on(
    client: TestClient, developer, targets
) -> None:
    events = _kiwi_events(client, developer, targets)
    assert "evidence" not in [n for n, _ in events]


def test_evidence_streams_beside_the_lookup_when_switched_on(
    client: TestClient, developer, targets
) -> None:
    service = client.app.state.harness.copilot
    service._settings = service._settings.model_copy(update={"kiwi_capture_evidence": True})
    events = _kiwi_events(client, developer, targets)
    evidence = [d for n, d in events if n == "evidence"]
    assert len(evidence) == 1
    item = evidence[0]
    result = next(d for n, d in events if n == "tool_result")
    assert item["callId"] == result["callId"]
    assert item["operationId"] == "schema.object_status"
    assert item["lookup"] == 1
    assert item["status"] == "succeeded"
    assert "UNTRUSTED TOOL_RESULT" in item["content"]
    # The ordinary tool_result event is unchanged: counts only, never rows or content.
    assert "content" not in result
    assert "rows" not in item


# --- C01: completeness semantics -------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "CREATE OR REPLACE FUNCTION f RETURN NUMBER IS",
        "BEGIN NULL;",
        "CREATE OR REPLACE PROCEDURE p IS BEGIN NULL; END; SELECT 1 FROM dual;",
        "CREATE OR REPLACE PROCEDURE p IS BEGIN NULL; END p",
        "CREATE OR REPLACE PROCEDURE p IS",
        "CREATE OR REPLACE PACKAGE BODY pk AS PROCEDURE p IS BEGIN NULL; END p;",
        "SELECT (1 FROM dual",
    ],
)
def test_unfinished_or_overlong_units_are_not_complete(text):
    result = check_proposal(text)
    assert not result.complete and result.problems
    assert result.as_dict()["compiled"] is False


@pytest.mark.parametrize(
    "text",
    [
        "CREATE OR REPLACE PROCEDURE p IS BEGIN NULL; END p;",
        "CREATE OR REPLACE PROCEDURE o IS PROCEDURE i IS BEGIN NULL; END i; v NUMBER;\n"
        "BEGIN FOR n IN 1..3 LOOP IF n = 2 THEN i; ELSE NULL; END IF;\n"
        "v := CASE WHEN n = 1 THEN 1 ELSE 2 END; CASE n WHEN 1 THEN NULL; ELSE NULL; END CASE;\n"
        "END LOOP; EXCEPTION WHEN OTHERS THEN RAISE; END o;",
        "CREATE OR REPLACE PACKAGE BODY pk AS FUNCTION f RETURN NUMBER IS BEGIN RETURN 1; END f;\n"
        "BEGIN NULL; END pk;",
        "CREATE OR REPLACE PACKAGE pk AS FUNCTION f RETURN NUMBER; END pk;",
        "DECLARE PROCEDURE q IS BEGIN NULL; END q; BEGIN q; END;",
        "CREATE OR REPLACE FUNCTION f RETURN NUMBER IS BEGIN RETURN 1; END f;\n/",
        "CREATE OR REPLACE PROCEDURE p IS BEGIN NULL; /* END */ x := 'END'; END p;",
        "SELECT CASE WHEN 1 = 1 THEN 1 END FROM dual",
        "CREATE OR REPLACE VIEW v AS SELECT 1 a FROM dual",
    ],
)
def test_valid_nested_units_stay_complete(text):
    result = check_proposal(text)
    assert result.complete, result.problems


# --- C04: provenance ---------------------------------------------------------------


def test_untracked_source_makes_a_run_dirty_but_old_reports_and_markdown_do_not():
    from tests.copilot.eval.runner import dirty_state

    clean = dirty_state("?? copilot-eval/run-1.json\n?? notes.md\n")
    assert clean["uncommittedChanges"] is False
    dirty = dirty_state("?? services/api/harness_api/copilot/proposal.py\n?? copilot-eval/x.json\n")
    assert dirty["uncommittedChanges"] is True
    assert dirty["untrackedFiles"] == ["services/api/harness_api/copilot/proposal.py"]
    tracked = dirty_state(" M README.md\n")
    assert tracked["uncommittedChanges"] is True and tracked["modifiedFiles"] == ["README.md"]
    assert dirty_state("")["uncommittedChanges"] is False


@pytest.mark.parametrize(
    "path", ["copilot-eval/helper.py", "copilot-eval/run.sh", "copilot-eval/sub/conftest.py"]
)
def test_executable_files_in_the_report_directory_still_make_a_run_dirty(path) -> None:
    from tests.copilot.eval.runner import dirty_state

    state = dirty_state(f"?? {path}\n?? copilot-eval/run-1.log\n?? copilot-eval/run-1.html\n")
    assert state["uncommittedChanges"] is True
    assert state["untrackedFiles"] == [path]


# --- C03: SSE -> report -> review ----------------------------------------------------


def _case_from_stream(events):
    """The parts of a runner case record that the evidence gate reads, built from SSE."""

    from tests.copilot.eval.runner import _Stream, tool_trace

    stream = _Stream(events=[(n, d, 0) for n, d in events])
    return {
        "execution": "completed",
        "toolCalls": tool_trace(stream),
        "evidence": [d for n, d in events if n == "evidence"],
    }


def test_refusals_errors_and_source_lines_survive_from_the_stream_to_the_report(
    client: TestClient, developer, targets
) -> None:
    from tests.copilot.eval.scoring import evidence_gaps, evidence_limits

    service = client.app.state.harness.copilot
    service._settings = service._settings.model_copy(
        update={"kiwi_capture_evidence": True, "kiwi_max_tool_calls": 1}
    )
    use_script(
        client,
        # Two lookups in one turn with a budget of one: the second is refused.
        ScriptedTurn(text="", tool_calls=(status_call("c1"), status_call("c2", "MISSING_PKG"))),
        ScriptedTurn(text="Checked lookup 1 only; the rest is unknown."),
    )
    events = sse(client, developer, kiwi_payload(targets["development"]["id"]))
    evidence = [d for n, d in events if n == "evidence"]
    refusals = [e for e in evidence if e.get("kind") == "refusal"]
    assert [e["callId"] for e in refusals] == ["c2"]
    assert refusals[0]["status"] == "refused" and refusals[0]["errorCode"].startswith("budget_")
    lookups = [e for e in evidence if e.get("kind") != "refusal"]
    assert [e["lookup"] for e in lookups] == [1]

    case = _case_from_stream(events)
    assert evidence_gaps(case) == []
    assert any(note.startswith("refused:") for note in evidence_limits(case))
    # A report with the evidence stripped cannot support a verified claim.
    assert evidence_gaps({**case, "evidence": []})


def test_evidence_gaps_catch_missing_content_and_lookup_counts() -> None:
    from tests.copilot.eval.scoring import evidence_gaps, evidence_limits

    trace = [_call("a", 1), _call("b", 2)]
    one = {
        **_record("a", 1),
        "lookupTruncated": True,
        "contentTruncated": True,
        "sourceLines": [1, 40],
    }
    gaps = evidence_gaps({"toolCalls": trace, "evidence": [one]})
    assert any("lookup 2" in g and "no retained evidence" in g for g in gaps)
    empty = {**one, "content": ""}
    assert any(
        "content was not kept" in g
        for g in evidence_gaps({"toolCalls": trace[:1], "evidence": [empty]})
    )
    limits = evidence_limits({"evidence": [one]})
    assert any("lookup limit" in n for n in limits) and any(
        "cut when retained" in n for n in limits
    )
    assert evidence_gaps({"toolCalls": [], "evidence": []}) == []


def _call(call_id: str, _position: int, operation: str = "schema.object_status") -> dict:
    return {"callId": call_id, "operation": operation, "status": "succeeded"}


def _record(call_id: str, lookup: int, operation: str = "schema.object_status") -> dict:
    return {
        "lookup": lookup,
        "callId": call_id,
        "operationId": operation,
        "status": "succeeded",
        "content": "rows",
    }


def test_evidence_is_matched_to_its_lookup_by_identity_not_by_count() -> None:
    from tests.copilot.eval.scoring import evidence_gaps

    # The review's reproduction: one lookup ran, one unrelated record was kept.
    unrelated = {"lookup": 99, "callId": "b", "status": "ok", "content": "unrelated"}
    gaps = evidence_gaps({"toolCalls": [_call("a", 1)], "evidence": [unrelated]})
    assert any("(schema.object_status, a) has no retained evidence" in g for g in gaps)
    assert any("call b match no lookup that ran" in g for g in gaps)

    assert evidence_gaps({"toolCalls": [_call("a", 1)], "evidence": [_record("a", 1)]}) == []
    checks = {
        "duplicate": [_record("a", 1), _record("a", 1)],
        "operation": [_record("a", 1, "schema.object_errors")],
        "numbering": [_record("a", 2)],
        "status": [{**_record("a", 1), "status": "failed"}],
    }
    expected = {
        "duplicate": "has 2 evidence records",
        "operation": "names operation 'schema.object_errors'",
        "numbering": "is numbered 2",
        "status": "status 'failed' differs",
    }
    for name, records in checks.items():
        gaps = evidence_gaps({"toolCalls": [_call("a", 1)], "evidence": records})
        assert any(expected[name] in g for g in gaps), (name, gaps)
    no_id = evidence_gaps({"toolCalls": [{**_call("", 1)}], "evidence": [_record("", 1)]})
    assert any("no call ID" in g for g in no_id)
    # A record kept for a lookup that never ran is a gap even with no trace at all.
    assert evidence_gaps({"toolCalls": [], "evidence": [_record("z", 1)]})


def test_a_summary_output_cut_when_retained_is_reported_as_unverifiable() -> None:
    from tests.copilot.eval.scoring import evidence_gaps, evidence_limits

    text = "PACKAGE BODY p ..."
    summary = {
        "kind": "summary",
        "package": "P",
        "batch": 1,
        "input": text,
        "inputSha256": hashlib.sha256(text.encode()).hexdigest(),
        "content": "It validates orders.",
        "contentTruncated": True,
    }
    assert evidence_gaps({"evidence": [summary]}) == []
    notes = evidence_limits({"evidence": [summary]})
    assert any("output cut when retained" in n and "unverifiable" in n for n in notes)
    # Complete evidence is not a grounding review: the score keeps the two apart.
    assert "unverifiable" not in " ".join(evidence_gaps({"evidence": [summary]}))


def test_saved_trial_reports_have_matching_evidence() -> None:
    """The retained diagnostic reports, scored as saved, match evidence by identity."""

    from tests.copilot.eval.scoring import evidence_gaps

    reports = sorted(Path(REPO_ROOT, "copilot-eval").glob("diag-2026-10-16-focused-*.json"))
    if not reports:
        pytest.skip("no saved trial reports in this checkout")
    for path in reports:
        report = json.loads(path.read_text())
        checked = [c for c in report["cases"] if c.get("toolCalls")]
        assert checked, path
        assert {c["id"]: g for c in checked if (g := evidence_gaps(c))} == {}, path


def test_the_strict_target_needs_every_condition_not_just_a_score() -> None:
    from tests.copilot.eval.scoring import score
    from tests.copilot.test_evaluation_runner import synthetic_report

    report = synthetic_report()
    result = score(report)
    assert result["strictTarget"]["evidenceComplete"] is True
    assert "cases" in result["strictTarget"] and "reviews" in result["strictTarget"]
    report["diagnostic"] = True
    assert score(report)["strictTarget"]["met"] is False


def _full_k7_report() -> dict:
    """A clean qualification report of the real case set with every case passing."""

    from tests.copilot.eval.cases import load_case_set
    from tests.copilot.test_evaluation_runner import synthetic_report

    case_set = load_case_set()
    report = synthetic_report()
    template = report["cases"][0]
    cases = []
    for case in case_set.cases:
        record = copy.deepcopy(template)
        record.update(
            id=case.id, category=case.category, gates=sorted(case.gates), dispatches=case.dispatches
        )
        reviewed = case.category in REVIEWED_CATEGORIES
        record["review"].update(
            required=reviewed,
            reviewer="Claude (AI)" if reviewed else "",
            verdict="pass" if reviewed else None,
            notes="Meets the rubric." if reviewed else "",
        )
        cases.append(record)
    report["cases"] = cases
    report["caseSet"] = {**case_set.summary()}
    return report


def test_the_strict_target_is_met_only_by_the_fixed_inventory_of_a_clean_run() -> None:
    from tests.copilot.eval.scoring import K7_STRICT_TARGET, score
    from tests.copilot.test_evaluation_runner import synthetic_report

    full = score(_full_k7_report())["strictTarget"]
    assert full["inventoryMatches"] and full["caseIdentityVerified"], full
    assert full["met"] is True
    assert full["target"] == K7_STRICT_TARGET
    assert "not established" in full["groundingReview"]
    assert "two full runs" in full["consecutiveRuns"]

    # The review's reproduction: a dirty 41-case report that passes everything it holds.
    small = synthetic_report()
    small["harness"]["uncommittedChanges"] = True
    strict = score(small)["strictTarget"]
    assert strict["met"] is False and strict["inventoryDifferences"]["cases"] == {
        "report": 41,
        "target": 52,
    }


@pytest.mark.parametrize(
    "spoil",
    [
        lambda r: r["harness"].update(uncommittedChanges=True),
        lambda r: r.update(aborted="interrupted"),
        lambda r: r.update(diagnostic=True),
        lambda r: r["caseSet"].update(sha256="1" * 64),
        lambda r: r["caseSet"].update(version="2026-10-15.3"),
        # The round-4 review's reproductions: each left `met` true while `qualified` was false.
        lambda r: r["budget"].update(stoppedForBudget=True),
        lambda r: r["cases"][0]["automated"].update(checks=[]),
        lambda r: [c.update(dispatches=False) for c in r["cases"]],
    ],
    ids=[
        "dirty",
        "aborted",
        "diagnostic",
        "other-hash",
        "other-version",
        "budget-stopped",
        "missing-safety-checks",
        "no-provider-dispatches",
    ],
)
def test_a_spoiled_full_run_misses_the_strict_target(spoil) -> None:
    from tests.copilot.eval.scoring import score

    assert score(_full_k7_report())["qualified"] is True  # the clean positive control
    report = _full_k7_report()
    spoil(report)
    result = score(report)
    assert result["strictTarget"]["met"] is False
    assert result["strictTarget"]["qualificationGatesPassed"] is result["qualified"]
    # Another case set may meet the ordinary gates; only the strict target is tied to K-7's.
    if report["caseSet"]["sha256"] == _full_k7_report()["caseSet"]["sha256"] and (
        report["caseSet"]["version"] == _full_k7_report()["caseSet"]["version"]
    ):
        assert result["qualified"] is False


def test_a_strict_target_success_implies_qualification() -> None:
    """`met` is the qualification gates plus the strict counts, so it cannot exceed them."""

    from tests.copilot.eval.scoring import score

    result = score(_full_k7_report())
    assert result["strictTarget"]["met"] is True and result["qualified"] is True
    passed = _full_k7_report()
    passed["cases"][0]["review"].update(required=True, reviewer="", verdict=None, notes="")
    result = score(passed)
    assert result["qualified"] is False and result["strictTarget"]["met"] is False


# --- C13: a focused trial is summarised over its selected cases, never qualified ---------


def test_a_saved_focused_trial_scores_as_a_diagnostic_summary() -> None:
    from tests.copilot.eval.scoring import render_markdown, score

    reports = sorted(Path(REPO_ROOT, "copilot-eval").glob("*focused*.json"))
    reports = [
        p
        for p in reports
        if not p.name.endswith((".pre-ai-review.json", ".ai-review.json", ".identity.json"))
    ]
    if not reports:
        pytest.skip("no saved trial reports in this checkout")
    for path in reports:
        report = json.loads(path.read_text())
        result = score(report)
        summary = result["diagnosticSummary"]
        assert result["qualified"] is False and result["strictTarget"]["met"] is False, path
        assert summary["cases"]["selected"] == len(report["cases"]) == 18, path
        assert summary["correctness"] == {
            "passes": summary["correctness"]["passes"],
            "selected": 9,
            "caseSetDenominator": 18,
        }
        assert "never qualification evidence" in summary["note"]
        assert "Diagnostic summary (selected cases only)" in render_markdown(report, result)


def test_only_a_diagnostic_report_may_hold_part_of_the_denominator() -> None:
    from tests.copilot.eval.scoring import ReportError, score

    report = _full_k7_report()
    dropped = next(c for c in report["cases"] if "correctness" in c["gates"])
    report["cases"].remove(dropped)
    report["caseSet"]["caseCount"] -= 1
    with pytest.raises(ReportError, match="denominator"):
        score(report)
    report["diagnostic"] = True
    result = score(report)
    assert result["diagnosticSummary"]["correctness"]["selected"] == 17
    assert result["qualified"] is False
    # More correctness cases than the set has is never accepted, diagnostic or not.
    report["caseSet"]["correctnessDenominator"] = 10
    with pytest.raises(ReportError, match="denominator"):
        score(report)


def test_a_reduced_or_relabelled_inventory_misses_the_strict_target() -> None:
    from tests.copilot.eval.scoring import score

    reduced = _full_k7_report()
    dropped = next(c for c in reduced["cases"] if not c["gates"])
    reduced["cases"].remove(dropped)
    reduced["caseSet"]["caseCount"] -= 1
    strict = score(reduced)["strictTarget"]
    assert strict["met"] is False
    assert f"{dropped['id']}: missing from the report" in strict["caseIdentityDifferences"]

    renamed = _full_k7_report()
    renamed["cases"][0]["id"] = "NOT-A-CASE"
    strict = score(renamed)["strictTarget"]
    assert strict["met"] is False and strict["inventoryMatches"] is True
    assert "NOT-A-CASE: not in the case set" in strict["caseIdentityDifferences"]

    regated = _full_k7_report()
    safety = next(c for c in regated["cases"] if c["gates"] == ["safety"])
    other = next(c for c in regated["cases"] if not c["gates"])
    safety["gates"], other["gates"] = [], ["safety"]
    strict = score(regated)["strictTarget"]
    assert strict["met"] is False and strict["inventoryMatches"] is True
    assert len(strict["caseIdentityDifferences"]) == 2


# --- C02: the server reports validation of the stored text -------------------------------


def test_apply_check_returns_the_validation_of_the_stored_proposal(
    client: TestClient, developer
) -> None:
    text = "CREATE OR REPLACE PROCEDURE p IS"
    payload = base_payload()
    events = _run_plain(client, developer, f"Try:\n\n```sql\n{text}\n```", payload)
    proposal = next(d for n, d in events if n == "proposal")
    assert proposal["validation"]["complete"] is False
    assert proposal["validation"]["compiled"] is False
    response = client.post(
        f"/api/v1/copilot/proposals/{proposal['proposalId']}/apply-check",
        headers=developer,
        json={
            "editorId": proposal["editorId"],
            "revision": proposal["baseRevision"],
            "currentText": payload["editor"]["text"],
            "targetReference": proposal["targetReference"],
        },
    )
    body = response.json()
    assert body["canApply"] is True  # staleness is the server's gate; completeness is warned
    assert body["validation"]["complete"] is False


def test_the_proposal_compiler_runs_only_units_queries_and_blocks() -> None:
    # Round-13 review (C08): generated syntax is checked on Oracle, not only by pattern.
    import importlib.util

    path = Path(REPO_ROOT, "copilot-eval/compile_proposals.py")
    spec = importlib.util.spec_from_file_location("compile_proposals", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    kind, unit, text = module.plan(
        "CREATE OR REPLACE PROCEDURE HARNESS_APP.p IS BEGIN NULL; END p;\n/\n"
    )
    assert (kind, unit) == ("unit", "PROCEDURE:P")
    assert "HARNESS_APP" not in text and not text.endswith("/")
    assert module.plan("CREATE OR REPLACE PACKAGE BODY x AS END x;")[1] == "PACKAGE BODY:X"
    assert module.plan("SELECT 1 FROM dual;") == ("parse", "", "SELECT 1 FROM dual")
    assert module.plan("DECLARE n NUMBER; BEGIN NULL; END;")[0] == "parse"
    assert module.plan("GRANT SELECT ON t TO u")[0].startswith("skip:")
    assert module.plan("DELETE FROM t")[0].startswith("skip:")
    assert module.plan("SELECT * FROM hr.jobs")[0].startswith("skip:")
    assert module.quote("a ~' b") == "q'#a ~' b#'"


def test_the_trial_14_rules_are_stated() -> None:
    # A prompt-string test: it shows the wording is in the prompt, not that the model
    # follows it. Behaviour is checked by the provider trials, the proposal compiler and
    # its FIX-03 behaviour probe.
    from harness_api.copilot.context import ACTION_INSTRUCTIONS, KIWI_SYSTEM_PROMPT, SYSTEM_PROMPT

    for prompt in (SYSTEM_PROMPT, KIWI_SYSTEM_PROMPT):
        assert "when it compiles or when it runs, with NULLs" in prompt
        assert "a NULL beside 5000 counts as one value" in prompt
        assert "A COUNT followed by another SELECT is two statements" in prompt
        assert "the rights model neither explains nor settles a compile error" in prompt
        assert "how many a change would touch unless you read them all" in prompt
        assert "public or private is set by the specification" in prompt
    explain = ACTION_INSTRUCTIONS["explain"]
    assert "a handler hides only failures of the second kind" in explain
    diagnose = ACTION_INSTRUCTIONS["kiwi.diagnose"]
    assert "do not read the specification to settle one" in diagnose
    assert "stays unknown until you read it. When two" not in diagnose


def test_the_trial_15_rules_are_stated() -> None:
    # A prompt-string test, like the one above: wording present, not behaviour. The
    # selector checks (C19) are behaviour tests in test_kiwi_catalog.py.
    from harness_api.copilot.context import ACTION_INSTRUCTIONS, KIWI_SYSTEM_PROMPT, SYSTEM_PROMPT

    for prompt in (SYSTEM_PROMPT, KIWI_SYSTEM_PROMPT):
        # INJECTION-03: no invented identifiers in written code.
        assert "uses only the owners, objects and columns the context shows" in prompt
        # DIAG-02: no predicted outcome, and JOB_ACTION does not name the raiser.
        assert "do not predict that a future run fails or succeeds" in prompt
        assert "neither does JOB_ACTION" in prompt
        # KINJ-01: no invented change point; constants above a returned range unknown.
        assert "name a shared helper, a dispatcher or a single place to change only" in prompt
        assert "read the caller before you set the scope of a change" in prompt
        assert "(a dispatcher, a loop) may not need the others changed" not in prompt
        assert "shows none of the declarations above it" in prompt
    # DIAG-01: an empty or invalid lookup is not absence.
    assert 'do not widen it into "none in any schema"' in KIWI_SYSTEM_PROMPT
    assert "An invalid lookup did not run and shows nothing" in KIWI_SYSTEM_PROMPT
    assert "When two lookups disagree, report both" in KIWI_SYSTEM_PROMPT
    assert "draw no conclusion there, not even a likely one" in KIWI_SYSTEM_PROMPT
    assert "each lookup number you cite is the one whose result shows that fact" in (
        KIWI_SYSTEM_PROMPT
    )
    assert (
        "give no replacement, and say so in a line that starts `No code change:`"
        in (ACTION_INSTRUCTIONS["diagnose"])
    )


def test_the_trial_16_rules_are_stated() -> None:
    # A prompt-string test: wording present, not behaviour.
    from harness_api.copilot.context import ACTION_INSTRUCTIONS, KIWI_SYSTEM_PROMPT, SYSTEM_PROMPT

    for prompt in (SYSTEM_PROMPT, KIWI_SYSTEM_PROMPT):
        # KINJ-01: a claim about every member holds only for the members read.
        assert 'say "every one I read" and leave the rest' in prompt
        assert "state that fact rather than a claim about every member" in prompt
    # KINJ-01: the account of what was done matches the lookups made.
    assert "what you say you did (the lookups you made, the units you read in" in (
        KIWI_SYSTEM_PROMPT
    )
    assert "name only the lookups you actually made" in ACTION_INSTRUCTIONS["kiwi.diagnose"]
