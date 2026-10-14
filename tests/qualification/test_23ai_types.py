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
from harness_worker.binds import VectorBind
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


_VECTOR_TABLE = "harness_vector_probe"


@pytest.fixture
def vector_table(connection: OracleConnection, limits: ExecutionLimits, evidence: Evidence):
    _require_23ai(connection, evidence, "VECTOR")
    connection.execute(
        f"CREATE TABLE {_VECTOR_TABLE} (id NUMBER PRIMARY KEY, embedding VECTOR(3, FLOAT32),"
        " code VECTOR(2, INT8))",
        {},
        StatementKind.DDL,
        limits,
    )
    try:
        yield _VECTOR_TABLE
    finally:
        connection.rollback()
        connection.execute(f"DROP TABLE {_VECTOR_TABLE} PURGE", {}, StatementKind.DDL, limits)


def test_a_vector_bind_is_stored_and_searched(
    connection: OracleConnection,
    limits: ExecutionLimits,
    evidence: Evidence,
    old_client: str | None,
    vector_table: str,
) -> None:
    """A JSON array hinted as a vector binds as one. Unhinted, it binds as a PL/SQL
    array and SQL refuses it with ORA-01484."""

    warnings: list[str] = []
    for row, (embedding, code) in enumerate(
        [((1.0, 0.0, 0.0), (1, -2)), ((0.0, 1.0, 0.0), (3, 4)), ((0.6, 0.8, 0.0), (5, 6))]
    ):
        inserted = connection.execute(
            f"INSERT INTO {vector_table} VALUES (:id, :embedding, :code)",
            {
                "id": row + 1,
                "embedding": VectorBind(embedding, "FLOAT32"),
                "code": VectorBind(code, "INT8"),
            },
            StatementKind.DML,
            limits,
        )
        warnings += inserted.warnings
    nearest = connection.execute(
        f"SELECT id, ROUND(VECTOR_DISTANCE(embedding, :query, COSINE), 4)"
        f" FROM {vector_table} ORDER BY 2 FETCH FIRST 2 ROWS ONLY",
        {"query": VectorBind((0.0, 1.0, 0.0), "FLOAT32")},
        StatementKind.QUERY,
        limits,
    )
    stored = _select(connection, f"SELECT code FROM {vector_table} WHERE id = 1", limits)
    assert nearest.result_set is not None
    evidence.note(
        "VECTOR binds",
        f"nearest to [0, 1, 0]: `{nearest.result_set.rows}`; INT8 stored as "
        f"`{stored.rows[0][0]!r}`; warnings `{warnings + nearest.warnings}`",
    )

    assert nearest.result_set.rows == [[2, 0], [3, 0.2]]
    with pytest.raises(Exception, match="ORA-01484"):
        connection.execute(
            f"SELECT COUNT(*) FROM {vector_table} WHERE VECTOR_DISTANCE(embedding, :q) < 1",
            {"q": [0.0, 1.0, 0.0]},
            StatementKind.QUERY,
            limits,
        )
    if old_client:
        assert stored.rows[0][0]["values"] == [1, -2]
        assert all(old_client in warning for warning in warnings + nearest.warnings)
        assert len(warnings) == 3
        return
    assert stored.rows[0][0] == {
        "kind": "vector",
        "format": "INT8",
        "dimensions": 2,
        "values": [1, -2],
        "truncated": False,
    }
    assert warnings == [] and nearest.warnings == []


def test_a_vector_index_serves_an_approximate_search(
    connection: OracleConnection,
    limits: ExecutionLimits,
    evidence: Evidence,
    vector_table: str,
) -> None:
    connection.execute(
        f"INSERT INTO {vector_table} (id, embedding)"
        " SELECT level, TO_VECTOR('[' || level || ', 1, 0]', 3, FLOAT32) FROM dual"
        " CONNECT BY level <= 50",
        {},
        StatementKind.DML,
        limits,
    )
    connection.commit()
    connection.execute(
        f"CREATE VECTOR INDEX {vector_table}_ivf ON {vector_table} (embedding)"
        " ORGANIZATION NEIGHBOR PARTITIONS DISTANCE COSINE",
        {},
        StatementKind.DDL,
        limits,
    )
    found = _select(
        connection,
        f"SELECT id FROM {vector_table}"
        " ORDER BY VECTOR_DISTANCE(embedding, TO_VECTOR('[50, 1, 0]'), COSINE)"
        " FETCH APPROX FIRST 1 ROWS ONLY",
        limits,
    )
    evidence.note("IVF vector index", f"approximate nearest to [50, 1, 0]: `{found.rows}`")
    assert found.rows == [[50]]

    try:
        connection.execute(
            f"CREATE VECTOR INDEX {vector_table}_hnsw ON {vector_table} (code)"
            " ORGANIZATION INMEMORY NEIGHBOR GRAPH DISTANCE COSINE",
            {},
            StatementKind.DDL,
            limits,
        )
    except Exception as exc:  # noqa: BLE001 - the pool is the database's configuration
        if "ORA-51962" not in str(exc):
            raise
        evidence.gap(
            "HNSW vector index",
            "ORA-51962: the database has no vector memory pool (VECTOR_MEMORY_SIZE).",
        )
    else:
        evidence.note("HNSW vector index", "created")
