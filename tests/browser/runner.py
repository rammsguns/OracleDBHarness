"""The browser run: every check, in the order a person meets them.

1. Preflight over HTTP: the origin reaches the API, the deployment is in OIDC mode and
   serves exactly the issuer and client this run was told to expect, the proxy serves the
   callback route, and development tokens are refused.
2. A refused identity: an account the provider authenticates but the harness has not
   registered is shown why, and is not signed in.
3. Sign-in through the console: the authorization request the console builds, the login,
   the callback, and what the address bar and storage hold afterwards.
4. API access with the token the console holds, and direct API access without it or
   with the wrong one.
5. Sign-out, and what does and does not stop working.
6. Expiry, in the console and at the API.

Tokens are read from the console's own requests, held in memory for the checks that need
them, and registered with the report so none can be written out.
"""

from __future__ import annotations

import base64
import json
import re
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx

from tests.browser.config import Account, BrowserConfig
from tests.browser.report import Report
from tests.browser.stack import LocalStack, StackProblem
from tests.browser.stub_provider import StubProvider

PENDING_KEY = "oracledbharness.oidc.pending"
SIGN_IN = "Sign in with your identity provider"
SIGN_OUT = "Sign out"
_SUBJECT = re.compile(r"subject (\S+?) and grant")


class _Abort(Exception):
    """A check failed that later checks depend on. Already recorded."""


@dataclass
class Traffic:
    """What the console sent, observed from the browser. Never written out."""

    authorization_urls: list[str] = field(default_factory=list)
    tokens: list[str] = field(default_factory=list)
    authorized_api_calls: int = 0


@dataclass
class SignIn:
    status: str  # "signed_in", "refused" or "timeout"
    message: str = ""
    login_form_shown: bool | None = None


def _claims(token: str) -> dict[str, Any]:
    """The token's payload, unverified: only its expiry is read, never trusted for access."""

    payload = token.split(".")[1]
    padded = payload + "=" * (-len(payload) % 4)
    decoded: dict[str, Any] = json.loads(base64.urlsafe_b64decode(padded))
    return decoded


def _tamper(token: str) -> str:
    """The same token with its signature changed."""

    head, _, signature = token.rpartition(".")
    replacement = "A" if signature[-2:-1] != "A" else "B"
    return f"{head}.{signature[:-2]}{replacement}{signature[-1:]}"


def _prompt(text: str) -> None:
    print(f"\n>>> {text}", file=sys.stderr, flush=True)


class Run:
    def __init__(self, config: BrowserConfig, report: Report, *, issuer: str) -> None:
        self.config = config
        self.report = report
        self.issuer = issuer
        self.origin = config.origin
        self.traffic = Traffic()
        self.authorization_endpoint = ""
        self.http = httpx.Client(base_url=self.origin, timeout=20.0, follow_redirects=False)

    # -- preflight ---------------------------------------------------------------------

    def preflight(self) -> bool:
        report, http = self.report, self.http
        ok = True

        def check(check_id: str, title: str, condition: bool, detail: str) -> None:
            nonlocal ok
            ok = report.expect(check_id, title, condition, detail) and ok

        try:
            health = http.get("/healthz")
        except httpx.HTTPError as exc:
            report.failed(
                "prerequisite.origin",
                "The console origin is reachable",
                f"{self.origin} could not be reached ({type(exc).__name__}: {exc}). Check "
                "HARNESS_BROWSER_CONSOLE_ORIGIN, and that the deployment is up and reachable "
                "from this machine.",
            )
            return False
        check(
            "preflight.origin",
            "The console origin reaches the API",
            health.status_code == 200,
            f"GET /healthz answered {health.status_code}",
        )

        info = http.get("/api/v1/system/info")
        body = info.json() if info.status_code == 200 else {}
        warnings = [w for w in body.get("warnings", []) if "OIDC" in w]
        check(
            "preflight.mode",
            "The deployment signs people in with OIDC, with no OIDC warning",
            body.get("authMode") == "oidc" and not warnings,
            f"authMode `{body.get('authMode')}`, OIDC warnings: {warnings or 'none'}",
        )

        served = http.get("/api/v1/auth/oidc")
        sign_in = served.json() if served.status_code == 200 else {}
        self.authorization_endpoint = str(sign_in.get("authorizationEndpoint", ""))
        check(
            "preflight.registration",
            "The deployment serves the expected issuer and client",
            served.status_code == 200
            and sign_in.get("issuer") == self.issuer
            and sign_in.get("clientId") == self.config.client_id,
            f"HTTP {served.status_code}; issuer `{sign_in.get('issuer')}` (expected "
            f"`{self.issuer}`), client `{sign_in.get('clientId')}` (expected "
            f"`{self.config.client_id}`)",
        )

        try:
            discovery = httpx.get(
                f"{self.issuer.rstrip('/')}/.well-known/openid-configuration", timeout=15.0
            ).json()
            discovered = discovery.get("issuer")
        except (httpx.HTTPError, ValueError) as exc:
            discovered = f"unreachable ({type(exc).__name__})"
        check(
            "preflight.discovery",
            "The provider's discovery document names the same issuer",
            discovered == self.issuer,
            f"discovery issuer `{discovered}`",
        )

        callback = http.get("/auth/callback")
        check(
            "preflight.callback-route",
            "The origin serves the console at the callback path",
            callback.status_code == 200 and 'id="root"' in callback.text,
            f"GET /auth/callback answered {callback.status_code}",
        )

        dev = http.post(
            "/api/v1/auth/dev-token", json={"subject": "browser-run", "roles": ["administrator"]}
        )
        check(
            "preflight.dev-token",
            "Development tokens are refused",
            dev.status_code != 200,
            f"POST /api/v1/auth/dev-token answered {dev.status_code}",
        )
        return ok and bool(self.authorization_endpoint)

    # -- the browser -------------------------------------------------------------------

    def watch(self, context: Any) -> None:
        def on_request(request: Any) -> None:
            url = request.url
            if self.authorization_endpoint and url.startswith(self.authorization_endpoint):
                query = parse_qs(urlsplit(url).query)
                for name in ("state", "code_challenge"):
                    for value in query.get(name, []):
                        self.report.secret(value)
                self.traffic.authorization_urls.append(url)
            if "/auth/callback" in url:
                for value in parse_qs(urlsplit(url).query).get("code", []):
                    self.report.secret(value)
            if url.startswith(f"{self.origin}/api/"):
                header = request.headers.get("authorization", "")
                if header.lower().startswith("bearer "):
                    token = header[7:]
                    self.report.secret(token)
                    if token not in self.traffic.tokens:
                        self.traffic.tokens.append(token)
                    self.traffic.authorized_api_calls += 1

        context.on("request", on_request)

    def sign_in(self, page: Any, account: Account | None, who: str) -> SignIn:
        from playwright.sync_api import Error as PlaywrightError

        page.goto(self.origin)
        requests_before = len(self.traffic.authorization_urls)
        page.get_by_role("button", name=SIGN_IN).click(timeout=30_000)
        manual = self.config.login == "manual"
        if manual:
            _prompt(
                f"In the browser window, sign in as {who}. Waiting up to "
                f"{self.config.login_timeout_seconds:.0f}s."
            )
        deadline = time.monotonic() + (self.config.login_timeout_seconds if manual else 60.0)
        typed = False
        while time.monotonic() < deadline:
            try:
                url = page.url
                # The callback path counts as the console: a console that forgot to clear the
                # address bar must still reach an outcome here, so the check made for that
                # afterwards can name it.
                on_console = url.startswith(self.origin)
                if not url.startswith(self.origin):
                    if not manual and not typed and page.locator("#username").is_visible():
                        assert account is not None
                        page.fill("#username", account.username)
                        page.fill("#password", account.password)
                        page.click("#kc-login")
                        typed = True
                # Back on the console after the provider was really asked. A provider that
                # still has a session redirects straight back, too fast to see it leave.
                went_to_provider = len(self.traffic.authorization_urls) > requests_before
                if on_console and went_to_provider:
                    if page.get_by_role("button", name=SIGN_OUT).is_visible():
                        return SignIn("signed_in", login_form_shown=None if manual else typed)
                    error = page.locator(".notice.error")
                    if error.count() and error.first.is_visible():
                        return SignIn(
                            "refused",
                            error.first.inner_text(),
                            login_form_shown=None if manual else typed,
                        )
            except PlaywrightError:
                pass  # a navigation replaced the page mid-check; look again
            page.wait_for_timeout(200)
        return SignIn(
            "timeout", f"no outcome within the login timeout (at {urlsplit(page.url).path})"
        )

    def storage_holds(self, page: Any, values: list[str]) -> bool:
        dump = page.evaluate(
            "() => JSON.stringify([Object.entries(localStorage), Object.entries(sessionStorage)])"
        )
        return any(value in dump for value in values)

    # -- the scenario --------------------------------------------------------------------

    def scenario(
        self,
        browser: Any,
        account: Account | None,
        register: Callable[[str, str], None] | None,
        forbidden_target_id: str,
    ) -> None:
        report, config = self.report, self.config
        context = browser.new_context()
        self.watch(context)
        page = context.new_page()

        # 2. A refused identity.
        if register is not None:
            # Local runs register the account only after the harness has refused it, so
            # the one account shows both halves.
            refused = self.sign_in(page, account, "the test account")
            match = _SUBJECT.search(refused.message)
            if not report.expect(
                "identity.unregistered",
                "An authenticated but unregistered account is refused, and told its subject",
                refused.status == "refused" and "not registered" in refused.message and bool(match),
                f"outcome `{refused.status}`",
            ):
                raise _Abort
            assert match is not None
            report.expect(
                "identity.unregistered-no-token-kept",
                "The refused account's token is not kept in storage",
                not self.storage_holds(page, self.traffic.tokens),
                "localStorage and sessionStorage checked",
            )
            register(match.group(1), "")
        elif config.check_unregistered:
            other = browser.new_context()
            self.watch(other)
            refused = self.sign_in(
                other.new_page(),
                config.unregistered,
                "an account the provider knows but the harness has NOT registered",
            )
            report.expect(
                "identity.unregistered",
                "An authenticated but unregistered account is refused, and told its subject",
                refused.status == "refused" and "not registered" in refused.message,
                f"outcome `{refused.status}`",
            )
            other.close()
        else:
            report.skipped(
                "identity.unregistered",
                "An authenticated but unregistered account is refused",
                "No unregistered account was named (HARNESS_BROWSER_CHECK_UNREGISTERED or "
                "HARNESS_BROWSER_UNREGISTERED_USER).",
            )

        # 3. Sign-in through the console.
        self.traffic.authorization_urls.clear()
        outcome = self.sign_in(page, account, "the registered test account")
        if not report.expect(
            "sign-in.completes",
            "Sign-in through the console completes and shows the signed-in console",
            outcome.status == "signed_in",
            f"outcome `{outcome.status}`"
            + (f": {report_safe(report, outcome.message)}" if outcome.message else ""),
        ):
            raise _Abort
        self.check_authorization_request()
        location = page.evaluate("() => [location.pathname, location.search, location.hash]")
        report.expect(
            "sign-in.callback-cleared",
            "The callback's code is gone from the address bar",
            location == ["/", "", ""],
            f"path `{location[0]}`, query {'empty' if not location[1] else 'present'}",
        )
        token = self.traffic.tokens[-1] if self.traffic.tokens else ""
        pending = page.evaluate(f"() => sessionStorage.getItem('{PENDING_KEY}')") is not None
        stored = self.storage_holds(page, self.traffic.tokens)
        report.expect(
            "sign-in.storage",
            "No token and no pending sign-in are left in browser storage",
            bool(token) and not pending and not stored,
            "; ".join(
                part
                for part in (
                    "a token is in browser storage" if stored else "",
                    "the pending sign-in is still in sessionStorage" if pending else "",
                )
                if part
            )
            or "the token is held in memory only",
        )
        if not token:
            report.failed(
                "api.access", "The console calls the API with a bearer token", "none seen"
            )
            raise _Abort

        # 4. API access, with and without the right credentials.
        me = self.http.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        roles = me.json().get("roles", []) if me.status_code == 200 else []
        shown = page.locator(".who").inner_text() if me.status_code == 200 else ""
        report.expect(
            "api.access",
            "The console's token is accepted by the API, for the account the console shows",
            me.status_code == 200
            and bool(me.json().get("displayName") or me.json().get("subject"))
            and (me.json().get("displayName") or me.json().get("subject")) in shown,
            f"GET /api/v1/auth/me answered {me.status_code}; roles {roles}",
        )
        targets = self.http.get("/api/v1/targets", headers={"Authorization": f"Bearer {token}"})
        report.expect(
            "api.targets",
            "An authenticated API call beyond the account itself succeeds",
            targets.status_code == 200,
            f"GET /api/v1/targets answered {targets.status_code}",
        )
        self.unauthorized(token, roles, forbidden_target_id)

        # 5. Sign-out.
        page.get_by_role("button", name=SIGN_OUT).click()
        page.get_by_role("button", name=SIGN_IN).wait_for(timeout=15_000)
        notice = (
            page.locator(".notice").first.inner_text() if page.locator(".notice").count() else ""
        )
        report.expect(
            "sign-out.console",
            "Sign-out returns to the sign-in screen and says the provider session remains",
            "Signed out" in notice,
            "notice shown" if notice else "no notice",
        )
        calls_before = self.traffic.authorized_api_calls
        page.reload()
        page.get_by_role("button", name=SIGN_IN).wait_for(timeout=15_000)
        page.wait_for_timeout(2_000)
        still_stored = self.storage_holds(page, self.traffic.tokens)
        report.expect(
            "sign-out.no-token-used",
            "After sign-out and a reload the console makes no authenticated API call",
            self.traffic.authorized_api_calls == calls_before and not still_stored,
            f"{self.traffic.authorized_api_calls - calls_before} authenticated call(s) after "
            f"sign-out; token {'still' if still_stored else 'not'} in browser storage",
        )
        after = self.http.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        remaining = int(_claims(token).get("exp", 0) - time.time())
        report.observed(
            "sign-out.token-at-api",
            "The signed-out token at the API",
            (
                f"Still accepted ({after.status_code}) for about {remaining}s more: console "
                "sign-out forgets the token but does not revoke it at the provider."
            )
            if after.status_code == 200
            else f"Refused ({after.status_code}).",
        )

        again = self.sign_in(page, account, "the registered test account again")
        report.expect(
            "sign-in.again",
            "Signing in again after sign-out completes",
            again.status == "signed_in",
            f"outcome `{again.status}`",
        )
        if again.login_form_shown is not None:
            report.observed(
                "sign-in.provider-session",
                "Provider session after console sign-out",
                "The provider asked for the password again."
                if again.login_form_shown
                else "The provider's session was still active, so no password was asked for.",
            )

        # 6. Expiry.
        self.expiry(page)
        context.close()

    def check_authorization_request(self) -> None:
        report = self.report
        if not self.traffic.authorization_urls:
            report.failed(
                "sign-in.authorization-request",
                "The console's authorization request is PKCE with the registered redirect URI",
                "No request to the authorization endpoint was seen.",
            )
            return
        query = parse_qs(urlsplit(self.traffic.authorization_urls[0]).query)

        def one(name: str) -> str:
            return query.get(name, [""])[0]

        problems = []
        if one("response_type") != "code":
            problems.append("response_type is not `code`")
        if one("client_id") != self.config.client_id:
            problems.append("client_id differs")
        if one("redirect_uri") != f"{self.origin}/auth/callback":
            problems.append(f"redirect_uri is `{one('redirect_uri')}`")
        if one("code_challenge_method") != "S256" or len(one("code_challenge")) < 43:
            problems.append("no S256 code challenge")
        if len(one("state")) < 32:
            problems.append("state is missing or short")
        report.expect(
            "sign-in.authorization-request",
            "The console's authorization request is PKCE with the registered redirect URI",
            not problems,
            "; ".join(problems) or f"redirect_uri `{self.origin}/auth/callback`, S256, state",
        )

    def unauthorized(self, token: str, roles: list[str], forbidden_target_id: str) -> None:
        report, http = self.report, self.http
        tampered = _tamper(token)
        report.secret(tampered)
        cases = [
            ("api.no-token", "An API call with no token is refused", {}, "/api/v1/targets", 401),
            (
                "api.tampered-token",
                "A token with an altered signature is refused",
                {"Authorization": f"Bearer {tampered}"},
                "/api/v1/targets",
                401,
            ),
            (
                "api.wrong-scheme",
                "A non-bearer Authorization header is refused",
                {"Authorization": "Basic YnJvd3Nlcjpub3RoaW5n"},
                "/api/v1/targets",
                401,
            ),
        ]
        for check_id, title, headers, path, expected in cases:
            response = http.get(path, headers=headers)
            report.expect(
                check_id,
                title,
                response.status_code == expected,
                f"{path} answered {response.status_code}",
            )

        bearer = {"Authorization": f"Bearer {token}"}
        if "administrator" in roles:
            report.skipped(
                "api.admin-refused",
                "An administrator-only endpoint refuses a non-administrator",
                "The test account is an administrator. Use an account without that role.",
            )
        else:
            audit = http.get("/api/v1/audit", headers=bearer)
            report.expect(
                "api.admin-refused",
                "An administrator-only endpoint refuses a non-administrator",
                audit.status_code == 403,
                f"/api/v1/audit answered {audit.status_code}",
            )
        if forbidden_target_id:
            target = http.get(f"/api/v1/targets/{forbidden_target_id}", headers=bearer)
            report.expect(
                "api.target-refused",
                "A target the account has no grant on is refused",
                target.status_code == 403,
                f"GET /api/v1/targets/<target> answered {target.status_code}",
            )
        else:
            report.skipped(
                "api.target-refused",
                "A target the account has no grant on is refused",
                "HARNESS_BROWSER_FORBIDDEN_TARGET_ID is not set.",
            )

    def expiry(self, page: Any) -> None:
        report, config = self.report, self.config
        if not self.traffic.tokens:
            report.skipped(
                "expiry.console", "The console signs out when the token expires", "no token"
            )
            return
        token = self.traffic.tokens[-1]
        expires = float(_claims(token).get("exp", 0))
        remaining = expires - time.time()
        if remaining > config.expiry_wait_seconds:
            reason = (
                f"The token expires in {remaining:.0f}s, longer than "
                f"HARNESS_BROWSER_EXPIRY_WAIT_SECONDS={config.expiry_wait_seconds:.0f}. Raise it, "
                "or shorten the console client's access token lifespan for the run."
            )
            report.skipped("expiry.console", "The console signs out when the token expires", reason)
            report.skipped("expiry.api", "The API refuses the expired token", reason)
            return
        if config.login == "manual":
            _prompt(
                f"Leave the browser window alone; waiting {remaining:.0f}s for the token to expire."
            )
        try:
            page.get_by_text("Your session expired").wait_for(timeout=(remaining + 30) * 1000)
            console_ok = page.get_by_role("button", name=SIGN_IN).is_visible()
        except Exception:  # noqa: BLE001 - a timeout is the failure being checked
            console_ok = False
        report.expect(
            "expiry.console",
            "The console signs out when the token expires",
            console_ok,
            f"token lifetime left at the start of the wait: {remaining:.0f}s",
        )
        time.sleep(max(0.0, expires - time.time() + 2))
        refused = self.http.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        report.expect(
            "expiry.api",
            "The API refuses the expired token",
            refused.status_code == 401,
            f"GET /api/v1/auth/me answered {refused.status_code} after expiry",
        )


def report_safe(report: Report, text: str) -> str:
    """Error text for the report, with anything secret or code-shaped removed."""

    cleaned = text
    for value in report._secrets:  # noqa: SLF001 - the report's own registry
        cleaned = cleaned.replace(value, "[redacted]")
    cleaned = re.sub(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", "[redacted]", cleaned)
    return re.sub(r"([?&#](?:code|access_token|id_token)=)[^&\s]+", r"\1[redacted]", cleaned)[:500]


def run(config: BrowserConfig, workdir: Path | None = None) -> Report:
    report = Report(
        mode=config.mode,
        origin=config.origin,
        issuer=config.issuer,
        client_id=config.client_id,
        allow_partial=config.allow_partial,
    )
    workdir = workdir or Path(tempfile.mkdtemp(prefix="harness-browser-"))
    provider: StubProvider | None = None
    stack: LocalStack | None = None
    account = config.account
    register: Callable[[str, str], None] | None = None
    forbidden = config.forbidden_target_id
    try:
        issuer, jwks = config.issuer, ""
        if config.mode == "rehearsal":
            provider = StubProvider(client_id=config.client_id, console_origin=config.origin)
            username = "rehearsal-developer"
            account = Account(username, provider.add_user(username, "Rehearsal Developer"))
            provider.start()
            issuer, jwks = provider.issuer, provider.jwks_url
        elif config.mode == "fixture":
            try:
                jwks = httpx.get(f"{issuer}/.well-known/openid-configuration", timeout=15.0).json()[
                    "jwks_uri"
                ]
            except (httpx.HTTPError, ValueError, KeyError) as exc:
                report.failed(
                    "prerequisite.provider",
                    "The fixture identity provider is running",
                    f"{issuer} did not serve discovery ({type(exc).__name__}). Start it with "
                    "`docker compose -f tests/identity/keycloak/compose.yaml up -d --wait`.",
                )
                return report
        report.issuer = issuer
        if account is not None:
            report.secret(account.password)

        if config.starts_local_stack:
            stack = LocalStack(
                workdir=workdir,
                console_origin=config.origin,
                issuer=issuer,
                jwks_url=jwks,
                client_id=config.client_id,
            )
            try:
                stack.start()
            except StackProblem as problem:
                report.failed(
                    "prerequisite.stack",
                    "The local API and console start",
                    report_safe(report, str(problem)),
                )
                return report
            register = stack.register
            forbidden = forbidden or stack.target_id("production")

        session = Run(config, report, issuer=issuer)
        if not session.preflight():
            return report

        try:
            from playwright.sync_api import Error as PlaywrightError
            from playwright.sync_api import sync_playwright
        except ImportError:
            report.failed(
                "prerequisite.playwright",
                "Playwright is installed",
                "Run `uv sync --group browser`.",
            )
            return report

        with sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch(
                    channel=None if config.channel == "chromium" else config.channel,
                    headless=not config.headed,
                )
            except PlaywrightError as exc:
                report.failed(
                    "prerequisite.browser",
                    "A browser starts",
                    f"{report_safe(report, str(exc).splitlines()[0])}. Run `uv run --group browser "
                    "playwright install chromium`, or set HARNESS_BROWSER_CHANNEL=chrome or msedge.",
                )
                return report
            report.browser = f"{config.channel} {browser.version}"
            try:
                session.scenario(browser, account, register, forbidden)
            except _Abort:
                pass
            except Exception as exc:  # noqa: BLE001 - recorded, so the report still says what ran
                report.failed(
                    "run.completed",
                    "The run completed",
                    report_safe(report, f"{type(exc).__name__}: {exc}"),
                )
            finally:
                browser.close()
    finally:
        if stack is not None:
            stack.stop()
        if provider is not None:
            provider.stop()
    return report
