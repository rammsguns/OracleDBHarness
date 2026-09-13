"""The browser qualification's configuration, read from ``HARNESS_BROWSER_*``.

A namespace of its own, like ``HARNESS_QUAL_*`` for Oracle, so a deployment's ``.env``
can never start a run, and so a half-configured run fails naming what is missing rather
than qualifying something nobody intended.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

ENV_PREFIX = "HARNESS_BROWSER_"
ENV_MODE = f"{ENV_PREFIX}MODE"
ENV_ORIGIN = f"{ENV_PREFIX}CONSOLE_ORIGIN"
ENV_ISSUER = f"{ENV_PREFIX}OIDC_ISSUER"
ENV_CLIENT_ID = f"{ENV_PREFIX}CLIENT_ID"
ENV_LOGIN = f"{ENV_PREFIX}LOGIN"
ENV_USER = f"{ENV_PREFIX}USER"
ENV_PASSWORD_FILE = f"{ENV_PREFIX}PASSWORD_FILE"  # noqa: S105 - a path
ENV_UNREGISTERED_USER = f"{ENV_PREFIX}UNREGISTERED_USER"
ENV_UNREGISTERED_PASSWORD_FILE = f"{ENV_PREFIX}UNREGISTERED_PASSWORD_FILE"  # noqa: S105
ENV_CHECK_UNREGISTERED = f"{ENV_PREFIX}CHECK_UNREGISTERED"
ENV_FORBIDDEN_TARGET = f"{ENV_PREFIX}FORBIDDEN_TARGET_ID"
ENV_EXPIRY_WAIT = f"{ENV_PREFIX}EXPIRY_WAIT_SECONDS"
ENV_ALLOW_PARTIAL = f"{ENV_PREFIX}ALLOW_PARTIAL"
ENV_CHANNEL = f"{ENV_PREFIX}CHANNEL"
ENV_HEADED = f"{ENV_PREFIX}HEADED"
ENV_LOGIN_TIMEOUT = f"{ENV_PREFIX}LOGIN_TIMEOUT_SECONDS"
ENV_REPORT = f"{ENV_PREFIX}REPORT"

MODES = ("rehearsal", "fixture", "pilot")
LOGINS = ("form", "manual")
CHANNELS = ("chromium", "chrome", "msedge")

# The realm in tests/identity/keycloak/realm.json, whose redirect URIs fix the port.
FIXTURE_ORIGIN = "http://127.0.0.1:5173"
FIXTURE_ISSUER = "http://127.0.0.1:8180/realms/engineering"
FIXTURE_CLIENT_ID = "oracledbharness-console"
FIXTURE_USER = "alice"
# A fixture of the throwaway realm, committed in realm.json. Not a credential of anything.
FIXTURE_PASSWORD = "qualification-only"  # noqa: S105

NOT_CONFIGURED = (
    f"{ENV_MODE} is not set. Set it to rehearsal, fixture or pilot; see "
    "tests/identity/README.md, 'Browser qualification'."
)


class ConfigurationProblem(Exception):
    """The run was asked for but cannot be run as configured. Lists every problem."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("\n".join(f"- {problem}" for problem in problems))
        self.problems = problems


@dataclass(frozen=True)
class Account:
    """An identity-provider account. The password is never part of a repr or report."""

    username: str
    password: str = field(repr=False, default="")


@dataclass(frozen=True)
class BrowserConfig:
    mode: str
    origin: str
    issuer: str
    client_id: str
    login: str
    account: Account | None
    unregistered: Account | None
    check_unregistered: bool
    forbidden_target_id: str
    expiry_wait_seconds: float
    allow_partial: bool
    channel: str
    headed: bool
    login_timeout_seconds: float
    report_path: Path | None

    @property
    def qualifies_pilot(self) -> bool:
        return self.mode == "pilot"

    @property
    def starts_local_stack(self) -> bool:
        return self.mode in ("rehearsal", "fixture")


def is_configured() -> bool:
    return bool(_get(ENV_MODE))


def _get(name: str) -> str:
    return os.environ.get(name, "").strip()


def _flag(name: str) -> bool:
    return _get(name).lower() in ("1", "true", "yes")


def _read_password(variable: str, problems: list[str]) -> str:
    path = _get(variable)
    if not path:
        problems.append(f"{variable} is not set (the password is read from a file).")
        return ""
    file = Path(path)
    if not file.is_file():
        problems.append(f"{variable} points at {path!r}, which is not a file.")
        return ""
    password = file.read_text(encoding="utf-8").strip()
    if not password:
        problems.append(f"The password file named by {variable} is empty.")
    return password


def _is_loopback(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return host in ("127.0.0.1", "localhost", "::1")


def load() -> BrowserConfig:
    """Build the configuration, or raise with every problem found at once."""

    problems: list[str] = []
    mode = _get(ENV_MODE).lower()
    if not mode:
        raise ConfigurationProblem([NOT_CONFIGURED])
    if mode not in MODES:
        raise ConfigurationProblem([f"{ENV_MODE}={mode!r}; expected one of {', '.join(MODES)}."])

    origin = _get(ENV_ORIGIN).rstrip("/")
    issuer = _get(ENV_ISSUER)
    client_id = _get(ENV_CLIENT_ID)
    login = _get(ENV_LOGIN).lower() or ("manual" if mode == "pilot" else "form")
    channel = _get(ENV_CHANNEL).lower() or "chromium"

    if login not in LOGINS:
        problems.append(f"{ENV_LOGIN}={login!r}; expected form or manual.")
    if channel not in CHANNELS:
        problems.append(f"{ENV_CHANNEL}={channel!r}; expected one of {', '.join(CHANNELS)}.")

    if mode == "pilot":
        for variable, value in (
            (ENV_ORIGIN, origin),
            (ENV_ISSUER, issuer),
            (ENV_CLIENT_ID, client_id),
        ):
            if not value:
                problems.append(
                    f"{variable} is required in pilot mode: the run checks the deployment "
                    "serves exactly this, rather than trusting whatever it is configured with."
                )
        if issuer.rstrip("/") == FIXTURE_ISSUER or (
            _is_loopback(issuer) and client_id == FIXTURE_CLIENT_ID
        ):
            problems.append(
                "Pilot mode names the local Keycloak fixture. It qualifies the pilot's own "
                "registration; use HARNESS_BROWSER_MODE=fixture for the fixture."
            )
        if login == "form":
            problems.append(
                f"{ENV_LOGIN}=form is refused in pilot mode. Sign in by hand in the browser "
                "window: login forms and second factors differ by provider, and a pilot "
                "account's password has no place in a test run."
            )
    elif mode == "fixture":
        origin = origin or FIXTURE_ORIGIN
        issuer = issuer or FIXTURE_ISSUER
        client_id = client_id or FIXTURE_CLIENT_ID
    else:
        origin = origin or FIXTURE_ORIGIN
        client_id = client_id or FIXTURE_CLIENT_ID
        if issuer:
            problems.append(
                f"{ENV_ISSUER} is ignored in rehearsal mode, which starts its own stand-in "
                "provider. Unset it, or use fixture or pilot mode."
            )

    if mode != "pilot" and not _is_loopback(origin):
        problems.append(
            f"{mode} mode starts the console locally, so {ENV_ORIGIN} must be a loopback "
            f"origin; got {origin!r}."
        )

    account: Account | None = None
    unregistered: Account | None = None
    if login == "form":
        if mode == "fixture":
            account = Account(_get(ENV_USER) or FIXTURE_USER, FIXTURE_PASSWORD)
            if _get(ENV_PASSWORD_FILE):
                account = Account(account.username, _read_password(ENV_PASSWORD_FILE, problems))
        elif mode == "rehearsal":
            account = None  # the stand-in provider creates its own account for the run
    unregistered_user = _get(ENV_UNREGISTERED_USER)
    if unregistered_user:
        password = (
            _read_password(ENV_UNREGISTERED_PASSWORD_FILE, problems) if login == "form" else ""
        )
        unregistered = Account(unregistered_user, password)

    expiry_text = _get(ENV_EXPIRY_WAIT)
    try:
        expiry_wait = float(expiry_text) if expiry_text else (0.0 if mode == "pilot" else 180.0)
    except ValueError:
        problems.append(f"{ENV_EXPIRY_WAIT}={expiry_text!r} is not a number of seconds.")
        expiry_wait = 0.0

    timeout_text = _get(ENV_LOGIN_TIMEOUT)
    try:
        login_timeout = float(timeout_text) if timeout_text else 300.0
    except ValueError:
        problems.append(f"{ENV_LOGIN_TIMEOUT}={timeout_text!r} is not a number of seconds.")
        login_timeout = 300.0

    if _flag(ENV_ALLOW_PARTIAL) and mode != "pilot":
        problems.append(f"{ENV_ALLOW_PARTIAL} only means something in pilot mode.")

    if problems:
        raise ConfigurationProblem(problems)

    report = _get(ENV_REPORT)
    return BrowserConfig(
        mode=mode,
        origin=origin,
        issuer=issuer,
        client_id=client_id,
        login=login,
        account=account,
        unregistered=unregistered,
        check_unregistered=bool(unregistered) or _flag(ENV_CHECK_UNREGISTERED),
        forbidden_target_id=_get(ENV_FORBIDDEN_TARGET),
        expiry_wait_seconds=expiry_wait,
        allow_partial=_flag(ENV_ALLOW_PARTIAL),
        channel=channel,
        headed=login == "manual" or _flag(ENV_HEADED),
        login_timeout_seconds=login_timeout,
        report_path=Path(report) if report else None,
    )
