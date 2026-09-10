"""Records what a qualification run actually observed.

docs/compatibility.md asks for the exact database version, patch level, character set
and driver mode with every run. Asking a person to copy those down by hand is how a
compatibility table ends up describing a database nobody has connected to in a year,
so the suite collects them itself and writes a block ready to append.

The report is written even when tests fail. A failed run against a recorded version
is evidence; an unrecorded one is not.
"""

from __future__ import annotations

import datetime as dt
import json
import platform
import subprocess
from dataclasses import dataclass, field
from typing import Any

from tests.qualification.config import OracleTestConfig


def _harness_build() -> str:
    """The commit the harness was at, so a result can be tied to a build."""

    try:
        result = subprocess.run(  # noqa: S603 - fixed argument list, no shell
            ["git", "rev-parse", "--short", "HEAD"],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return result.stdout.strip() or "unknown"


def driver_versions(driver_mode: str) -> dict[str, str]:
    """python-oracledb's own version, and the client library when in thick mode."""

    versions: dict[str, str] = {"driverMode": driver_mode}
    try:
        import oracledb
    except ImportError:
        versions["pythonOracledb"] = "not installed"
        return versions

    versions["pythonOracledb"] = getattr(oracledb, "__version__", "unknown")
    if driver_mode == "thick":
        try:
            client = oracledb.clientversion()
        except Exception as exc:  # noqa: BLE001 - reported, not raised
            versions["oracleClient"] = f"unavailable: {exc}"
        else:
            versions["oracleClient"] = ".".join(str(part) for part in client)
    return versions


@dataclass
class Evidence:
    """Collected facts about one qualification run."""

    config: OracleTestConfig
    started: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.UTC))
    database: dict[str, Any] = field(default_factory=dict)
    observations: dict[str, str] = field(default_factory=dict)

    def record_database(self, values: dict[str, Any]) -> None:
        self.database.update(values)

    def note(self, key: str, value: str) -> None:
        """Record something only a real database could have told us.

        Used for the answers the gap table in docs/compatibility.md is waiting for:
        which ORA code a broken commit raises, what a cancelled statement leaves
        behind, and so on.
        """

        self.observations[key] = value

    def as_markdown(self) -> str:
        lines = [
            f"### Run {self.started:%Y-%m-%d %H:%M} UTC",
            "",
            f"- Harness build: `{_harness_build()}`",
            f"- Target: `{self.config.dsn}` as `{self.config.username}`, "
            f"schema `{self.config.schema}`",
            f"- Platform: {platform.platform()}, Python {platform.python_version()}",
        ]
        for key, value in driver_versions(self.config.driver_mode).items():
            lines.append(f"- {key}: `{value}`")
        if self.database:
            lines.append("")
            lines.append("Database, as reported by the database:")
            lines.append("")
            for key, value in sorted(self.database.items()):
                lines.append(f"- {key}: `{value}`")
        if self.observations:
            lines.append("")
            lines.append("Observed Oracle behaviour:")
            lines.append("")
            lines.append("| Question | What this database did |")
            lines.append("| --- | --- |")
            for key, value in sorted(self.observations.items()):
                lines.append(f"| {key} | {value} |")
        else:
            lines.append("")
            lines.append(
                "No behavioural observations were recorded, which means the suite did "
                "not get far enough to make any. Treat this run as incomplete."
            )
        lines.append("")
        return "\n".join(lines)

    def write(self) -> None:
        """Write the report, if one was asked for.

        Appends. A compatibility record that replaces its predecessor cannot show a
        regression between two database versions.
        """

        path = self.config.report_path
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(self.as_markdown())
            handle.write("\n")
        sidecar = path.with_suffix(".json")
        payload = {
            "started": self.started.isoformat(),
            "harnessBuild": _harness_build(),
            "target": {
                "dsn": self.config.dsn,
                "username": self.config.username,
                "schema": self.config.schema,
            },
            "driver": driver_versions(self.config.driver_mode),
            "database": self.database,
            "observations": self.observations,
        }
        sidecar.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
