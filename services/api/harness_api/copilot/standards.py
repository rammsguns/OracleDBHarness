"""The team's PL/SQL standards, given to Kiwi when it writes or reviews code.

An administrator points ``HARNESS_KIWI_STANDARDS_FILE`` at a small JSON document::

    {
      "namingPrefixes": {"package": "pkg_", "procedure": "prc_", "parameter": "p_"},
      "errorLoggingPackage": "app_log.error",
      "bulkCollectLimit": 500,
      "exceptionPolicy": "Never swallow WHEN OTHERS; log and re-raise.",
      "headerTemplate": "-- Purpose:\\n-- Author:\\n-- Change history:"
    }

Only the keys above are accepted, so a typo fails loudly instead of being ignored.
The file is read by the harness, never sent anywhere except inside the system prompt,
and Kiwi is told to cite the key of each standard it applies.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from harness_worker.errors import ConfigurationError

MAX_STANDARDS_BYTES = 16 * 1024

STANDARD_KEYS = (
    "namingPrefixes",
    "errorLoggingPackage",
    "bulkCollectLimit",
    "exceptionPolicy",
    "headerTemplate",
)


@dataclass(frozen=True)
class Standards:
    values: dict[str, Any] = field(default_factory=dict)

    @property
    def keys(self) -> list[str]:
        return [key for key in STANDARD_KEYS if key in self.values]

    def render(self) -> str:
        lines = [
            "",
            "Team standards. Follow them in any code you write or review. When a line of "
            'your answer applies one, cite it as "(standard: <key>)". If the user asks '
            "for something that breaks a standard, say which one and why before doing it.",
        ]
        for key in self.keys:
            value = self.values[key]
            if isinstance(value, dict):
                rendered = ", ".join(f"{kind}: {prefix}" for kind, prefix in value.items())
            else:
                rendered = str(value)
            lines.append(f"- {key}: {rendered}")
        return "\n".join(lines)


def parse_standards(raw: str, *, source: str = "standards file") -> Standards:
    if len(raw.encode("utf-8")) > MAX_STANDARDS_BYTES:
        raise ConfigurationError(
            f"The Kiwi {source} is larger than {MAX_STANDARDS_BYTES} bytes.",
            detail={"maxBytes": MAX_STANDARDS_BYTES},
        )
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigurationError(
            f"The Kiwi {source} is not valid JSON: {exc.msg} (line {exc.lineno}).",
        ) from exc
    if not isinstance(document, dict):
        raise ConfigurationError(f"The Kiwi {source} must be a JSON object.")
    unknown = sorted(set(document) - set(STANDARD_KEYS))
    if unknown:
        raise ConfigurationError(
            f"The Kiwi {source} has unknown keys: {', '.join(unknown)}.",
            detail={"unknown": unknown, "allowed": list(STANDARD_KEYS)},
        )

    prefixes = document.get("namingPrefixes")
    if prefixes is not None and not (
        isinstance(prefixes, dict)
        and all(isinstance(k, str) and isinstance(v, str) for k, v in prefixes.items())
    ):
        raise ConfigurationError("namingPrefixes must map object kinds to prefix strings.")
    limit = document.get("bulkCollectLimit")
    if limit is not None and (
        isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100_000
    ):
        raise ConfigurationError("bulkCollectLimit must be a whole number from 1 to 100000.")
    for key in ("errorLoggingPackage", "exceptionPolicy", "headerTemplate"):
        value = document.get(key)
        if value is not None and not isinstance(value, str):
            raise ConfigurationError(f"{key} must be a string.")
    return Standards(values={k: v for k, v in document.items() if v not in (None, "", {})})


def load_standards(path: str) -> Standards | None:
    """Read the configured file, or return None when none is configured."""

    if not path:
        return None
    file = Path(path)
    try:
        raw = file.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigurationError(
            f"The Kiwi standards file could not be read: {exc.strerror or exc}.",
            detail={"path": str(file)},
        ) from exc
    return parse_standards(raw)
