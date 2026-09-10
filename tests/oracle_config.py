"""The explicit Oracle test configuration, shared by every suite that can use one.

Without it the whole test suite runs against the local stand-in, because
``tests/conftest.py`` builds ``Settings`` directly. Flipping ``HARNESS_ORACLE_BACKEND``
does not change that and never could.

This module is the switch, for three suites at once:

* ``tests/qualification`` drives the backend directly and does not run at all without
  it.
* ``tests/integration`` and ``tests/e2e`` go through the API. With this configured they
  run against Oracle; without it, against the stand-in exactly as before.

It reads its own ``HARNESS_QUAL_*`` namespace so a run can never be started by accident
from a deployment's ``.env``, and so a half-configured run fails with a specific
message instead of silently connecting somewhere unintended.

Set at minimum::

    HARNESS_QUAL_ORACLE_DSN=dbhost:1521/ORCLPDB1
    HARNESS_QUAL_ORACLE_USER=harness_app
    HARNESS_QUAL_ORACLE_PASSWORD_FILE=/run/secrets/harness_app.password

Point it at a NON-PRODUCTION database. The fixtures drop and recreate their own
objects on every run, under the ordinary names a sample schema uses
(``EMPLOYEES``, ``DEPARTMENTS``, ``ORDER_LINES``, ``EMPLOYEE_REPORT``), and the
cancellation and connection-loss checks deliberately break sessions.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
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

# A genuinely separate database, used as the "test" target. The isolation criteria
# are about two targets not mixing identity, credentials or session state, and two
# profiles pointing at one database cannot demonstrate that.
ENV_SECOND_DSN = f"{ENV_PREFIX}SECOND_DSN"
ENV_SECOND_USER = f"{ENV_PREFIX}SECOND_USER"
ENV_SECOND_PASSWORD_FILE = f"{ENV_PREFIX}SECOND_PASSWORD_FILE"  # noqa: S105 - a path
ENV_SECOND_SCHEMA = f"{ENV_PREFIX}SECOND_SCHEMA"

SKIP_REASON = (
    f"No Oracle target is configured. Set {ENV_DSN} (and {ENV_USER}, "
    f"{ENV_PASSWORD_FILE}) to run against a real database. "
    "See oracle/qualification/README.md."
)

# The demonstration schema, which the stand-in seeds and oracle/qualification/ mirrors.
# The integration and end-to-end suites name it in queries, runbook parameters and
# object lookups, so a qualification schema called anything else fails them all for a
# reason that has nothing to do with the harness. The qualification suite itself does
# not care and will use whatever ORACLE_SCHEMA says.
DEMO_SCHEMA = "HARNESS_APP"

WRONG_SCHEMA_REASON = (
    f"The integration and end-to-end suites address the demonstration schema by name "
    f"({DEMO_SCHEMA}). Set {ENV_SCHEMA}={DEMO_SCHEMA} - or leave it unset, which is "
    f"the default - and create the fixture objects there. Only tests/qualification "
    f"can run against a differently named schema."
)

NO_SECOND_TARGET_REASON = (
    f"{ENV_SECOND_DSN} is not set. This check needs two genuinely separate databases; "
    "two profiles pointing at one cannot show that identity, credentials and session "
    "state stay apart."
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
class Endpoint:
    """One account on one database.

    Carries exactly the fields a ``ConnectionProfile`` has columns for, so the demo
    seed can be pointed at a real target without inventing anything the application
    could not express.
    """

    host: str
    port: int
    service_name: str
    username: str
    password: str = field(repr=False)
    schema: str

    @property
    def dsn(self) -> str:
        return f"{self.host}:{self.port}/{self.service_name}"


@dataclass(frozen=True)
class OracleTestConfig:
    """A configured Oracle target, and optionally a second one and a privileged one."""

    primary: Endpoint
    second: Endpoint | None
    admin: Endpoint | None
    driver_mode: str
    client_lib_dir: str
    protocol: str
    wallet_dir: str | None
    report_path: Path | None

    # -- convenience for the qualification suite, which only uses the primary ------

    @property
    def host(self) -> str:
        return self.primary.host

    @property
    def port(self) -> int:
        return self.primary.port

    @property
    def service_name(self) -> str:
        return self.primary.service_name

    @property
    def username(self) -> str:
        return self.primary.username

    @property
    def schema(self) -> str:
        return self.primary.schema

    @property
    def dsn(self) -> str:
        return self.primary.dsn

    def spec_for(self, endpoint: Endpoint, profile_id: str) -> ConnectionSpec:
        return ConnectionSpec(
            profile_id=profile_id,
            host=endpoint.host,
            port=endpoint.port,
            service_name=endpoint.service_name,
            username=endpoint.username,
            password=endpoint.password,
            driver_mode=self.driver_mode,
            protocol=self.protocol,
            wallet_dir=self.wallet_dir,
            default_schema=endpoint.schema,
        )

    def connection_spec(self, profile_id: str = "qualification") -> ConnectionSpec:
        return self.spec_for(self.primary, profile_id)

    def second_spec(self, profile_id: str = "qualification-second-target") -> ConnectionSpec | None:
        if self.second is None:
            return None
        return self.spec_for(self.second, profile_id)

    def admin_spec(self) -> ConnectionSpec | None:
        """A privileged connection used to kill sessions.

        Optional. Without it the connection-loss checks cannot run, because the only
        honest way to lose a connection is for something outside it to end the
        session. It connects as itself, not into the fixture schema.
        """

        if self.admin is None:
            return None
        spec = self.spec_for(self.admin, "qualification-admin")
        return replace(spec, default_schema=None)


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

    primary = Endpoint(
        host=host,
        port=port,
        service_name=service,
        username=username,
        password=_read_password(ENV_PASSWORD_FILE),
        schema=os.environ.get(ENV_SCHEMA, "").strip().upper() or DEMO_SCHEMA,
    )

    second = _optional_endpoint(
        ENV_SECOND_DSN, ENV_SECOND_USER, ENV_SECOND_PASSWORD_FILE, ENV_SECOND_SCHEMA
    )
    if second is not None and second.dsn == primary.dsn:
        raise ConfigurationProblem(
            f"{ENV_SECOND_DSN} names the same database as {ENV_DSN} ({primary.dsn}). "
            "The isolation checks compare two targets and would pass trivially."
        )

    admin = _optional_endpoint(ENV_ADMIN_DSN, ENV_ADMIN_USER, ENV_ADMIN_PASSWORD_FILE, None)

    report = os.environ.get(ENV_REPORT_PATH, "").strip()

    return OracleTestConfig(
        primary=primary,
        second=second,
        admin=admin,
        driver_mode=driver_mode,
        client_lib_dir=client_lib_dir,
        protocol=os.environ.get(ENV_PROTOCOL, "tcp").strip() or "tcp",
        wallet_dir=os.environ.get(ENV_WALLET_DIR, "").strip() or None,
        report_path=Path(report) if report else None,
    )


def _optional_endpoint(
    dsn_var: str, user_var: str, password_var: str, schema_var: str | None
) -> Endpoint | None:
    """Build an endpoint from a group of variables, or None if its DSN is unset.

    Setting the DSN and nothing else is a configuration error rather than a silent
    fallback: a half-named endpoint means someone intended to configure one.
    """

    dsn = os.environ.get(dsn_var, "").strip()
    if not dsn:
        return None
    host, port, service = _split_dsn(dsn, dsn_var)
    username = os.environ.get(user_var, "").strip()
    if not username:
        raise ConfigurationProblem(f"{dsn_var} is set but {user_var} is not.")
    schema = os.environ.get(schema_var, "").strip() if schema_var else ""
    return Endpoint(
        host=host,
        port=port,
        service_name=service,
        username=username,
        password=_read_password(password_var),
        schema=schema or username.upper(),
    )


def has_second_target() -> bool:
    return bool(os.environ.get(ENV_SECOND_DSN, "").strip())
