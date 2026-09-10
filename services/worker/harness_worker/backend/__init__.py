"""Backend selection.

``fake`` is for local development, tests and demonstrations. ``oracledb`` talks to a
real database. Nothing above this package knows which one is configured.
"""

from __future__ import annotations

from harness_worker.backend.base import (
    ConnectionSpec,
    OracleBackend,
    OracleConnection,
    StatementResult,
)
from harness_worker.errors import ConfigurationError

__all__ = [
    "ConnectionSpec",
    "OracleBackend",
    "OracleConnection",
    "StatementResult",
    "create_backend",
]


def create_backend(
    kind: str,
    *,
    driver_mode: str = "thin",
    lib_dir: str | None = None,
    fake_data_dir: str | None = None,
) -> OracleBackend:
    if kind == "fake":
        from harness_worker.backend.fake import FakeOracleBackend

        return FakeOracleBackend(fake_data_dir)
    if kind in ("oracledb", "oracle"):
        from harness_worker.backend.oracle import OracleDbBackend

        return OracleDbBackend(driver_mode=driver_mode, lib_dir=lib_dir)
    raise ConfigurationError(
        f"Unknown Oracle backend {kind!r}. Use 'oracledb' or 'fake'.",
        detail={"configured": kind},
    )
