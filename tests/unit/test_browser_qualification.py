"""The browser sign-in qualification's decisions, checked without a browser.

What decides whether a browser run can be trusted is not the browser: it is whether a
local fixture can be mistaken for the pilot, whether a skipped check can read as a
qualification, and whether a token can reach the report. Those are checked here, along
with the stand-in provider's enforcement of PKCE, so ordinary CI covers them.
"""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from tests.browser import config as browser_config
from tests.browser.report import LeakError, Report
from tests.browser.runner import _claims, _tamper, report_safe
from tests.browser.stub_provider import StubProvider


def configure(monkeypatch: pytest.MonkeyPatch, **values: str) -> None:
    for name in dir(browser_config):
        if name.startswith("ENV_") and name != "ENV_PREFIX":
            monkeypatch.delenv(getattr(browser_config, name), raising=False)
    for key, value in values.items():
        monkeypatch.setenv(key, value)


PILOT = {
    "HARNESS_BROWSER_MODE": "pilot",
    "HARNESS_BROWSER_CONSOLE_ORIGIN": "https://harness.pilot.example",
    "HARNESS_BROWSER_OIDC_ISSUER": "https://login.pilot.example/tenant/v2.0",
    "HARNESS_BROWSER_CLIENT_ID": "harness-console",
}


# -- configuration ---------------------------------------------------------------------


def test_nothing_runs_unless_a_mode_is_named(monkeypatch: pytest.MonkeyPatch) -> None:
    configure(monkeypatch)
    assert browser_config.is_configured() is False
    with pytest.raises(browser_config.ConfigurationProblem, match="HARNESS_BROWSER_MODE"):
        browser_config.load()


def test_pilot_mode_needs_the_registration_it_is_checking(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every missing value is named at once, so one attempt fixes the configuration."""

    configure(monkeypatch, HARNESS_BROWSER_MODE="pilot")
    with pytest.raises(browser_config.ConfigurationProblem) as refused:
        browser_config.load()
    text = str(refused.value)
    for variable in ("CONSOLE_ORIGIN", "OIDC_ISSUER", "CLIENT_ID"):
        assert variable in text


def test_pilot_mode_defaults_to_signing_in_by_hand(monkeypatch: pytest.MonkeyPatch) -> None:
    configure(monkeypatch, **PILOT)
    config = browser_config.load()
    assert config.login == "manual"
    assert config.headed is True
    assert config.account is None
    assert config.starts_local_stack is False
    # Not waited for unless someone says how long they will wait.
    assert config.expiry_wait_seconds == 0


def test_pilot_mode_refuses_form_login(monkeypatch: pytest.MonkeyPatch) -> None:
    """A pilot account's password has no place in a test run."""

    configure(monkeypatch, **PILOT, HARNESS_BROWSER_LOGIN="form")
    with pytest.raises(browser_config.ConfigurationProblem, match="refused in pilot mode"):
        browser_config.load()


@pytest.mark.parametrize(
    "issuer",
    [browser_config.FIXTURE_ISSUER, browser_config.FIXTURE_ISSUER + "/"],
)
def test_the_local_fixture_cannot_be_run_as_the_pilot(
    monkeypatch: pytest.MonkeyPatch, issuer: str
) -> None:
    configure(monkeypatch, **{**PILOT, "HARNESS_BROWSER_OIDC_ISSUER": issuer})
    with pytest.raises(browser_config.ConfigurationProblem, match="local Keycloak fixture"):
        browser_config.load()


def test_fixture_mode_uses_the_realm_it_ships_with(monkeypatch: pytest.MonkeyPatch) -> None:
    configure(monkeypatch, HARNESS_BROWSER_MODE="fixture")
    config = browser_config.load()
    assert config.origin == "http://127.0.0.1:5173"
    assert config.issuer == browser_config.FIXTURE_ISSUER
    assert config.account is not None and config.account.username == "alice"
    assert "qualification-only" not in repr(config)
    assert config.starts_local_stack is True


def test_local_modes_refuse_a_remote_origin(monkeypatch: pytest.MonkeyPatch) -> None:
    configure(
        monkeypatch,
        HARNESS_BROWSER_MODE="fixture",
        HARNESS_BROWSER_CONSOLE_ORIGIN="https://harness.pilot.example",
    )
    with pytest.raises(browser_config.ConfigurationProblem, match="loopback"):
        browser_config.load()


def test_partial_runs_are_a_pilot_only_notion(monkeypatch: pytest.MonkeyPatch) -> None:
    configure(monkeypatch, HARNESS_BROWSER_MODE="rehearsal", HARNESS_BROWSER_ALLOW_PARTIAL="1")
    with pytest.raises(browser_config.ConfigurationProblem, match="only means something in pilot"):
        browser_config.load()


# -- the verdict -----------------------------------------------------------------------


def _report(mode: str, *outcomes: str, allow_partial: bool = False) -> Report:
    report = Report(mode=mode, origin="https://harness.pilot.example", allow_partial=allow_partial)
    report.commit = "abc1234"
    for index, outcome in enumerate(outcomes):
        report.record(f"check.{index}", f"Check {index}", outcome, "")
    return report


def test_only_a_complete_pilot_run_qualifies() -> None:
    assert _report("pilot", "passed", "observed").qualified is True
    assert _report("pilot", "passed", "skipped").qualified is False
    assert _report("pilot", "passed", "failed").qualified is False
    assert _report("pilot").qualified is False


@pytest.mark.parametrize("mode", ["rehearsal", "fixture"])
def test_a_local_run_never_qualifies_however_well_it_goes(mode: str) -> None:
    report = _report(mode, "passed", "passed")
    assert report.succeeded is True
    assert report.qualified is False
    assert "not" in report.verdict


def test_a_partial_pilot_run_can_succeed_but_never_qualifies() -> None:
    strict = _report("pilot", "passed", "skipped")
    assert strict.succeeded is False
    assert strict.verdict.startswith("NOT QUALIFIED")

    partial = _report("pilot", "passed", "skipped", allow_partial=True)
    assert partial.succeeded is True
    assert partial.qualified is False
    assert partial.verdict.startswith("PARTIAL")


# -- no secret reaches the report --------------------------------------------------------

JWT_SHAPED = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJhbGljZSJ9.c2lnbmF0dXJlLXNpZ25hdHVyZQ"


def test_a_registered_secret_stops_the_report_being_written(tmp_path: Path) -> None:
    report = _report("fixture", "passed")
    report.secret("a-password-seen-during-the-run")
    report.checks[0].detail = "typed a-password-seen-during-the-run into the form"
    with pytest.raises(LeakError):
        report.write(tmp_path / "report.md")
    assert not (tmp_path / "report.md").exists()


@pytest.mark.parametrize(
    "detail",
    [
        f"answered with {JWT_SHAPED}",
        "landed on /auth/callback?code=abc123&state=xyz",
        "fragment #access_token=abc123",
    ],
)
def test_token_and_code_shapes_stop_the_report_even_when_unregistered(
    tmp_path: Path, detail: str
) -> None:
    report = _report("fixture", "passed")
    report.checks[0].detail = detail
    with pytest.raises(LeakError):
        report.write(tmp_path / "report.md")


def test_error_text_is_made_safe_before_it_is_recorded(tmp_path: Path) -> None:
    report = _report("fixture", "failed")
    report.secret("super-secret-value")
    report.checks[0].detail = report_safe(
        report,
        f"Timeout navigating to /auth/callback?code=zq9Kcode with super-secret-value and {JWT_SHAPED}",
    )
    report.write(tmp_path / "report.md")
    written = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "zq9Kcode" not in written and "super-secret-value" not in written
    assert json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))["qualified"] is False


def test_a_tampered_token_differs_only_in_its_signature() -> None:
    tampered = _tamper(JWT_SHAPED)
    assert tampered != JWT_SHAPED
    assert tampered.rsplit(".", 1)[0] == JWT_SHAPED.rsplit(".", 1)[0]
    assert _claims(JWT_SHAPED) == {"sub": "alice"}


# -- the stand-in provider enforces what a real one does -----------------------------------


def _challenge(verifier: str) -> str:
    return (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )


@pytest.fixture
def provider() -> tuple[StubProvider, TestClient, str]:
    stub = StubProvider(client_id="console", console_origin="http://127.0.0.1:5173")
    password = stub.add_user("dev", "Dev")
    return stub, TestClient(stub.app()), password


def _code(client: TestClient, password: str, verifier: str) -> str:
    params = {
        "response_type": "code",
        "client_id": "console",
        "redirect_uri": "http://127.0.0.1:5173/auth/callback",
        "state": "s" * 32,
        "code_challenge": _challenge(verifier),
        "code_challenge_method": "S256",
    }
    page = client.get("/authorize", params=params, follow_redirects=False)
    if page.status_code == 302:
        # The provider's session from an earlier login: straight back with a code.
        return parse_qs(urlsplit(page.headers["location"]).query)["code"][0]
    assert page.status_code == 200, page.text
    login = client.post(
        "/login",
        params=params,
        data={"username": "dev", "password": password},
        follow_redirects=False,
    )
    assert login.status_code == 302, login.text
    return parse_qs(urlsplit(login.headers["location"]).query)["code"][0]


def _exchange(client: TestClient, code: str, verifier: str) -> object:
    return client.post(
        "/token",
        headers={"Origin": "http://127.0.0.1:5173"},
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": "http://127.0.0.1:5173/auth/callback",
            "client_id": "console",
            "code_verifier": verifier,
        },
    )


def test_the_stand_in_redeems_a_code_once_and_only_with_its_verifier(
    provider: tuple[StubProvider, TestClient, str],
) -> None:
    _, client, password = provider
    verifier = "v" * 64

    wrong = _exchange(client, _code(client, password, verifier), "w" * 64)
    assert wrong.status_code == 400  # type: ignore[attr-defined]

    code = _code(client, password, verifier)
    accepted = _exchange(client, code, verifier)
    assert accepted.status_code == 200  # type: ignore[attr-defined]
    assert accepted.headers["access-control-allow-origin"] == "http://127.0.0.1:5173"  # type: ignore[attr-defined]
    claims = _claims(accepted.json()["access_token"])  # type: ignore[attr-defined]
    assert claims["aud"] == "oracledbharness" and claims["azp"] == "console"

    assert _exchange(client, code, verifier).status_code == 400  # type: ignore[attr-defined]


def test_the_stand_in_refuses_an_unregistered_redirect_and_a_missing_challenge(
    provider: tuple[StubProvider, TestClient, str],
) -> None:
    _, client, _ = provider
    base = {"response_type": "code", "client_id": "console", "state": "s" * 32}
    elsewhere = client.get(
        "/authorize",
        params={**base, "redirect_uri": "https://attacker.example/cb"},
        follow_redirects=False,
    )
    assert elsewhere.status_code == 400
    no_pkce = client.get(
        "/authorize",
        params={**base, "redirect_uri": "http://127.0.0.1:5173/auth/callback"},
        follow_redirects=False,
    )
    assert no_pkce.status_code == 302
    assert "error=invalid_request" in no_pkce.headers["location"]
