"""Proposals are read from an answer's code blocks a line at a time (K-7, 2026-10-15).

The answers here have the shapes the local model gave in the K-7 run: the error message
in a ```text block before the code, and quoted lines in blocks indented inside a list.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from harness_api.copilot.fences import fenced_blocks, without
from tests.copilot.test_copilot import base_payload, sse
from tests.copilot.test_kiwi_authoring import fixed, multi_part_payload

__all__ = ["fixed"]

REPLACEMENT = """FUNCTION headcount(p_department_id IN NUMBER) RETURN NUMBER IS
  l_count NUMBER;
BEGIN
  SELECT COUNT(*) INTO l_count FROM employees WHERE department_id = p_department_id;
  RETURN l_count;
END headcount;"""

# FIX-01's shape: a ```text block, then prose, then the code.
ERROR_FIRST = f"""## Diagnosis

The compiler error is:

```text
PL/SQL: ORA-00942: table or view does not exist (line 4, column 36)
```

That points to the table reference on line 4. There is no `EMPLOYEE` table.

## Proposed replacement

```plsql
{REPLACEMENT}
```

Not compiled: apply it in the editor and compile there.
"""

# FIX-02's shape: quoted lines in blocks indented inside a list item.
INDENTED = """- Line 2 declares the variable:
  ```sql
  l_totl NUMBER := 0;
  ```

- Line 4 uses another name.

```sql
FUNCTION f RETURN NUMBER IS
  l_total NUMBER := 0;
BEGIN
  RETURN l_total;
END f;
```
"""


def proposal_for(client: TestClient, developer, install, answer: str) -> dict | None:
    install(answer)
    events = sse(client, developer, base_payload())
    return next((data for name, data in events if name == "proposal"), None)


def test_a_text_block_before_the_code_is_skipped_whole(
    client: TestClient, developer, fixed
) -> None:
    proposal = proposal_for(client, developer, fixed, ERROR_FIRST)
    assert proposal is not None
    assert proposal["proposedText"] == REPLACEMENT
    # The error text stays in the rationale it belongs to; the code does not.
    assert "ORA-00942: table or view does not exist" in proposal["rationale"]
    assert "Not compiled" in proposal["rationale"]
    assert "SELECT COUNT(*)" not in proposal["rationale"]


def test_the_first_code_block_is_the_proposal_even_when_indented(
    client: TestClient, developer, fixed
) -> None:
    # The model was asked for one block; given several, the first is what it proposed.
    proposal = proposal_for(client, developer, fixed, INDENTED)
    assert proposal is not None
    assert proposal["proposedText"] == "l_totl NUMBER := 0;"
    assert "FUNCTION f RETURN NUMBER" in proposal["rationale"]


def test_an_answer_with_only_prose_or_other_languages_has_no_proposal(
    client: TestClient, developer, fixed
) -> None:
    answer = "The error:\n\n```text\nORA-00942\n```\n\nRun this:\n\n```bash\nsqlplus\n```\n"
    assert proposal_for(client, developer, fixed, answer) is None


def test_labelled_parts_are_read_a_line_at_a_time(client: TestClient, developer, fixed) -> None:
    fixed(
        "The error was:\n\n```text\nPLS-00323\n```\n\n"
        "```plsql part=body\n-- new body\n```\n\nThe spec is unchanged.\n"
    )
    events = sse(client, developer, multi_part_payload())
    proposal = next(data for name, data in events if name == "proposal")
    by_part = {part["part"]: part for part in proposal["parts"]}
    assert by_part["body"]["proposedText"] == "-- new body"
    assert by_part["spec"]["changed"] is False
    assert "PLS-00323" in proposal["rationale"]
    assert "-- new body" not in proposal["rationale"]


def test_blocks_are_found_as_markdown_finds_them() -> None:
    blocks = fenced_blocks(ERROR_FIRST)
    assert [(b.language, b.part) for b in blocks] == [("text", None), ("plsql", None)]
    assert blocks[1].text == REPLACEMENT


def test_an_indented_block_loses_the_fence_indentation() -> None:
    first = fenced_blocks(INDENTED)[0]
    assert first.language == "sql"
    assert first.text == "l_totl NUMBER := 0;"


def test_a_longer_fence_may_hold_a_shorter_one() -> None:
    answer = "````markdown\nUse:\n```sql\nSELECT 1 FROM dual;\n```\n````\n"
    blocks = fenced_blocks(answer)
    assert len(blocks) == 1
    assert blocks[0].language == "markdown"
    assert "```sql" in blocks[0].text


def test_tildes_close_tildes_only() -> None:
    answer = "~~~sql\nSELECT 1 FROM dual;\n```\nstill code\n~~~\n"
    (block,) = fenced_blocks(answer)
    assert block.text == "SELECT 1 FROM dual;\n```\nstill code"


def test_an_unclosed_fence_is_not_a_block() -> None:
    # A truncated answer: the code never ends, so nothing in it is proposed.
    assert fenced_blocks("Here is the fix:\n\n```sql\nSELECT 1\n") == []


def test_an_inline_triple_backtick_does_not_open_a_block() -> None:
    answer = "Write ```sql``` before the code.\n\n```sql\nSELECT 1 FROM dual;\n```\n"
    (block,) = fenced_blocks(answer)
    assert block.text == "SELECT 1 FROM dual;"


def test_labels_other_than_a_language_and_a_part_are_not_proposals() -> None:
    answer = "```plsql part=body extra\n-- x\n```\n```part=spec\n-- y\n```\n"
    blocks = fenced_blocks(answer)
    assert [(b.language, b.part) for b in blocks] == [("", "spec")]


def test_without_cuts_the_blocks_and_tidies_the_gaps() -> None:
    blocks = fenced_blocks(ERROR_FIRST)
    rest = without(ERROR_FIRST, [blocks[1]])
    assert "```plsql" not in rest
    assert "```text" in rest
    assert "\n\n\n" not in rest
