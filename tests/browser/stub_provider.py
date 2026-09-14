"""A minimal OpenID Connect provider for rehearsing the browser run on a bare machine.

It implements only what the console uses: discovery, a key set, an authorization
endpoint with a login form, and a token endpoint for the authorization code flow with
PKCE (S256 only). It enforces what a real provider enforces on that path - a registered
client and redirect URI, a matching verifier, single-use codes, CORS for the console's
origin only - so the rehearsal exercises the console's handling of them. It is not a
provider anyone should qualify against, and a report from it says so.

The login form uses Keycloak's element ids (``username``, ``password``, ``kc-login``) so
the runner's form login is the same code against both. The password is generated for
each run and never written anywhere.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import socket
import threading
import time
import uuid
from dataclasses import dataclass, field
from html import escape
from typing import Any
from urllib.parse import parse_qs, urlencode

import uvicorn
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from jose import jwk, jwt

AUDIENCE = "oracledbharness"
SESSION_COOKIE = "stub_idp_session"


@dataclass
class _User:
    username: str
    password: str = field(repr=False)
    subject: str
    name: str


@dataclass
class _Pending:
    client_id: str
    redirect_uri: str
    challenge: str
    subject: str
    issued: float


class StubProvider:
    """One provider, one client, and the accounts a run needs."""

    def __init__(
        self,
        *,
        client_id: str,
        console_origin: str,
        token_lifespan_seconds: int = 45,
    ) -> None:
        self.client_id = client_id
        self.console_origin = console_origin.rstrip("/")
        self.redirect_uri = f"{self.console_origin}/auth/callback"
        self.token_lifespan = token_lifespan_seconds
        self._key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self._kid = secrets.token_hex(8)
        self._users: dict[str, _User] = {}
        self._codes: dict[str, _Pending] = {}
        self._sessions: dict[str, str] = {}
        self._lock = threading.Lock()
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None
        self.port = 0

    @property
    def issuer(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def jwks_url(self) -> str:
        return f"{self.issuer}/jwks"

    def add_user(self, username: str, name: str) -> str:
        """Create an account with a fresh password, and return the password."""

        password = secrets.token_urlsafe(18)
        self._users[username] = _User(username, password, str(uuid.uuid4()), name)
        return password

    # -- serving ---------------------------------------------------------------------

    def start(self) -> None:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        self.port = listener.getsockname()[1]
        config = uvicorn.Config(self.app(), log_level="warning", lifespan="off")
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(
            target=self._server.run, kwargs={"sockets": [listener]}, daemon=True
        )
        self._thread.start()
        deadline = time.monotonic() + 20
        while not self._server.started:
            if time.monotonic() > deadline:
                raise RuntimeError("The stand-in identity provider did not start.")
            time.sleep(0.05)

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=10)

    # -- the protocol ----------------------------------------------------------------

    def _sign(self, user: _User) -> str:
        now = int(time.time())
        pem = self._key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        claims = {
            "iss": self.issuer,
            "sub": user.subject,
            "aud": AUDIENCE,
            "azp": self.client_id,
            "iat": now,
            "exp": now + self.token_lifespan,
            "name": user.name,
        }
        return jwt.encode(
            claims, pem.decode("ascii"), algorithm="RS256", headers={"kid": self._kid}
        )

    def _public_jwk(self) -> dict[str, Any]:
        pem = (
            self._key.public_key()
            .public_bytes(
                serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
            )
            .decode("ascii")
        )
        key: dict[str, Any] = jwk.construct(pem, "RS256").to_dict()
        key.update({"kid": self._kid, "use": "sig", "alg": "RS256"})
        return key

    def _cors(self, request: Request, response: Response) -> Response:
        if request.headers.get("origin") == self.console_origin:
            response.headers["Access-Control-Allow-Origin"] = self.console_origin
        return response

    def _redirect_with_code(self, redirect_uri: str, state: str, pending: _Pending) -> Response:
        code = secrets.token_urlsafe(24)
        with self._lock:
            self._codes[code] = pending
        return RedirectResponse(f"{redirect_uri}?{urlencode({'code': code, 'state': state})}", 302)

    def app(self) -> FastAPI:  # noqa: C901 - one small provider, kept in one place
        app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

        @app.get("/.well-known/openid-configuration")
        def discovery() -> dict[str, Any]:
            return {
                "issuer": self.issuer,
                "authorization_endpoint": f"{self.issuer}/authorize",
                "token_endpoint": f"{self.issuer}/token",
                "jwks_uri": self.jwks_url,
                "response_types_supported": ["code"],
                "code_challenge_methods_supported": ["S256"],
                "id_token_signing_alg_values_supported": ["RS256"],
            }

        @app.get("/jwks")
        def keys() -> dict[str, Any]:
            return {"keys": [self._public_jwk()]}

        @app.get("/authorize")
        def authorize(request: Request) -> Response:
            query = request.query_params
            if query.get("client_id") != self.client_id:
                return HTMLResponse("Unknown client.", 400)
            if query.get("redirect_uri") != self.redirect_uri:
                # A real provider shows an error page rather than redirecting anywhere.
                return HTMLResponse("Invalid redirect_uri.", 400)
            redirect = self.redirect_uri
            state = query.get("state", "")
            if (
                query.get("response_type") != "code"
                or query.get("code_challenge_method") != "S256"
                or len(query.get("code_challenge", "")) < 43
            ):
                return RedirectResponse(
                    f"{redirect}?{urlencode({'error': 'invalid_request', 'state': state})}", 302
                )
            session = request.cookies.get(SESSION_COOKIE)
            subject = self._sessions.get(session or "")
            if subject:
                pending = _Pending(
                    self.client_id, redirect, query["code_challenge"], subject, time.time()
                )
                return self._redirect_with_code(redirect, state, pending)
            action = f"/login?{urlencode(dict(query))}"
            return HTMLResponse(
                "<!doctype html><title>Stand-in sign-in</title>"
                "<h1>Stand-in identity provider</h1>"
                f'<form method="post" action="{escape(action)}">'
                '<input id="username" name="username" autocomplete="off">'
                '<input id="password" name="password" type="password">'
                '<button id="kc-login" type="submit">Sign in</button></form>'
            )

        @app.post("/login")
        async def login(request: Request) -> Response:
            query = request.query_params
            form = await _form(request)
            username, password = form.get("username", ""), form.get("password", "")
            user = self._users.get(username)
            if user is None or not secrets.compare_digest(user.password, password):
                return HTMLResponse("Invalid username or password.", 401)
            session = secrets.token_urlsafe(24)
            self._sessions[session] = user.subject
            pending = _Pending(
                self.client_id,
                self.redirect_uri,
                query.get("code_challenge", ""),
                user.subject,
                time.time(),
            )
            response = self._redirect_with_code(self.redirect_uri, query.get("state", ""), pending)
            response.set_cookie(SESSION_COOKIE, session, httponly=True, samesite="lax")
            return response

        @app.post("/token")
        async def token(request: Request) -> Response:
            form = await _form(request)
            grant_type, code = form.get("grant_type", ""), form.get("code", "")
            redirect_uri, client_id = form.get("redirect_uri", ""), form.get("client_id", "")
            code_verifier = form.get("code_verifier", "")
            with self._lock:
                pending = self._codes.pop(code, None)  # single use, whatever happens next
            error: str | None = None
            if grant_type != "authorization_code":
                error = "unsupported_grant_type"
            elif pending is None or time.time() - pending.issued > 60:
                error = "invalid_grant"
            elif client_id != pending.client_id or redirect_uri != pending.redirect_uri:
                error = "invalid_grant"
            else:
                digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
                challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
                if not secrets.compare_digest(challenge, pending.challenge):
                    error = "invalid_grant"
            if error is not None or pending is None:
                return self._cors(
                    request,
                    JSONResponse({"error": error, "error_description": "Code not accepted."}, 400),
                )
            user = next(u for u in self._users.values() if u.subject == pending.subject)
            return self._cors(
                request,
                JSONResponse(
                    {
                        "access_token": self._sign(user),
                        "token_type": "Bearer",
                        "expires_in": self.token_lifespan,
                    }
                ),
            )

        return app


async def _form(request: Request) -> dict[str, str]:
    """A form body, without the multipart dependency FastAPI's ``Form`` needs."""

    parsed = parse_qs((await request.body()).decode("utf-8"), keep_blank_values=True)
    return {key: values[0] for key, values in parsed.items()}
