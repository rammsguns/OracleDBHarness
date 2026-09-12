"""The API and the metadata store have to read the same password.

The Compose pilot mounts one secret file for PostgreSQL; the API reads that same
file rather than carrying a literal in its connection URL, so a deployment that sets
a real password does not leave the API authenticating with a placeholder.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from sqlalchemy.engine import make_url

from harness_api.config import Settings
from harness_api.db import resolve_metadata_url
from harness_worker.errors import ConfigurationError


def _settings(tmp_path: Path, **overrides) -> Settings:
    return Settings(
        env="development",
        metadata_url="postgresql+psycopg://harness@metadata:5432/harness",
        secret_dir=str(tmp_path),
        **overrides,
    )


def test_the_metadata_password_is_read_from_the_mounted_secret(tmp_path: Path) -> None:
    secret = tmp_path / "postgres_password"
    secret.write_text("a real pilot password\n", encoding="utf-8")

    url = resolve_metadata_url(_settings(tmp_path, metadata_password_file=str(secret)))
    assert url.password == "a real pilot password"
    assert url.username == "harness"
    # The value is not in the rendered URL, so it cannot leak through a log line.
    assert "a real pilot password" not in str(url)


def test_a_missing_password_file_is_a_configuration_error_not_a_silent_fallback(
    tmp_path: Path,
) -> None:
    with pytest.raises(ConfigurationError):
        resolve_metadata_url(_settings(tmp_path, metadata_password_file=str(tmp_path / "absent")))


def test_a_url_without_a_password_file_is_used_unchanged(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    assert resolve_metadata_url(settings) == make_url(settings.metadata_url)


@pytest.mark.skipif(
    not hasattr(os, "geteuid") or os.geteuid() == 0,
    reason="Needs POSIX file modes and a non-root user; root can read any mode.",
)
def test_an_unreadable_password_file_says_what_is_wrong(tmp_path: Path) -> None:
    """The failure a documented `chmod 600` used to produce, named rather than raw.

    Compose mounts a secret with the mode it has on the host, and the API runs as an
    unprivileged user that is not the operator who created the file. That combination exits
    at startup, and the bare PermissionError points at a path without saying that the mode
    came from the host or that the database container in the same deployment is unaffected,
    its entrypoint having read the same file as root. Found by the Compose install drill;
    see deploy/secrets/README.md.
    """

    secret = tmp_path / "postgres_password"
    secret.write_text("a real pilot password", encoding="utf-8")
    secret.chmod(0o000)

    try:
        with pytest.raises(ConfigurationError, match="cannot read it") as refused:
            resolve_metadata_url(_settings(tmp_path, metadata_password_file=str(secret)))
    finally:
        secret.chmod(0o600)

    assert refused.value.detail["path"] == str(secret)
    assert "deploy/secrets/README.md" in refused.value.message
    # The password must not be in the message or the detail of an error that gets logged.
    assert "a real pilot password" not in str(refused.value.as_dict())
