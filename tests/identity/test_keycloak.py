"""Console sign-in, qualified against a real identity provider.

tests/unit/test_oidc.py signs tokens itself and stubs the provider's endpoints. This
suite talks to a running Keycloak configured the way docs/setup.md tells an operator
to register the console (tests/identity/keycloak/realm.json), performs the console's
authorization code flow with PKCE against it, and hands the token Keycloak issued to
the API. Nothing here is stubbed.

It skips unless HARNESS_TEST_OIDC_ISSUER names the realm; README.md has the run.
"""

from __future__ import annotations

import base64
import hashlib
import html
import os
import re
import secrets
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient
from jose import jwt
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from harness_api.app import create_app
from harness_api.config import Settings
from harness_api.db import initialize_schema
from harness_api.models import User
from harness_api.security import Authenticator
from harness_worker.errors import AuthenticationError

ISSUER = os.environ.get("HARNESS_TEST_OIDC_ISSUER", "")

pytestmark = pytest.mark.skipif(
    not ISSUER,
    reason="Set HARNESS_TEST_OIDC_ISSUER to the qualification realm; see tests/identity/README.md.",
)

# Fixtures of the disposable realm in keycloak/realm.json, not credentials of anything.
USERNAME = "alice"
PASSWORD = "qualification-only"  # noqa: S105
ADMIN_USERNAME = os.environ.get("HARNESS_TEST_KEYCLOAK_ADMIN", "admin")
ADMIN_PASSWORD = os.environ.get("HARNESS_TEST_KEYCLOAK_ADMIN_PASSWORD", "admin")

CONSOLE_CLIENT = "oracledbharness-console"
OTHER_CLIENT = "another-application"
CONSOLE_ORIGIN = "http://127.0.0.1:5173"
REDIRECT_URI = f"{CONSOLE_ORIGIN}/auth/callback"
AUDIENCE = "oracledbharness"


def _keycloak() -> str:
    return ISSUER.split("/realms/")[0]


def _realm() -> str:
    return ISSUER.rstrip("/").rsplit("/", 1)[-1]


@pytest.fixture(scope="module")
def discovery() -> dict[str, Any]:
    response = httpx.get(f"{ISSUER}/.well-known/openid-configuration", timeout=10)
    response.raise_for_status()
    document: dict[str, Any] = response.json()
    return document


def _settings(tmp_path: Path, discovery: dict[str, Any], **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "env": "development",
        "metadata_url": f"sqlite+pysqlite:///{(tmp_path / 'metadata.sqlite3').as_posix()}",
        "secret_dir": str(tmp_path),
        "auth_mode": "oidc",
        "oidc_issuer": ISSUER,
        "oidc_jwks_url": discovery["jwks_uri"],
        "oidc_audience": AUDIENCE,
        "oidc_client_id": CONSOLE_CLIENT,
        "oracle_backend": "fake",
        "oracle_fake_data_dir": str(tmp_path / "fake"),
    }
    values.update(overrides)
    return Settings(**values)


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Session]:
    engine = create_engine(f"sqlite+pysqlite:///{(tmp_path / 'users.sqlite3').as_posix()}")
    initialize_schema(engine)
    with Session(engine) as session:
        yield session


def _challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _authorize(
    authorization_endpoint: str,
    token_endpoint: str,
    client_id: str = CONSOLE_CLIENT,
    scopes: str = "openid profile email",
) -> httpx.Response:
    """Do what a person and the console do together; return the token response.

    The parameters are the ones apps/web/src/oidc.ts sends. The login form is
    submitted the way a browser would submit it, cookies and all.
    """

    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(48)
    with httpx.Client(timeout=10, follow_redirects=False) as browser:
        login_page = browser.get(
            authorization_endpoint,
            params={
                "response_type": "code",
                "client_id": client_id,
                "redirect_uri": REDIRECT_URI,
                "scope": scopes,
                "state": state,
                "code_challenge": _challenge(verifier),
                "code_challenge_method": "S256",
            },
        )
        assert login_page.status_code == 200, login_page.text[:500]
        form = re.search(r'action="([^"]*login-actions/authenticate[^"]*)"', login_page.text)
        assert form, "Keycloak did not serve its login form."
        # Keycloak marks its session cookies Secure. Browsers still send those to a
        # loopback origin over plain http, which httpx will not; forward them as a
        # browser would.
        session_cookies = "; ".join(f"{name}={value}" for name, value in login_page.cookies.items())
        submitted = browser.post(
            html.unescape(form.group(1)),
            headers={"Cookie": session_cookies},
            data={"username": USERNAME, "password": PASSWORD, "credentialId": ""},
        )
        assert submitted.status_code == 302, submitted.text[:500]
        callback = submitted.headers["location"]
        assert callback.startswith(REDIRECT_URI + "?"), callback
        answer = parse_qs(urlsplit(callback).query)
        assert answer["state"] == [state]

        # The browser redeems the code itself, cross-origin: the provider must allow
        # the console's origin at its token endpoint.
        return browser.post(
            token_endpoint,
            headers={"Origin": CONSOLE_ORIGIN, "Accept": "application/json"},
            data={
                "grant_type": "authorization_code",
                "code": answer["code"][0],
                "redirect_uri": REDIRECT_URI,
                "client_id": client_id,
                "code_verifier": verifier,
            },
        )


def _sign_in(discovery: dict[str, Any], client_id: str = CONSOLE_CLIENT) -> dict[str, Any]:
    response = _authorize(
        discovery["authorization_endpoint"], discovery["token_endpoint"], client_id
    )
    assert response.status_code == 200, response.text
    tokens: dict[str, Any] = response.json()
    return tokens


def _register(session: Session, subject: str) -> None:
    session.add(User(subject=subject, display_name="", roles=["developer"]))
    session.commit()


class _Admin:
    """Keycloak's admin API, for changing the provider underneath a running harness."""

    def __init__(self) -> None:
        token = httpx.post(
            f"{_keycloak()}/realms/master/protocol/openid-connect/token",
            data={
                "grant_type": "password",
                "client_id": "admin-cli",
                "username": ADMIN_USERNAME,
                "password": ADMIN_PASSWORD,
            },
            timeout=10,
        )
        token.raise_for_status()
        self._client = httpx.Client(
            base_url=f"{_keycloak()}/admin/realms/{_realm()}",
            headers={"Authorization": f"Bearer {token.json()['access_token']}"},
            timeout=10,
        )

    def add_signing_key(self, name: str) -> str:
        realm_id = self._client.get("").json()["id"]
        created = self._client.post(
            "/components",
            json={
                "name": name,
                "providerId": "rsa-generated",
                "providerType": "org.keycloak.keys.KeyProvider",
                "parentId": realm_id,
                # Above the realm's original key, so new tokens are signed with this.
                "config": {
                    "priority": ["500"],
                    "enabled": ["true"],
                    "active": ["true"],
                    "algorithm": ["RS256"],
                    "keySize": ["2048"],
                },
            },
        )
        assert created.status_code == 201, created.text
        return created.headers["location"].rsplit("/", 1)[-1]

    def remove_component(self, component_id: str) -> None:
        self._client.delete(f"/components/{component_id}").raise_for_status()


# -- the provider is registered the way the setup guide says ---------------------------


def test_discovery_gives_the_console_what_it_needs(
    tmp_path: Path, discovery: dict[str, Any]
) -> None:
    config = Authenticator(_settings(tmp_path, discovery)).console_sign_in()
    assert config["issuer"] == ISSUER
    assert config["authorizationEndpoint"] == discovery["authorization_endpoint"]
    assert config["tokenEndpoint"] == discovery["token_endpoint"]
    assert "S256" in discovery.get("code_challenge_methods_supported", [])


def test_the_provider_requires_pkce_from_the_console(discovery: dict[str, Any]) -> None:
    # Without a challenge the provider must refuse outright: PKCE is what stands in
    # for the client secret a browser cannot keep.
    refused = httpx.get(
        discovery["authorization_endpoint"],
        params={
            "response_type": "code",
            "client_id": CONSOLE_CLIENT,
            "redirect_uri": REDIRECT_URI,
            "scope": "openid",
            "state": "no-pkce",
        },
        follow_redirects=False,
        timeout=10,
    )
    assert refused.status_code == 302, refused.text[:300]
    assert "error=invalid_request" in refused.headers["location"]


def test_the_token_endpoint_allows_the_console_origin_only(discovery: dict[str, Any]) -> None:
    response = _authorize(discovery["authorization_endpoint"], discovery["token_endpoint"])
    assert response.status_code == 200, response.text
    assert response.headers.get("access-control-allow-origin") == CONSOLE_ORIGIN

    elsewhere = httpx.post(
        discovery["token_endpoint"],
        headers={"Origin": "https://elsewhere.example"},
        data={"grant_type": "authorization_code", "code": "x", "client_id": CONSOLE_CLIENT},
        timeout=10,
    )
    assert "access-control-allow-origin" not in elsewhere.headers


# -- the API accepts what the provider issues, and nothing else -----------------------


def test_the_console_signs_in_end_to_end(tmp_path: Path, discovery: dict[str, Any]) -> None:
    settings = _settings(tmp_path, discovery)
    with TestClient(create_app(settings)) as client:
        config = client.get("/api/v1/auth/oidc").json()
        tokens = _sign_in(
            {
                "authorization_endpoint": config["authorizationEndpoint"],
                "token_endpoint": config["tokenEndpoint"],
            }
        )
        assert tokens["token_type"].lower() == "bearer"
        bearer = {"Authorization": f"Bearer {tokens['access_token']}"}

        # Authenticated, but nobody has registered this account yet. The refusal
        # carries the subject an administrator needs to register it - Keycloak's is
        # an opaque id, not the user name or email the person would give.
        unknown = client.get("/api/v1/auth/me", headers=bearer)
        assert unknown.status_code == 401, unknown.text
        subject = unknown.json()["error"]["detail"]["subject"]
        assert subject == jwt.get_unverified_claims(tokens["access_token"])["sub"]
        assert subject != USERNAME
        # The console shows the message, not the detail.
        assert subject in unknown.json()["error"]["message"]

        with Session(create_engine(settings.metadata_url)) as session:
            _register(session, subject)

        me = client.get("/api/v1/auth/me", headers=bearer)
        assert me.status_code == 200, me.text
        assert me.json()["subject"] == subject
        assert me.json()["displayName"] == "Alice Example"
        assert me.json()["roles"] == ["developer"]


def test_a_token_issued_to_another_application_is_refused(
    tmp_path: Path, discovery: dict[str, Any], db: Session
) -> None:
    # Genuine, correctly signed, same realm, same person - but not issued for this API.
    tokens = _sign_in(discovery, client_id=OTHER_CLIENT)
    _register(db, jwt.get_unverified_claims(tokens["access_token"])["sub"])
    with pytest.raises(AuthenticationError, match="not valid") as refused:
        Authenticator(_settings(tmp_path, discovery)).authenticate(
            db, f"Bearer {tokens['access_token']}"
        )
    assert "aud" in refused.value.detail["reason"]


def test_the_id_token_is_not_an_access_token(
    tmp_path: Path, discovery: dict[str, Any], db: Session
) -> None:
    tokens = _sign_in(discovery)
    _register(db, jwt.get_unverified_claims(tokens["access_token"])["sub"])
    with pytest.raises(AuthenticationError, match="not valid"):
        Authenticator(_settings(tmp_path, discovery)).authenticate(
            db, f"Bearer {tokens['id_token']}"
        )


def test_a_token_signed_with_a_rotated_key_is_accepted(
    tmp_path: Path, discovery: dict[str, Any], db: Session
) -> None:
    """Providers rotate signing keys. A running API must follow without a restart."""

    authenticator = Authenticator(_settings(tmp_path, discovery))
    before = _sign_in(discovery)
    _register(db, jwt.get_unverified_claims(before["access_token"])["sub"])
    # This caches the key set as it was.
    authenticator.authenticate(db, f"Bearer {before['access_token']}")

    admin = _Admin()
    component = admin.add_signing_key(f"rotated-{secrets.token_hex(4)}")
    try:
        after = _sign_in(discovery)
        old_kid = jwt.get_unverified_header(before["access_token"])["kid"]
        assert jwt.get_unverified_header(after["access_token"])["kid"] != old_kid
        principal = authenticator.authenticate(db, f"Bearer {after['access_token']}")
        assert principal.subject == jwt.get_unverified_claims(after["access_token"])["sub"]
        # Tokens issued before the rotation stay good until they expire.
        authenticator.authenticate(db, f"Bearer {before['access_token']}")
    finally:
        admin.remove_component(component)
