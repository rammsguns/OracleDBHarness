"""Bounded LOB handling.

``_shape`` in the adapter turns a LOB into a preview plus a length and a truncated
flag. It has never met a LOB. The stand-in stores CLOBs as SQLite text and returns
them whole, so neither the bound nor the shape of the result has been exercised.

There is a known ambiguity here worth confirming rather than assuming: for a CLOB,
python-oracledb's ``size()`` and ``read(offset, amount)`` count *characters*, while
the limit applied to them is named ``lob_preview_bytes`` and the field reporting the
result is named ``byteLength``. For single-byte characters the two agree. For the
multi-byte content below they do not, and these tests record which one this driver
returns.
"""

from __future__ import annotations

from typing import Any

from harness_worker.backend import OracleConnection
from harness_worker.types import ExecutionLimits, StatementKind
from tests.qualification.evidence import Evidence


def _row(
    connection: OracleConnection, columns: str, id_: int, limits: ExecutionLimits
) -> list[Any]:
    result = connection.execute(
        f"SELECT {columns} FROM harness_lobs WHERE id = :id",  # noqa: S608 - fixed literals
        {"id": id_},
        StatementKind.QUERY,
        limits,
    )
    assert result.result_set is not None
    assert result.result_set.rows, f"harness_lobs row {id_} is missing"
    return result.result_set.rows[0]


def test_a_small_clob_is_returned_whole(
    connection: OracleConnection,
    limits: ExecutionLimits,
    evidence: Evidence,
) -> None:
    """Under the limit, nothing should be marked truncated."""

    value = _row(connection, "c_small", 1, limits)[0]
    evidence.note("Shape of a small CLOB", f"`{value!r}`")

    assert isinstance(value, dict), (
        f"A CLOB arrived as {type(value).__name__}, not the shaped preview the API "
        "publishes. The result grid and the API contract disagree."
    )
    assert value["kind"] == "lob"
    assert value["preview"] == "short clob"
    assert value["truncated"] is False


def test_a_large_clob_is_cut_to_the_preview_limit(
    connection: OracleConnection,
    limits: ExecutionLimits,
    evidence: Evidence,
) -> None:
    """The bound that keeps a 64 KB column out of a result grid.

    The safety property is that the preview never exceeds the configured limit. That
    holds whether the driver counts characters or bytes; the test asserts it in the
    stricter of the two so a pass means the same thing either way.
    """

    tight = limits.model_copy(update={"lob_preview_bytes": 1024})
    value = _row(connection, "c_large", 1, tight)[0]

    preview = value["preview"]
    evidence.note(
        "Large CLOB under a 1 KiB preview limit",
        f"reported byteLength `{value['byteLength']}`, preview is "
        f"{len(preview)} characters / {len(preview.encode('utf-8'))} bytes, "
        f"truncated=`{value['truncated']}`",
    )

    assert value["truncated"] is True
    assert len(preview) <= 1024
    assert len(preview.encode("utf-8")) <= 1024, (
        f"The preview is {len(preview.encode('utf-8'))} bytes under a 1024-byte "
        "limit. The limit is being applied in characters, so a multi-byte column "
        "can exceed the response budget it exists to protect."
    )
    assert preview.startswith("x")


def test_the_reported_length_of_a_multibyte_lob_is_recorded(
    connection: OracleConnection,
    limits: ExecutionLimits,
    evidence: Evidence,
) -> None:
    """Which unit ``byteLength`` is actually in, for an NCLOB of 4,000 accented characters.

    Recorded rather than asserted: the correct answer depends on what the field is
    meant to promise, and that is a decision to take with the observation in hand.
    A character count reported as ``byteLength`` under-reports this column by half.
    """

    value = _row(connection, "n_large", 1, limits)[0]
    reported = value["byteLength"]
    evidence.note(
        "NCLOB of 4,000 two-byte characters: what is byteLength?",
        f"`{reported}`. 4000 means characters; 8000 means bytes."
        + (
            " The field name promises bytes and the driver counted characters."
            if reported == 4000
            else ""
        ),
    )
    assert reported > 0
    assert value["kind"] == "lob"


def test_a_blob_is_previewed_as_hex_and_bounded(
    connection: OracleConnection,
    limits: ExecutionLimits,
    evidence: Evidence,
) -> None:
    """Binary content must never be decoded as text on its way to the grid."""

    tight = limits.model_copy(update={"lob_preview_bytes": 256})
    value = _row(connection, "b_large", 1, tight)[0]

    preview = value["preview"]
    evidence.note(
        "BLOB under a 256-byte preview limit",
        f"byteLength `{value['byteLength']}`, preview {len(preview)} hex characters, "
        f"truncated=`{value['truncated']}`",
    )
    assert value["truncated"] is True
    # Hex, so two characters per byte and nothing that could be mistaken for text.
    assert all(char in "0123456789abcdefABCDEF" for char in preview), preview[:64]
    assert len(preview) <= 512


def test_empty_and_null_lobs_are_distinguishable(
    connection: OracleConnection,
    limits: ExecutionLimits,
    evidence: Evidence,
) -> None:
    """``EMPTY_CLOB()`` is not NULL, and a user should not see them as the same thing."""

    empty_clob, missing_clob, empty_blob = _row(connection, "c_small, c_large, b_large", 2, limits)
    evidence.note(
        "EMPTY_CLOB() vs NULL",
        f"EMPTY_CLOB() arrived as `{empty_clob!r}`; a NULL CLOB as `{missing_clob!r}`; "
        f"EMPTY_BLOB() as `{empty_blob!r}`.",
    )
    assert missing_clob is None
    assert empty_clob is not None, (
        "EMPTY_CLOB() came back as NULL. An empty LOB and an absent one are different "
        "facts about a row and the grid would show them identically."
    )
    assert empty_clob["byteLength"] == 0
    assert empty_clob["truncated"] is False


def test_a_zero_preview_budget_returns_no_content(
    connection: OracleConnection,
    limits: ExecutionLimits,
) -> None:
    """A deployment that sets the budget to zero must get lengths, not content.

    ``lob_preview_bytes`` has ``ge=0``, so zero is a legal configuration and has to
    mean something coherent rather than falling through to an unbounded read.
    """

    none_at_all = limits.model_copy(update={"lob_preview_bytes": 0})
    value = _row(connection, "c_large", 1, none_at_all)[0]
    assert value["preview"] == ""
    assert value["truncated"] is True
    assert value["byteLength"] > 0
