"""BOOLEAN and VECTOR, the SQL types 23ai added.

Neither exists before 23ai, and the stand-in has neither, so until a 23.26 target ran
these nothing had shown how they arrive. python-oracledb returns a BOOLEAN as a Python
bool, which is JSON already. It returns a VECTOR as an ``array.array`` and a SPARSE
VECTOR as a ``SparseVector``; neither serialises, so a query returning one failed in
the API until ``_shape`` bounded it like a LOB.

The values are literals selected from DUAL, so these checks need no fixture objects.

Thick mode with an Oracle Client older than 23 predates both types. The server then
sends a BOOLEAN as a NUMBER and a VECTOR as text, which arrives as a plain list. The
tests check that behaviour exactly too, so a client that changes it is noticed.
"""

from __future__ import annotations

from typing import Any

import pytest

from harness_worker.backend import OracleConnection, StatementResult
from harness_worker.types import ExecutionLimits, ResultSet, StatementKind
from tests import oracle_config as qual_config
from tests.qualification.evidence import Evidence


@pytest.fixture(scope="module")
def old_client(oracle_config: qual_config.OracleTestConfig) -> str | None:
    """The Oracle Client version when it predates BOOLEAN and VECTOR, otherwise None."""

    if oracle_config.driver_mode == "thin":
        return None
    import oracledb  # thick mode means the driver is installed and initialised

    version = oracledb.clientversion()
    return ".".join(map(str, version)) if version[0] < 23 else None


def _require_23ai(connection: OracleConnection, evidence: Evidence, area: str) -> None:
    identity = connection.identity()
    if identity.major_version < 23:
        reason = f"the target is {identity.version}; the SQL {area} type arrived in 23ai."
        evidence.gap(area, reason)
        pytest.skip(reason)


def _execute(connection: OracleConnection, sql: str, limits: ExecutionLimits) -> StatementResult:
    result = connection.execute(sql, {}, StatementKind.QUERY, limits)
    assert result.result_set is not None, sql
    # What the API does with the rows. A value it cannot serialise fails here.
    result.result_set.model_dump_json(by_alias=True)
    return result


def _select(connection: OracleConnection, sql: str, limits: ExecutionLimits) -> ResultSet:
    result_set = _execute(connection, sql, limits).result_set
    assert result_set is not None
    return result_set


def test_a_boolean_arrives_as_true_false_or_null(
    connection: OracleConnection,
    limits: ExecutionLimits,
    evidence: Evidence,
    old_client: str | None,
) -> None:
    _require_23ai(connection, evidence, "BOOLEAN")

    result = _select(connection, "SELECT TRUE, FALSE, CAST(NULL AS BOOLEAN) FROM dual", limits)
    row = result.rows[0]
    types = [column.type_name for column in result.columns]
    evidence.note("How does BOOLEAN arrive?", f"`{row!r}`, column types {types}")

    if old_client:
        # The server converts for a client that predates BOOLEAN.
        assert row == [1, 0, None]
        assert types[0] == "DB_TYPE_NUMBER"
        return
    assert row == [True, False, None]
    assert all(value is None or type(value) is bool for value in row), row


def test_a_boolean_bind_is_compared_as_a_boolean(
    connection: OracleConnection,
    limits: ExecutionLimits,
    evidence: Evidence,
    old_client: str | None,
) -> None:
    """A JSON true or false arrives as a Python bool, and binds as BOOLEAN. An Oracle
    Client older than 23 binds it as a NUMBER, which a condition rejects."""

    _require_23ai(connection, evidence, "BOOLEAN")

    if old_client:
        with pytest.raises(Exception, match="ORA-00920") as failure:
            connection.execute(
                "SELECT CASE WHEN :value THEN 'yes' ELSE 'no' END FROM dual",
                {"value": True},
                StatementKind.QUERY,
                limits,
            )
        evidence.note(
            "BOOLEAN bind",
            f"Oracle Client {old_client} binds a bool as NUMBER: `{failure.value}`",
        )
        return

    answers: dict[bool, Any] = {}
    for value in (True, False):
        result = connection.execute(
            "SELECT CASE WHEN :value THEN 'yes' ELSE 'no' END FROM dual",
            {"value": value},
            StatementKind.QUERY,
            limits,
        )
        assert result.result_set is not None
        answers[value] = result.result_set.rows[0][0]
    evidence.note("BOOLEAN bind", f"true selected `{answers[True]}`, false `{answers[False]}`")

    assert answers == {True: "yes", False: "no"}


def test_dense_vectors_arrive_bounded_and_json_safe(
    connection: OracleConnection,
    limits: ExecutionLimits,
    evidence: Evidence,
    old_client: str | None,
) -> None:
    _require_23ai(connection, evidence, "VECTOR")

    executed = _execute(
        connection,
        "SELECT TO_VECTOR('[0.1, 1.5, -2]', 3, FLOAT32),"
        " TO_VECTOR('[0.1, 2]', 2, FLOAT64),"
        " TO_VECTOR('[1, -2]', 2, INT8),"
        " TO_VECTOR('[255, 0]', 16, BINARY)"
        " FROM dual",
        limits,
    )
    result = executed.result_set
    assert result is not None
    float32, float64, int8, binary = result.rows[0]
    evidence.note(
        "How does VECTOR arrive?",
        f"FLOAT32 `{float32!r}`<br>FLOAT64 `{float64!r}`<br>INT8 `{int8!r}`<br>"
        f"BINARY `{binary!r}`<br>column types {[c.type_name for c in result.columns]}",
    )

    if old_client:
        # Only the element count is known, and a BINARY vector shows its bytes.
        assert [value["format"] for value in result.rows[0]] == [None] * 4
        assert [value["length"] for value in result.rows[0]] == [3, 2, 2, 2]
        assert int8["values"] == [1, -2] and binary["values"] == [255, 0]
        assert float64["values"] == [0.1, 2.0]
        assert float32["values"][1:] == [1.5, -2.0]
        assert any(old_client in warning for warning in executed.warnings), executed.warnings
        return

    assert float32 == {
        "kind": "vector",
        "format": "FLOAT32",
        "dimensions": 3,
        "values": [0.1, 1.5, -2.0],
        "truncated": False,
    }
    assert float64["format"] == "FLOAT64" and float64["values"] == [0.1, 2.0]
    assert int8["format"] == "INT8" and int8["values"] == [1, -2]
    assert binary == {
        "kind": "vector",
        "format": "BINARY",
        "dimensions": 16,
        "preview": "ff00",
        "truncated": False,
    }


def test_a_wide_vector_is_cut_to_the_preview_limit(
    connection: OracleConnection,
    limits: ExecutionLimits,
    evidence: Evidence,
    old_client: str | None,
) -> None:
    """VECTOR allows 65,535 dimensions; a result grid gets the preview's worth. The
    literal stays under the 4,000-byte VARCHAR2 limit (ORA-01489)."""

    _require_23ai(connection, evidence, "VECTOR")

    tight = limits.model_copy(update={"lob_preview_bytes": 256})
    result = _select(
        connection,
        "SELECT TO_VECTOR('[' || RTRIM(RPAD('1,', 2 * 1000, '1,'), ',') || ']', 1000, FLOAT64)"
        " FROM dual",
        tight,
    )
    value = result.rows[0][0]
    size = value["length"] if old_client else value["dimensions"]
    evidence.note(
        "VECTOR(1000, FLOAT64) under a 256-byte preview limit",
        f"{'elements' if old_client else 'dimensions'} `{size}`, "
        f"{len(value['values'])} values shown, truncated=`{value['truncated']}`",
    )

    assert size == 1000
    assert len(value["values"]) == 32
    assert value["truncated"] is True


def test_a_sparse_vector_keeps_its_indices(
    connection: OracleConnection,
    limits: ExecutionLimits,
    evidence: Evidence,
    old_client: str | None,
) -> None:
    _require_23ai(connection, evidence, "VECTOR")

    result = _select(
        connection,
        "SELECT TO_VECTOR('[4, [1, 3], [1.5, 2.5]]', 4, FLOAT32, SPARSE) FROM dual",
        limits,
    )
    value = result.rows[0][0]
    evidence.note("How does a SPARSE VECTOR arrive?", f"`{value!r}`")

    if old_client:
        # Expanded by the server: the indices are lost, every dimension arrives.
        assert value == {
            "kind": "vector",
            "format": None,
            "length": 4,
            "values": [0.0, 1.5, 0.0, 2.5],
            "truncated": False,
        }
        return
    assert value == {
        "kind": "vector",
        "format": "FLOAT32",
        "sparse": True,
        "dimensions": 4,
        "storedCount": 2,
        "indices": [1, 3],
        "values": [1.5, 2.5],
        "truncated": False,
    }
