"""Bind values as the driver should receive them.

A bind arrives as JSON, so its value is a string, number, boolean, null or array. Most
need nothing more. A JSON array is the exception: python-oracledb binds a Python list
as a PL/SQL array, which SQL refuses with ORA-01484, so a vector could not be bound at
all. A ``typeHint`` of ``vector`` (or ``vector:float32``, ``vector:float64``,
``vector:int8``) says the array is a VECTOR, and the backend binds it as one.

FLOAT32 is the default because it is what Oracle makes of vector text with no format,
and ``VECTOR_DISTANCE`` refuses to compare vectors of different formats (ORA-51812).
Other hints are accepted and ignored, as they always were.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from harness_worker.errors import ValidationError
from harness_worker.types import BindParameter

VECTOR_FORMATS = {"float32": "FLOAT32", "float64": "FLOAT64", "int8": "INT8"}


@dataclass(frozen=True)
class VectorBind:
    """A dense vector, checked and ready to bind."""

    values: tuple[float | int, ...]
    format: str


def bind_values(binds: list[BindParameter]) -> dict[str, Any]:
    return {bind.name: _value(bind) for bind in binds}


def _value(bind: BindParameter) -> Any:
    hint = (bind.type_hint or "").strip().lower()
    if hint != "vector" and not hint.startswith("vector:"):
        return bind.value
    if bind.value is None:
        return None
    _, _, named = hint.partition(":")
    format_ = VECTOR_FORMATS.get(named or "float32")
    if format_ is None:
        raise ValidationError(
            f"Bind {bind.name!r} has type hint {bind.type_hint!r}; a vector format is one "
            f"of {', '.join(VECTOR_FORMATS)}.",
            detail={"bind": bind.name},
        )
    values = bind.value
    if not isinstance(values, list) or not values:
        raise ValidationError(
            f"Bind {bind.name!r} is hinted as a vector, so its value must be a non-empty "
            "array of numbers.",
            detail={"bind": bind.name},
        )
    for element in values:
        # bool is an int to Python, but true is not a vector element.
        if isinstance(element, bool) or not isinstance(element, int | float):
            raise ValidationError(
                f"Bind {bind.name!r} is hinted as a vector but holds {element!r}.",
                detail={"bind": bind.name},
            )
        if not math.isfinite(element):
            raise ValidationError(
                f"Bind {bind.name!r} holds {element!r}; a vector element must be finite.",
                detail={"bind": bind.name},
            )
        if format_ == "INT8" and not (isinstance(element, int) and -128 <= element <= 127):
            raise ValidationError(
                f"Bind {bind.name!r} is an INT8 vector, so {element!r} must be an integer "
                "from -128 to 127.",
                detail={"bind": bind.name},
            )
    return VectorBind(tuple(values), format_)
