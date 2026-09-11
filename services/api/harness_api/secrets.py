"""Secret resolution.

Database passwords never reach the metadata store, the browser, an execution record,
or a log line. A profile carries a SecretReference; this module turns that reference
into a value at connection time and nothing keeps it afterwards.

The file provider reads a mounted secret directory, which is what the Compose pilot
uses. The env provider is for local development. Both refuse to traverse outside
their configured root.
"""

from __future__ import annotations

import os
from pathlib import Path

from harness_api.models import SecretReference
from harness_worker.errors import ConfigurationError, NotFoundError


class SecretResolver:
    def __init__(self, secret_dir: str) -> None:
        self._root = Path(secret_dir).resolve()

    def resolve(self, reference: SecretReference) -> str:
        if reference.provider == "file":
            return self._read_file(reference.locator)
        if reference.provider == "env":
            value = os.environ.get(reference.locator)
            if value is None:
                raise NotFoundError(
                    f"Secret reference {reference.name!r} points at environment variable "
                    f"{reference.locator!r}, which is not set.",
                    detail={"secretName": reference.name},
                )
            return value
        raise ConfigurationError(
            f"Unknown secret provider {reference.provider!r} on reference {reference.name!r}.",
            detail={"secretName": reference.name},
        )

    def _read_file(self, locator: str) -> str:
        candidate = (self._root / locator).resolve()
        # Containment is a path relationship, not a string prefix: "/run/secrets" is a
        # prefix of "/run/secrets-other/key", which is outside the directory.
        if candidate == self._root or not candidate.is_relative_to(self._root):
            raise ConfigurationError(
                "A secret locator tried to read outside the configured secret directory.",
                detail={"locator": locator},
            )
        if not candidate.is_file():
            raise NotFoundError(
                "The referenced secret file does not exist. Mount it, or point the "
                "reference somewhere else.",
                detail={"locator": locator, "secretDir": str(self._root)},
            )
        # A trailing newline from `echo` or a Kubernetes secret is not part of the
        # password; anything else, including inner whitespace, is preserved.
        return candidate.read_text(encoding="utf-8").rstrip("\r\n")

    def describe(self, reference: SecretReference) -> dict:
        """Enough to diagnose a misconfiguration, with no part of the value."""

        available = True
        detail = ""
        try:
            self.resolve(reference)
        except Exception as exc:  # noqa: BLE001 - the failure is the answer
            available = False
            detail = str(exc)
        return {
            "id": reference.id,
            "name": reference.name,
            "provider": reference.provider,
            "locator": reference.locator,
            "resolvable": available,
            "detail": detail,
        }
