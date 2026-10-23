"""K8: collection declarations need named types before variables."""

import pytest

from harness_api.copilot.context import CODE_RULES
from harness_api.copilot.edit_contract import collection_declaration_problems


@pytest.mark.parametrize(
    "declaration",
    [
        "l_orders TABLE OF HARNESS_APP.ORDERS%ROWTYPE;",
        "v VARRAY(10) OF NUMBER;",
        "v VARYING ARRAY (10) OF NUMBER;",
    ],
)
def test_inline_collection_declaration(declaration: str) -> None:
    problems = collection_declaration_problems(f"DECLARE\n{declaration}\nBEGIN NULL; END;")
    assert len(problems) == 1
    assert declaration in problems[0]
    assert "TYPE t IS" in problems[0]
    assert "PLS-00103" in problems[0]


@pytest.mark.parametrize(
    "proposal",
    [
        "TYPE t IS TABLE OF NUMBER; v t;",
        "TYPE t\nIS\nTABLE OF HARNESS_APP.ORDERS%ROWTYPE; v t;",
        "SUBTYPE t IS some_type;",
        "SUBTYPE t IS TABLE OF NUMBER;",
        "TYPE t /* collection */ IS\nTABLE OF NUMBER;",
        "TYPE t IS VARRAY(10) OF NUMBER;",
        "CREATE TYPE t AS TABLE OF NUMBER;",
        "CREATE OR REPLACE TYPE t AS VARYING ARRAY(10) OF NUMBER;",
        "SELECT * FROM TABLE(v);",
        "-- v TABLE OF NUMBER;\nBEGIN NULL; END;",
        "/* v TABLE OF NUMBER; */ BEGIN NULL; END;",
        "BEGIN print('v TABLE OF NUMBER;'); END;",
    ],
)
def test_valid_collection_forms(proposal: str) -> None:
    assert collection_declaration_problems(proposal) == []


def test_prompt_declares_type_before_variable() -> None:
    assert "TYPE t_rows IS TABLE OF <owner>.<table>%ROWTYPE; l_rows t_rows;" in CODE_RULES


@pytest.mark.parametrize("fixed", [True, False])
def test_collection_uses_anchor_repair_path(client, developer, fixed: bool) -> None:
    from tests.copilot.test_k7_edit_contract import event, run
    from tests.copilot.test_k7_wave2h import draft03_payload, fenced

    bad = "CREATE OR REPLACE PROCEDURE p IS l_orders TABLE OF HARNESS_APP.ORDERS%ROWTYPE; BEGIN NULL; END;"
    good = bad.replace(
        "l_orders TABLE OF HARNESS_APP.ORDERS%ROWTYPE;",
        "TYPE t IS TABLE OF HARNESS_APP.ORDERS%ROWTYPE; l_orders t;",
    )
    provider, events = run(
        client, developer, draft03_payload(), fenced(bad), fenced(good if fixed else bad)
    )
    contract = event(events, "edit_contract")
    assert contract["repair"] == ("repaired" if fixed else "failed:anchor")
    assert len(contract["anchorRepair"]) == 1
    assert len(provider.messages) == 2
    assert event(events, "proposal")["proposedText"] == (good if fixed else bad)
