"""The explicit Oracle test configuration.

The rest of the suite runs against the local stand-in because ``tests/conftest.py``
asks for it by name. Flipping ``HARNESS_ORACLE_BACKEND`` does not change that and was
never going to: the fixtures construct ``Settings`` directly.

This module is the switch. It reads its own ``HARNESS_QUAL_*`` namespace so a
qualification run can never be started by accident from a deployment's ``.env``, and
so a half-configured run fails with a specific message instead of silently connecting
somewhere unintended.

Set at minimum::

    HARNESS_QUAL_ORACLE_DSN=dbhost:1521/ORCLPDB1
    HARNESS_QUAL_ORACLE_USER=harness_qual
    HARNESS_QUAL_ORACLE_PASSWORD_FILE=/run/secrets/harness_qual.password

Point it at a NON-PRODUCTION database. The fixtures drop and recreate their own
objects on every run, and the cancellation and connection-loss checks deliberately
break sessions.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from harness_worker.backend.base import ConnectionSpec

ENV_PREFIX = "HARNESS_QUAL_"

#: Presence of this variable is what turns the qualification suite on.
ENV_DSN = f"{ENV_PREFIX}ORACLE_DSN"
ENV_USER = f"{ENV_PREFIX}ORACLE_USER"
ENV_PASSWORD_FILE = f"{ENV_PREFIX}ORACLE_PASSWORD_FILE"  # noqa: S105 - a path, not a password
ENV_DRIVER_MODE = f"{ENV_PREFIX}DRIVER_MODE"
ENV_CLIENT_LIB_DIR = f"{ENV_PREFIX}ORACLE_CLIENT_LIB_DIR"
ENV_SCHEMA = f"{ENV_PREFIX}ORACLE_SCHEMA"
ENV_PROTOCOL = f"{ENV_PREFIX}PROTOCOL"
ENV_WALLET_DIR = f"{ENV_PREFIX}WALLET_DIR"
ENV_ADMIN_DSN = f"{ENV_PREFIX}ADMIN_DSN"
ENV_ADMIN_USER = f"{ENV_PREFIX}ADMIN_USER"
ENV_ADMIN_PASSWORD_FILE = f"{ENV_PREFIX}ADMIN_PASSWORD_FILE"  # noqa: S105 - a path
ENV_REPORT_PATH = f"{ENV_PREFIX}REPORT"

SKIP_REASON = (
    f"Oracle qualification is not configured. Set {ENV_DSN} (and {ENV_USER}, "
    f"{ENV_PASSWORD_FILE}) to run this suite against a real database. "
    "See oracle/qualification/README.md."
)


class ConfigurationProblem(Exception):
    """The suite was switched on but cannot be run as configured.

    Raised rather than skipped: a qualification run that quietly does nothing is
    worse than one that fails, because it is indistinguishable from a passing run in
    a CI summary.
    """


def _read_password(variable: str) -> str:
    path = os.environ.get(variable, "").strip()
    if not path:
        raise ConfigurationProblem(
            f"{variable} is not set. The password is read from a mounted file so it "
            "does not appear in the environment, shell history or a process listing."
        )
    file = Path(path)
    if not file.is_file():
        raise ConfigurationProblem(f"{variable} points at {path!r}, which is not a file.")
    password = file.read_text(encoding="utf-8").strip()
    if not password:
        raise ConfigurationProblem(f"The password file {path!r} is empty.")
    return password


def _split_dsn(dsn: str, variable: str) -> tuple[str, int, str]:
    """Split ``host:port/service`` into its parts.

    Only this one form is accepted. A full connect descriptor or a tnsnames alias
    would not round-trip through ``ConnectionSpec``, which the harness builds from a
    profile's separate host, port and service columns; accepting one here would
    qualify a connection path the application cannot use.
    """

    host, _, remainder = dsn.partition(":")
    port_text, _, service = remainder.partition("/")
    if not (host and port_text and service):
        raise ConfigurationProblem(
            f"{variable}={dsn!r} is not in the form host:port/service, which is the "
            "only form a connection profile can express."
        )
    if not port_text.isdigit():
        raise ConfigurationProblem(f"{variable}={dsn!r} has a non-numeric port.")
    return host, int(port_text), service


@dataclass(frozen=True)
class OracleTestConfig:
    """One configured qualification target."""

    host: str
    port: int
    service_name: str
    username: str
    password: str = field(repr=False)
    schema: str
    driver_mode: str
    client_lib_dir: str
    protocol: str
    wallet_dir: str | None
    admin: tuple[str, int, str, str, str] | None
    report_path: Path | None

    @property
    def dsn(self) -> str:
        return f"{self.host}:{self.port}/{self.service_name}"

    def connection_spec(self, profile_id: str = "qualification") -> ConnectionSpec:
        return ConnectionSpec(
            profile_id=profile_id,
            host=self.host,
            port=self.port,
            service_name=self.service_name,
            username=self.username,
            password=self.password,
            driver_mode=self.driver_mode,
            protocol=self.protocol,
            wallet_dir=self.wallet_dir,
            default_schema=self.schema,
        )

    def admin_spec(self) -> ConnectionSpec | None:
        """A second, privileged connection used to kill sessions.

        Optional. Without it the connection-loss checks cannot run, because the only
        honest way to lose a connection is for something outside it to end the
        session.
        """

        if self.admin is None:
            return None
        host, port, service, username, password = self.admin
        return ConnectionSpec(
            profile_id="qualification-admin",
            host=host,
            port=port,
            service_name=service,
            username=username,
            password=password,
            driver_mode=self.driver_mode,
            protocol=self.protocol,
            wallet_dir=self.wallet_dir,
        )


def is_configured() -> bool:
    return bool(os.environ.get(ENV_DSN, "").strip())


def load() -> OracleTestConfig:
    """Build the configuration, or explain precisely what is missing."""

    dsn = os.environ.get(ENV_DSN, "").strip()
    if not dsn:
        raise ConfigurationProblem(SKIP_REASON)
    host, port, service = _split_dsn(dsn, ENV_DSN)

    username = os.environ.get(ENV_USER, "").strip()
    if not username:
        raise ConfigurationProblem(f"{ENV_USER} is not set.")

    driver_mode = os.environ.get(ENV_DRIVER_MODE, "thin").strip().lower()
    if driver_mode not in ("thin", "thick"):
        raise ConfigurationProblem(f"{ENV_DRIVER_MODE} must be 'thin' or 'thick'.")
    client_lib_dir = os.environ.get(ENV_CLIENT_LIB_DIR, "").strip()
    if driver_mode == "thick" and not client_lib_dir:
        raise ConfigurationProblem(
            f"{ENV_DRIVER_MODE}=thick needs {ENV_CLIENT_LIB_DIR} to point at the "
            "Oracle Client libraries. Driver mode is process wide, so a thick run "
            "must be a separate process from a thin one."
        )

    admin: tuple[str, int, str, str, str] | None = None
    admin_dsn = os.environ.get(ENV_ADMIN_DSN, "").strip()
    if admin_dsn:
        admin_host, admin_port, admin_service = _split_dsn(admin_dsn, ENV_ADMIN_DSN)
        admin_user = os.environ.get(ENV_ADMIN_USER, "").strip()
        if not admin_user:
            raise ConfigurationProblem(f"{ENV_ADMIN_DSN} is set but {ENV_ADMIN_USER} is not.")
        admin = (
            admin_host,
            admin_port,
            admin_service,
            admin_user,
            _read_password(ENV_ADMIN_PASSWORD_FILE),
        )

    report = os.environ.get(ENV_REPORT_PATH, "").strip()

    return OracleTestConfig(
        host=host,
        port=port,
        service_name=service,
        username=username,
        password=_read_password(ENV_PASSWORD_FILE),
        schema=os.environ.get(ENV_SCHEMA, "").strip() or username.upper(),
        driver_mode=driver_mode,
        client_lib_dir=client_lib_dir,
        protocol=os.environ.get(ENV_PROTOCOL, "tcp").strip() or "tcp",
        wallet_dir=os.environ.get(ENV_WALLET_DIR, "").strip() or None,
        admin=admin,
        report_path=Path(report) if report else None,
    )
