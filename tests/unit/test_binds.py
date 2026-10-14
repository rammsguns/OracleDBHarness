"""A JSON array hinted as a vector, checked before it reaches a backend.

Without the hint a JSON array reaches the driver as a list, which python-oracledb binds
as a PL/SQL array and SQL refuses with ORA-01484, as D2 showed on 2026-10-14.
"""

from __future__ import annotations

import pytest

from harness_worker.binds import VectorBind, bind_values
from harness_worker.errors import ValidationError
from harness_worker.types import BindParameter


def _bind(value, hint: str | None) -> object:
    return bind_values([BindParameter(name="v", value=value, typeHint=hint)])["v"]


def test_a_value_without_a_vector_hint_is_passed_through_unchanged() -> None:
    assert _bind([1, 2], None) == [1, 2]
    assert _bind("text", "varchar2") == "text"


@pytest.mark.parametrize(
    ("hint", "expected"),
    [
        ("vector", "FLOAT32"),
        ("VECTOR", "FLOAT32"),
        ("vector:float64", "FLOAT64"),
        (" Vector:INT8 ", "INT8"),
    ],
)
def test_a_vector_hint_names_the_format(hint: str, expected: str) -> None:
    assert _bind([1, -2], hint) == VectorBind((1, -2), expected)


def test_a_null_vector_binds_as_null() -> None:
    assert _bind(None, "vector") is None


@pytest.mark.parametrize(
    ("value", "hint"),
    [
        ([], "vector"),
        ("[1, 2]", "vector"),
        ([1, "2"], "vector"),
        ([True, 1], "vector"),
        ([1, [2]], "vector"),
        ([float("nan")], "vector"),
        ([float("inf")], "vector:float64"),
        ([1.5], "vector:int8"),
        ([128], "vector:int8"),
        ([1], "vector:binary"),
        ([1], "vector:float16"),
    ],
)
def test_a_value_that_is_not_a_vector_of_that_format_is_refused(value, hint: str) -> None:
    with pytest.raises(ValidationError) as refused:
        _bind(value, hint)
    assert refused.value.detail == {"bind": "v"}
