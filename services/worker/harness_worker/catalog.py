"""The reviewed query catalog.

MVP_PLAN.md requires a documented inventory of approved current-state queries, each
recording the privileges and database versions it needs. That inventory is the
``oracle/`` directory: one .sql file per operation, with a machine-readable header.
Nothing outside this catalog is dispatched as a "diagnostic" -- free-form SQL goes
through the worksheet path, where it is labelled as such and policy-gated separately.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from harness_worker.errors import ConfigurationError, NotFoundError
from harness_worker.types import Capability, RiskClass

_HEADER = re.compile(r"^--\s*@([A-Za-z_]+):\s*(.*)$")


@dataclass(frozen=True)
class CatalogEntry:
    """One reviewed operation: its SQL, its cost of entry and its blast radius."""

    operation_id: str
    title: str
    description: str
    sql: str
    capabilities: tuple[Capability, ...] = ()
    risk: RiskClass = RiskClass.READ
    min_version: int = 11
    parameters: tuple[str, ...] = ()
    identifier_parameters: tuple[str, ...] = ()
    privileges: tuple[str, ...] = ()
    source_path: Path | None = field(default=None, compare=False)
    returns: str = "rows"

    def describe(self) -> dict:
        return {
            "operationId": self.operation_id,
            "title": self.title,
            "description": self.description,
            "capabilities": [c.value for c in self.capabilities],
            "risk": self.risk.value,
            "minVersion": self.min_version,
            "parameters": list(self.parameters),
            "identifierParameters": list(self.identifier_parameters),
            "privileges": list(self.privileges),
            "returns": self.returns,
        }


def _split_list(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in value.split(",") if part.strip())


def parse_entry(path: Path) -> CatalogEntry:
    text = path.read_text(encoding="utf-8")
    headers: dict[str, str] = {}
    body_lines: list[str] = []
    in_header = True
    for line in text.splitlines():
        if in_header:
            match = _HEADER.match(line.strip())
            if match:
                headers[match.group(1).lower()] = match.group(2).strip()
                continue
            if not line.strip() or line.strip().startswith("--"):
                continue
            in_header = False
        body_lines.append(line)

    missing = {"id", "title"} - set(headers)
    if missing:
        raise ConfigurationError(
            f"Catalog entry {path.name} is missing header(s): {', '.join(sorted(missing))}",
            detail={"path": str(path)},
        )

    try:
        capabilities = tuple(
            Capability(value) for value in _split_list(headers.get("capabilities", ""))
        )
    except ValueError as exc:
        raise ConfigurationError(
            f"Catalog entry {path.name} names an unknown capability: {exc}",
            detail={"path": str(path)},
        ) from exc

    return CatalogEntry(
        operation_id=headers["id"],
        title=headers["title"],
        description=headers.get("description", ""),
        sql="\n".join(body_lines).strip(),
        capabilities=capabilities,
        risk=RiskClass(headers.get("risk", "read")),
        min_version=int(headers.get("min_version", "11")),
        parameters=_split_list(headers.get("parameters", "")),
        identifier_parameters=_split_list(headers.get("identifier_parameters", "")),
        privileges=_split_list(headers.get("privileges", "")),
        source_path=path,
        returns=headers.get("returns", "rows"),
    )


class QueryCatalog:
    """All reviewed operations, keyed by operation ID."""

    def __init__(self, entries: dict[str, CatalogEntry]) -> None:
        self._entries = entries

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, operation_id: object) -> bool:
        return operation_id in self._entries

    def get(self, operation_id: str) -> CatalogEntry:
        entry = self._entries.get(operation_id)
        if entry is None:
            raise NotFoundError(
                f"No reviewed operation is registered as {operation_id!r}.",
                detail={"operationId": operation_id},
            )
        return entry

    def list(self, prefix: str | None = None) -> list[CatalogEntry]:
        values = [
            entry
            for key, entry in sorted(self._entries.items())
            if prefix is None or key.startswith(prefix)
        ]
        return values

    def required_capabilities(self) -> set[Capability]:
        needed: set[Capability] = set()
        for entry in self._entries.values():
            needed.update(entry.capabilities)
        return needed


def load_catalog(root: Path) -> QueryCatalog:
    """Load every .sql file under ``root`` (recursively) into a catalog."""

    if not root.exists():
        raise ConfigurationError(
            f"The reviewed query directory {root} does not exist.", detail={"path": str(root)}
        )
    entries: dict[str, CatalogEntry] = {}
    for path in sorted(root.rglob("*.sql")):
        # grants/ holds reviewed DBA setup scripts. They are documentation for a human
        # to run, never operations the application dispatches.
        if path.name.startswith("_") or "grants" in path.relative_to(root).parts:
            continue
        entry = parse_entry(path)
        if entry.operation_id in entries:
            raise ConfigurationError(
                f"Duplicate operation ID {entry.operation_id!r} in {path}.",
                detail={"path": str(path)},
            )
        entries[entry.operation_id] = entry
    return QueryCatalog(entries)


def default_catalog_root() -> Path:
    """The repository ``oracle/`` directory, or an override from the environment."""

    import os

    override = os.environ.get("HARNESS_ORACLE_CATALOG_DIR")
    if override:
        return Path(override)
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "oracle"
        if (candidate / "diagnostics").is_dir():
            return candidate
    raise ConfigurationError(
        "Could not locate the oracle/ query catalog. Set HARNESS_ORACLE_CATALOG_DIR."
    )


@lru_cache(maxsize=4)
def cached_catalog(root: str | None = None) -> QueryCatalog:
    return load_catalog(Path(root) if root else default_catalog_root())
