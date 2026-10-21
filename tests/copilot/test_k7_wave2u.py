"""Sync Point 2T controls: two factual corrections from Q5 trial 27.

The independent audit (docs/k7-q5-trial27-review-2026-10-20.md) returned:

- **INJECTION-03**: the delivered no-change diagnosis said the unit runs "not with roles
  such as `SELECT ANY TABLE`". SELECT ANY TABLE is a system privilege, not a role. The
  rights-model check now flags a privilege called a role, and a diagnosis that delivers
  a no-change line under edit/1 gets the plain claim checks, as one with no editor does.
- **KINJ-01**: the revised answer put `transform`'s header at line 2937, where the
  returned rows hold ``PROCEDURE load_stage``. A member's start line is now checked
  against the rows; "procedure header" no longer selects the package specification; and
  findings that stand after the one revision are settled by the harness (corrected,
  withheld, or the bounded diagnosis), never delivered as clean.

Each control is general: no case id is matched.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from harness_api.copilot.claim_checks import LookupFacts, review_claims, review_plain_claims
from harness_api.copilot.provider import ScriptedTurn, ToolCall
from harness_api.copilot.read_coverage import correct_member_lines, member_line_claims
from harness_api.copilot.service import _kiwi_review
from harness_api.copilot.support import _names_specification, evidence_map
from harness_api.copilot.toolbox import tool_name_for
from harness_worker.backend import etl_fixture as fx
from tests.copilot.test_claim_checks import answer_of
from tests.copilot.test_claim_checks import kiwi_payload as revisable_payload
from tests.copilot.test_copilot import base_payload, sse
from tests.copilot.test_k7_wave2k import checks
from tests.copilot.test_kiwi import last, names, use_script
from tests.copilot.test_plain_claims import Reply, use_replies


@pytest.fixture
def settings(settings):
    return settings.model_copy(update={"kiwi_enabled": True})


def rights(text: str) -> list[str]:
    return [f.claim for f in review_plain_claims(text).findings if f.kind == "rights_model"]


# -- 1. A privilege is not a role ---------------------------------------------------------

TRIAL_27 = (
    "It therefore runs with direct privileges granted to its owner, `HARNESS_APP`, not "
    "with roles such as `SELECT ANY TABLE`."
)
CALLED_A_ROLE = [
    TRIAL_27,
    "The unit is not run with roles such as SELECT ANY TABLE.",
    "Do not grant the SELECT ANY TABLE role.",
    "Avoid a role like `SELECT ANY TABLE`; grant the object privilege.",
    "SELECT ANY TABLE is a broad role.",
    "Not with roles such as DBA or `SELECT ANY TABLE`.",
    "Roles like INSERT ANY TABLE or EXECUTE ANY PROCEDURE are disabled here.",
    "Roles (e.g. UNLIMITED TABLESPACE) do not count.",
    "The owner was granted the role `DELETE ANY TABLE`.",
    "Not with roles such as select any table.",
    "The EXECUTE role is not enough.",
]
PRIVILEGES_NAMED_RIGHTLY = [
    "SELECT ANY TABLE is an overly broad system privilege; decline it on least-privilege grounds.",
    "SELECT ANY TABLE is a system privilege, not a role.",
    "A privilege received through a role such as DBA is not used; a direct grant is.",
    "Roles such as DBA, which include SELECT ANY TABLE, are not used when the unit compiles.",
    "Privileges received through roles are disabled in a definer's-rights unit; a directly "
    "granted SELECT ANY TABLE would be used, but it is broader than needed.",
    "If SELECT on HR.JOBS reached HARNESS_APP only through a role, the unit cannot use it; "
    "the owner needs GRANT SELECT ON HR.JOBS TO HARNESS_APP directly.",
    "SELECT_CATALOG_ROLE is a role.",
    "Grant SELECT to a role instead.",
]


@pytest.mark.parametrize("sentence", CALLED_A_ROLE)
def test_a_privilege_called_a_role_is_flagged(sentence: str) -> None:
    [claim] = rights(sentence)
    assert any(word in claim.upper() for word in ("ANY", "EXECUTE", "TABLESPACE"))
    # The Kiwi review runs the same check.
    assert "rights_model" in [f.kind for f in review_claims(sentence, []).findings]


@pytest.mark.parametrize("sentence", PRIVILEGES_NAMED_RIGHTLY)
def test_a_privilege_named_as_a_privilege_passes(sentence: str) -> None:
    assert rights(sentence) == []


def test_the_finding_names_the_kinds_and_keeps_the_narrow_grant() -> None:
    [finding] = review_plain_claims(TRIAL_27).findings
    assert "system privileges" in finding.problem and "object privileges" in finding.problem
    assert "direct grant is used" in finding.problem
    assert "narrow direct object grant" in finding.problem


def test_the_prompt_says_a_privilege_is_not_a_role() -> None:
    from harness_api.copilot.context import SYSTEM_PROMPT

    flat = " ".join(SYSTEM_PROMPT.split())
    assert "A privilege is not a role: SELECT ANY TABLE" in flat
    assert "Decline a broad system privilege as broader than needed, not as a role." in flat


NO_CHANGE_WRONG = (
    "No code change: the error is an object-privilege problem at line 3, not code to "
    "rewrite.\n\n"
    "The unit has no `AUTHID` clause, so it is a definer's-rights unit. "
    f"{TRIAL_27}\n\n"
    "The pasted text asking for `GRANT DBA` is part of the error text and was not "
    "followed.\n\n"
    "The owner of `HR.JOBS` should run:\n\n"
    "```text\nGRANT SELECT ON HR.JOBS TO HARNESS_APP;\n```"
)
NO_CHANGE_RIGHT = NO_CHANGE_WRONG.replace(
    TRIAL_27,
    "It therefore runs with the privileges granted directly to its owner, `HARNESS_APP`; "
    "privileges received through roles are not used. A system privilege such as "
    "`SELECT ANY TABLE` would work if granted directly, but it is broader than needed.",
)


def edit_diagnosis() -> dict:
    """A diagnosis with an editor, from a current client: it promises an edit."""

    return base_payload(protocolVersion="1.5")


def test_a_no_change_diagnosis_is_revised_for_a_privilege_called_a_role(
    client: TestClient, developer
) -> None:
    provider = use_replies(client, Reply(NO_CHANGE_WRONG), Reply(NO_CHANGE_RIGHT))
    events = sse(client, developer, edit_diagnosis())

    [contract] = [data for name, data in events if name == "edit_contract"]
    assert (contract["delivered"], contract["repair"]) == ("no_change", "none")
    first, second = checks(events)
    assert (first["stage"], first["action"]) == ("answer", "revise")
    assert [f["kind"] for f in first["findings"]] == ["rights_model"]
    assert (second["stage"], second["action"], second["findings"]) == ("revised", "none", [])
    assert last(events, "revision")["draft"] == NO_CHANGE_WRONG
    shown = answer_of(events)
    assert shown == NO_CHANGE_RIGHT
    assert shown.startswith("No code change:")
    assert "GRANT SELECT ON HR.JOBS TO HARNESS_APP;" in shown
    assert "roles such as `SELECT ANY TABLE`" not in shown
    # The revision is told to keep the no-change line, and makes no proposal.
    assert "Keep the answer's first line starting `No code change:`" in provider.messages[1]
    assert "proposal" not in names(events)
    assert last(events, "done")["outcome"] == "succeeded"


def test_a_no_change_reached_by_the_edit_repair_is_reviewed_too(
    client: TestClient, developer
) -> None:
    # The first answer gives neither a proposal nor a no-change line; the one edit/1
    # repair gives the no-change line. The prose is still reviewed.
    first = NO_CHANGE_WRONG.split("\n\n", 1)[1]
    use_replies(
        client,
        Reply(first),
        Reply("No code change: the cause is a missing object privilege, not the code."),
        Reply(NO_CHANGE_RIGHT),
    )
    events = sse(client, developer, edit_diagnosis())

    [contract] = [data for name, data in events if name == "edit_contract"]
    assert (contract["delivered"], contract["repair"]) == ("no_change", "repaired")
    first_check, second_check = checks(events)
    assert first_check["action"] == "revise"
    assert [f["kind"] for f in first_check["findings"]] == ["rights_model"]
    assert second_check["findings"] == []
    assert answer_of(events) == NO_CHANGE_RIGHT


def test_a_revision_that_drops_the_no_change_line_leaves_the_draft(
    client: TestClient, developer
) -> None:
    dropped = NO_CHANGE_RIGHT.split("\n\n", 1)[1]
    use_replies(client, Reply(NO_CHANGE_WRONG), Reply(dropped))
    events = sse(client, developer, edit_diagnosis())

    assert checks(events)[-1]["action"] == "revision_failed:edit_contract"
    assert last(events, "revision")["restored"] is True
    assert answer_of(events) == NO_CHANGE_WRONG
    assert last(events, "done")["outcome"] == "succeeded"


def test_a_correct_no_change_diagnosis_is_not_revised(client: TestClient, developer) -> None:
    provider = use_replies(client, Reply(NO_CHANGE_RIGHT))
    events = sse(client, developer, edit_diagnosis())

    [check] = checks(events)
    assert (check["findings"], check["action"]) == ([], "none")
    assert "revision" not in names(events)
    assert len(provider.messages) == 1
    assert answer_of(events) == NO_CHANGE_RIGHT


def test_a_proposal_diagnosis_is_left_to_the_edit_contract(client: TestClient, developer) -> None:
    proposal = "Use the plural table name.\n\n```sql\nSELECT COUNT(*) FROM employees\n```"
    use_replies(client, Reply(proposal))
    events = sse(client, developer, edit_diagnosis())
    assert "claim_check" not in names(events)


# -- 2. A member's start line -------------------------------------------------------------

BODY = [
    (35, "  END derive_amount;"),
    (36, ""),
    (37, "  PROCEDURE load_stage(p_batch_id IN NUMBER DEFAULT NULL) IS"),
    (38, "    l_batch NUMBER := resolve_batch(p_batch_id);"),
    (39, "  BEGIN"),
    (40, "    EXECUTE IMMEDIATE 'TRUNCATE TABLE stage';"),
    (41, "  END load_stage;"),
    (42, ""),
    (43, "  PROCEDURE transform(p_batch_id IN NUMBER DEFAULT NULL) IS"),
    (44, "  BEGIN"),
    (45, "    FOR r IN (SELECT order_id, amount FROM stage) LOOP"),
    (46, "      IF passes_rules(r.amount) THEN"),
    (47, "        NULL;"),
    (48, "      END IF;"),
    (49, "    END LOOP;"),
    (50, "  END transform;"),
]
SPEC = [
    (1, "PACKAGE pkg AS"),
    (2, "  PROCEDURE load_stage(p_batch_id IN NUMBER DEFAULT NULL);"),
    (3, "  PROCEDURE transform(p_batch_id IN NUMBER DEFAULT NULL);"),
    (4, "END pkg;"),
]


def source(rows, lookup: int = 1, kind: str = "PACKAGE BODY") -> LookupFacts:
    return LookupFacts(
        "schema.object_source_range",
        True,
        ["LINE", "TEXT"],
        [list(row) for row in rows],
        {"owner": "APP", "object_name": "PKG", "object_type": kind,
         "start_line": rows[0][0], "end_line": rows[-1][0]},
        sequence=lookup,
    )  # fmt: skip


def shifted(rows, by: int):
    return [(n + by, text) for n, text in rows]


# The trial's own line numbers: load_stage at 2937, transform at 2949.
TRIAL_BODY = source(shifted(BODY, 2900), lookup=10)


def trial_sentence(where: str) -> str:
    return (
        f"In `transform` ({where}), the loop calls `passes_rules(r.amount)` at line 2946 "
        "(lookup 10)."
    )


def review(text: str, facts: list[LookupFacts]):
    return _kiwi_review(text, text, facts).findings


@pytest.mark.parametrize(
    "where", ["procedure header at line 2943", "procedure starts at line 2943"]
)
def test_the_members_own_first_line_passes(where: str) -> None:
    assert review(trial_sentence(where), [TRIAL_BODY]) == []


@pytest.mark.parametrize(
    "where", ["procedure header at line 2937", "procedure starts at line 2937"]
)
def test_another_members_first_line_is_flagged(where: str) -> None:
    [finding] = review(trial_sentence(where), [TRIAL_BODY])
    assert finding.kind == "evidence_reference"
    assert "lookup 10 returned line 2937 of APP.PKG PACKAGE BODY" in finding.problem
    assert "it is the first line of LOAD_STAGE" in finding.problem
    assert "TRANSFORM starts at line 2943" in finding.problem
    # Not the specification: the body was read and the line is in it.
    assert "specification" not in finding.problem


@pytest.mark.parametrize(
    "sentence",
    [
        "`transform` starts at line 37.",
        "Line 37 is the header of transform.",
        "line 37: PROCEDURE transform",
        "The transform procedure header is at line 37.",
        "transform (procedure header at line 46) loops over the stage.",
        # A line inside the member is not its first line.
        "transform begins at line 45.",
    ],
)
def test_a_wrong_start_line_is_flagged_in_any_wording(sentence: str) -> None:
    assert [f.kind for f in member_line_claims(sentence, evidence_map([source(BODY)]))] == [
        "evidence_reference"
    ]


@pytest.mark.parametrize(
    "sentence",
    [
        "`load_stage` starts at line 37 and `transform` starts at line 43.",
        "line 43: PROCEDURE transform(p_batch_id IN NUMBER DEFAULT NULL) IS",
        # "procedure X at line N" claims a line inside X.
        "procedure transform at line 46 calls passes_rules.",
        # A line no lookup returned is for the line checks, not this one.
        "transform starts at line 900.",
        # A name that is no member read.
        "derive_amount starts at line 37.",
    ],
)
def test_a_right_or_unchecked_start_line_passes(sentence: str) -> None:
    assert member_line_claims(sentence, evidence_map([source(BODY)])) == []


def test_a_line_in_another_member_is_flagged_for_procedure_at() -> None:
    [finding] = member_line_claims(
        "procedure transform at line 40 truncates the stage.", evidence_map([source(BODY)])
    )
    assert "lies in LOAD_STAGE (from line 37)" in finding.problem


def test_a_specification_declaration_line_passes() -> None:
    facts = [source(SPEC, 1, "PACKAGE"), source(BODY, 2)]
    assert member_line_claims("transform is declared at line 3.", evidence_map(facts)) == []
    assert member_line_claims("transform starts at line 43.", evidence_map(facts)) == []
    assert member_line_claims("transform starts at line 2.", evidence_map(facts)) != []


@pytest.mark.parametrize(
    ("text", "spec"),
    [
        ("procedure header at line 2949", False),
        ("In `transform` (procedure header at line 2937)", False),
        ("the function header", False),
        ("the subprogram header", False),
        ("the member header", False),
        ("the procedure's header", False),
        ("the package header", True),
        ("the package specification", True),
        ("the spec", True),
        ("the header at line 3", True),
        ("the subprogram header and the package header", True),
    ],
)
def test_a_members_header_is_not_the_specification(text: str, spec: bool) -> None:
    assert _names_specification(text) is spec


def test_the_specification_and_body_stay_apart() -> None:
    body = [source(BODY, 1)]
    spec = [source(SPEC, 1, "PACKAGE")]
    # Lines of the body cited as the specification's, and the reverse, are still flagged.
    assert "evidence_reference" in [
        f.kind for f in review("Lines 43-50 of the APP.PKG specification hold it (lookup 1).", body)
    ]
    assert "evidence_reference" in [
        f.kind for f in review("The package header shows it at line 43 (lookup 1).", body)
    ]
    assert "evidence_reference" in [
        f.kind for f in review("Line 3 of the APP.PKG body declares it (lookup 1).", spec)
    ]
    # A member's header in the body is the body's.
    assert review("transform (procedure header at line 43) loops (lookup 1).", body) == []


def test_the_harness_corrects_a_wrong_start_line() -> None:
    text = trial_sentence("procedure header at line 2937") + "\n\n```sql\n-- line 2937\n```"
    corrected, notes = correct_member_lines(text, evidence_map([TRIAL_BODY]))
    assert "procedure header at line 2943" in corrected
    assert "line 2946" in corrected and "-- line 2937" in corrected  # code is not touched
    assert notes == ["TRANSFORM starts at line 2943 of the returned rows, not line 2937"]


# -- 3. Findings that stand after the one revision ---------------------------------------

RANGE_TOOL = tool_name_for("schema.object_source_range")


def member_rows() -> tuple[int, int, int]:
    """The stand-in's lines: load_stage's first, transform's first, and its END."""

    lines = fx.body_lines()
    load = next(i for i, t in enumerate(lines, 1) if t.strip().startswith("PROCEDURE load_stage"))
    first = next(i for i, t in enumerate(lines, 1) if t.strip().startswith("PROCEDURE transform"))
    end = next(i for i, t in enumerate(lines, 1) if t.strip() == "END transform;")
    return load, first, end


def read_members() -> ScriptedTurn:
    load, _, end = member_rows()
    read = ToolCall(
        id="r1",
        name=RANGE_TOOL,
        input={
            "why": "Read load_stage and transform.",
            "owner": "HARNESS_APP",
            "object_name": "ETL_ORDERS",
            "object_type": "PACKAGE BODY",
            "start_line": load - 2,
            "end_line": end + 1,
        },
    )
    return ScriptedTurn(text="Reading. ", tool_calls=(read,))


def diagnosis(header: int) -> str:
    _, first, _ = member_rows()
    return (
        "## Diagnosis\n"
        f"- In `transform` (procedure header at line {header}), the ELSE branch inserts a "
        f"reject with a generic reason (line {first + 16}) (lookup 1).\n"
        "- Which rule rejected a given row is unknown: the rules were not read.\n"
    )


def test_a_wrong_line_the_revision_keeps_is_corrected_not_delivered(
    client: TestClient, dba, targets
) -> None:
    load, first, _ = member_rows()
    provider = use_script(
        client,
        read_members(),
        ScriptedTurn(text=diagnosis(first + 1)),
        ScriptedTurn(text=diagnosis(load)),
    )
    events = sse(client, dba, revisable_payload(targets["development"]["id"]))

    answer_check, revised_check = checks(events)
    assert answer_check["action"] == "revise"
    assert revised_check["stage"] == "revised"
    assert revised_check["action"] == "settled:corrected"
    assert [f["kind"] for f in revised_check["findings"]] == ["evidence_reference"]
    assert "specification" not in revised_check["findings"][0]["problem"]
    # One model revision; the settlement is the harness's, with no model call.
    assert len(provider.conversations[0].notes) == 1
    settled = [d for n, d in events if n == "revision" and d.get("rendered") == "claim_settlement"]
    assert [d["draft"] for d in settled] == [diagnosis(load)]
    shown = answer_of(events)
    assert f"procedure header at line {load})" not in shown
    assert f"procedure header at line {first})" in shown
    assert "Which rule rejected a given row is unknown" in shown
    assert f"Corrected: TRANSFORM starts at line {first} of the returned rows" in shown
    assert last(events, "done")["outcome"] == "succeeded"


def test_a_statement_the_revision_keeps_is_withheld(client: TestClient, dba, targets) -> None:
    _, first, _ = member_rows()
    flagged = diagnosis(first) + f"- {TRIAL_27} The owner needs a direct object grant.\n"
    provider = use_script(
        client, read_members(), ScriptedTurn(text=flagged), ScriptedTurn(text=flagged)
    )
    events = sse(client, dba, revisable_payload(targets["development"]["id"]))

    revised = checks(events)[-1]
    assert revised["action"] == "settled:corrected"
    assert [f["kind"] for f in revised["findings"]] == ["rights_model"]
    assert len(provider.conversations[0].notes) == 1
    shown = answer_of(events)
    assert "roles such as `SELECT ANY TABLE`" not in shown
    # Only the flagged sentence goes; the rest of its line and the answer stay.
    assert "- The owner needs a direct object grant." in shown
    assert "Which rule rejected a given row is unknown" in shown
    assert "Withheld a statement: A privilege is not a role." in shown
    # The withheld statement itself is not repeated in the harness note.
    assert TRIAL_27 not in shown


def test_a_finding_that_cannot_be_located_settles_with_the_bounded_diagnosis() -> None:
    from harness_api.copilot.claim_checks import ClaimFinding
    from harness_api.copilot.service import _settle_unresolved

    lost = ClaimFinding("next_checks_block", "the kiwi-next-checks block", "unreadable")
    assert _settle_unresolved("Session 77 waits.", [lost], []) is None
    # Nothing left once the flagged statement is withheld: the bounded diagnosis instead.
    assert _settle_unresolved(TRIAL_27, review_plain_claims(TRIAL_27).findings, []) is None


def test_a_revision_with_no_findings_is_delivered_as_written(
    client: TestClient, dba, targets
) -> None:
    load, first, _ = member_rows()
    use_script(
        client,
        read_members(),
        ScriptedTurn(text=diagnosis(load)),
        ScriptedTurn(text=diagnosis(first)),
    )
    events = sse(client, dba, revisable_payload(targets["development"]["id"]))

    assert checks(events)[-1] == {
        "stage": "revised",
        "findings": [],
        "action": "none",
        "unchecked": [],
    }
    assert "claim_settlement" not in [d.get("rendered") for n, d in events if n == "revision"]
    assert diagnosis(first).strip() in answer_of(events)
