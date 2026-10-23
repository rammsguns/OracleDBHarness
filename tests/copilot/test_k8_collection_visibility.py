"""K8: collection declarations and elliptical account visibility claims."""

import pytest

from harness_api.copilot.claim_checks import _absence_inferences
from harness_api.copilot.context import CODE_RULES
from harness_api.copilot.edit_contract import collection_declaration_problems
from tests.copilot.test_k7_wave2x import SEARCHED


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


KINJ = [
    "As an account with access (can see the ORDER_FEED schema and its source, which this account cannot)",
    "(can read source in ORDER_FEED, invisible to this account)",
    "(can read ORDER_FEED code and its links, invisible to this account)",
]


@pytest.mark.parametrize(
    "sentence",
    [
        "As an account with access (can see the ORDER_FEED schema and its source, which this account cannot): ...",
        "As an account with access (can read source in ORDER_FEED, invisible to this account): ...",
        "As an account with access (can read ORDER_FEED code and its links, invisible to this account): ...",
    ],
)
def test_verbatim_kinj_visibility_sentences(sentence: str) -> None:
    assert _absence_inferences(sentence, SEARCHED)


@pytest.mark.parametrize(
    "sentence",
    KINJ
    + [
        "The schema is invisible to this account.",
        "The owner is inaccessible to this account.",
        "The source is unreadable to this account.",
    ],
)
def test_categorical_visibility(sentence: str) -> None:
    assert _absence_inferences(sentence, SEARCHED)


@pytest.mark.parametrize(
    "sentence",
    [
        *[
            f"An account can read the schema and its source, which this account cannot {verb}."
            for verb in ("change", "edit", "compile")
        ],
        "The source is invisible to the optimizer.",
        "The schema may be invisible to this account.",
        "The schema may be inaccessible to this account.",
        *[f"`{sentence}`" for sentence in KINJ],
        *[f'"{sentence}"' for sentence in KINJ],
        *[f"'{sentence}'" for sentence in KINJ],
        *[f"‘{sentence}’" for sentence in KINJ],
    ],
)
def test_visibility_negative_probes(sentence: str) -> None:
    assert _absence_inferences(sentence, SEARCHED) == []


def test_prompt_declares_type_before_variable() -> None:
    assert "TYPE t_rows IS TABLE OF <owner>.<table>%ROWTYPE; l_rows t_rows;" in CODE_RULES


def test_complete_owner_scoped_listing() -> None:
    from harness_api.copilot.claim_checks import LookupFacts

    facts = LookupFacts(
        "schema.list_objects", True, ["OBJECT_NAME"], [], {"owner": "ORDER_FEED"}, 1
    )
    assert _absence_inferences("`EXPORT_DAY` does not exist in schema ORDER_FEED.", [facts]) == []


@pytest.mark.parametrize(
    "claim",
    [
        "The table isn't visible to this account",
        "The account's table is invisible to this account",
        "The account's table isn't visible to this account",
    ],
)
def test_word_apostrophes_inside_and_outside_quotes(claim: str) -> None:
    assert _absence_inferences(f"The error says '{claim}'", SEARCHED) == []
    assert _absence_inferences(claim, SEARCHED)
    # A later quote must not mask an earlier, ordinary-prose contraction.
    assert _absence_inferences(f"{claim}; the error says 'lookup failed'", SEARCHED)


@pytest.mark.parametrize("adverb", ["", "currently ", "now ", "yet ", "still "])
@pytest.mark.parametrize("account", ["this", "the"])
@pytest.mark.parametrize("cannot", ["cannot", "can't", "can’t"])
@pytest.mark.parametrize("ending", ["", ")", "]", ".", ";", ":", ",", "  )"])
def test_elliptical_visibility_terminators(
    adverb: str, account: str, cannot: str, ending: str
) -> None:
    sentence = f"Can read the schema, which {adverb}{account} account {cannot}{ending}"
    assert _absence_inferences(sentence, SEARCHED)


@pytest.mark.parametrize("adverb", ["", "currently ", "now ", "yet ", "still "])
@pytest.mark.parametrize("cannot", ["cannot", "can't"])
@pytest.mark.parametrize(
    "continuation",
    [" change.", ", change.", ", for example, change.", "  , for example, compile."],
)
def test_elliptical_visibility_mutation_continuations(
    adverb: str, cannot: str, continuation: str
) -> None:
    sentence = f"Can read the schema, which {adverb}this account {cannot}{continuation}"
    assert _absence_inferences(sentence, SEARCHED) == []


@pytest.mark.parametrize(
    "suffix", ["", " := t()", " DEFAULT t()", " NOT NULL := t()", " NOT NULL DEFAULT t()"]
)
@pytest.mark.parametrize("name", ["l_orders", '"l_orders"', '"IS"', '"order""items"'])
@pytest.mark.parametrize("collection", ["TABLE OF NUMBER", "VARRAY(10) OF NUMBER"])
def test_collection_repair_separates_variable_suffix(
    suffix: str, name: str, collection: str
) -> None:
    problems = collection_declaration_problems(f"DECLARE {name} {collection}{suffix};")
    assert len(problems) == 1
    repair = problems[0].split("declare its type first: ")[1].split(" (TYPE")[0]
    assert repair == f"TYPE t IS {collection}; {name} t{suffix};"
    assert collection_declaration_problems(repair) == []


@pytest.mark.parametrize(
    "proposal",
    [
        'TYPE "t" IS TABLE OF NUMBER; "l_orders" "t" := "t"();',
        'TYPE "t" IS TABLE OF NUMBER; "l_orders" "t" DEFAULT "t"();',
        'CREATE OR REPLACE TYPE "t" AS TABLE OF NUMBER;',
        '-- "l_orders" TABLE OF NUMBER;\nBEGIN NULL; END;',
        '/* "l_orders" TABLE OF NUMBER; */ BEGIN NULL; END;',
        "BEGIN print('\"l_orders\" TABLE OF NUMBER;'); END;",
        "TYPE t IS TABLE OF NUMBER; l_orders t NOT NULL := t();",
    ],
)
def test_quoted_names_and_initialized_named_collections_are_valid(proposal: str) -> None:
    assert collection_declaration_problems(proposal) == []


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
