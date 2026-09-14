"""What a browser run found, and the verdict it is allowed to reach.

The report never contains a token, an authorization code or a password. Every such value
the run sees is registered here the moment it is seen, and writing refuses - rather than
masks - any text that still contains one, or anything shaped like a JWT or a callback
code. A report that had to be scrubbed would be one nobody could trust was scrubbed.
"""

from __future__ import annotations

import datetime as dt
import json
import platform
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PASSED = "passed"
FAILED = "failed"
SKIPPED = "skipped"
#: A fact worth recording that is neither a pass nor a failure, such as a token staying
#: valid at the API after the console signs out.
OBSERVED = "observed"

_JWT = re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")
_CODE = re.compile(r"[?&#](code|access_token|id_token)=(?!\[redacted\])[^&\s]+")


class LeakError(AssertionError):
    """The report text contains something it must never contain."""


def _commit() -> str:
    try:
        result = subprocess.run(  # noqa: S603 - fixed argument list
            ["git", "rev-parse", "--short", "HEAD"],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return result.stdout.strip() or "unknown"


@dataclass
class Check:
    id: str
    title: str
    outcome: str
    detail: str = ""


@dataclass
class Report:
    mode: str
    origin: str
    issuer: str = ""
    client_id: str = ""
    allow_partial: bool = False
    browser: str = ""
    started: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.UTC))
    commit: str = field(default_factory=_commit)
    checks: list[Check] = field(default_factory=list)
    _secrets: set[str] = field(default_factory=set, repr=False)

    # -- recording -------------------------------------------------------------------

    def secret(self, value: str | None) -> None:
        """Register a value that must never appear in the report."""

        if value and len(value) >= 6:
            self._secrets.add(value)

    def record(self, check_id: str, title: str, outcome: str, detail: str = "") -> Check:
        check = Check(check_id, title, outcome, detail)
        self.checks.append(check)
        return check

    def passed(self, check_id: str, title: str, detail: str = "") -> Check:
        return self.record(check_id, title, PASSED, detail)

    def failed(self, check_id: str, title: str, detail: str) -> Check:
        return self.record(check_id, title, FAILED, detail)

    def skipped(self, check_id: str, title: str, reason: str) -> Check:
        return self.record(check_id, title, SKIPPED, reason)

    def observed(self, check_id: str, title: str, detail: str) -> Check:
        return self.record(check_id, title, OBSERVED, detail)

    def expect(self, check_id: str, title: str, condition: bool, detail: str) -> bool:
        self.record(check_id, title, PASSED if condition else FAILED, detail)
        return condition

    # -- the verdict -----------------------------------------------------------------

    @property
    def failures(self) -> list[Check]:
        return [check for check in self.checks if check.outcome == FAILED]

    @property
    def skips(self) -> list[Check]:
        return [check for check in self.checks if check.outcome == SKIPPED]

    @property
    def qualified(self) -> bool:
        """Only a complete, clean pilot run qualifies the pilot's identity registration."""

        return self.mode == "pilot" and bool(self.checks) and not self.failures and not self.skips

    @property
    def succeeded(self) -> bool:
        """Whether the run did what it was asked to. The exit code follows this."""

        if self.failures or not self.checks:
            return False
        if self.mode == "pilot":
            return self.qualified or self.allow_partial
        return True

    @property
    def verdict(self) -> str:
        if not self.checks:
            return "INCOMPLETE - no checks ran"
        if self.failures:
            return f"FAILED - {len(self.failures)} check(s) failed"
        if self.mode == "rehearsal":
            return (
                "REHEARSAL PASSED - a local stand-in identity provider; evidence about this "
                "tooling only, not about any identity provider or the pilot"
            )
        if self.mode == "fixture":
            return (
                "FIXTURE PASSED - the local Keycloak realm and a local console preview; not "
                "qualification of the pilot identity registration, origin or proxy"
            )
        if self.skips:
            state = "PARTIAL" if self.allow_partial else "NOT QUALIFIED"
            return f"{state} - {len(self.skips)} check(s) did not run"
        return "QUALIFIED - pilot identity registration, deployed origin and proxy"

    # -- output ----------------------------------------------------------------------

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": "browser-sign-in-qualification",
            "mode": self.mode,
            "verdict": self.verdict,
            "qualified": self.qualified,
            "started": self.started.isoformat(),
            "harnessCommit": self.commit,
            "consoleOrigin": self.origin,
            "issuer": self.issuer,
            "clientId": self.client_id,
            "browser": self.browser,
            "platform": f"{platform.platform()}, Python {platform.python_version()}",
            "allowPartial": self.allow_partial,
            "checks": [check.__dict__ for check in self.checks],
        }

    def as_markdown(self) -> str:
        lines = [
            f"### Browser sign-in ({self.mode}) {self.started:%Y-%m-%d %H:%M} UTC",
            "",
            f"**{self.verdict}**",
            "",
            f"- Harness commit: `{self.commit}`",
            f"- Console origin: `{self.origin}`",
            f"- Issuer: `{self.issuer}`",
            f"- Client: `{self.client_id}`",
            f"- Browser: {self.browser or 'not started'}",
            f"- Platform: {platform.platform()}, Python {platform.python_version()}",
            "",
            "| Check | Outcome | Detail |",
            "| --- | --- | --- |",
        ]
        for check in self.checks:
            detail = check.detail.replace("|", "\\|").replace("\n", "<br>")
            lines.append(f"| {check.title} | {check.outcome} | {detail} |")
        lines.append("")
        return "\n".join(lines)

    def assert_clean(self, text: str) -> None:
        for value in self._secrets:
            if value in text:
                raise LeakError(
                    "The report contains a token, code or password seen during the run."
                )
        if _JWT.search(text):
            raise LeakError("The report contains something shaped like a JWT.")
        if _CODE.search(text):
            raise LeakError("The report contains a callback code or token parameter.")

    def write(self, path: Path) -> None:
        markdown = self.as_markdown()
        payload = json.dumps(self.as_dict(), indent=2)
        self.assert_clean(markdown)
        self.assert_clean(payload)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(markdown + "\n")
        path.with_suffix(".json").write_text(payload, encoding="utf-8")
