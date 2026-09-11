"""The OIDC identity mode, exercised with real signatures against a stubbed provider.

The pilot runs with HARNESS_AUTH_MODE=oidc, and until now only the development signer
had been exercised. These tests sign RS256 tokens with a generated key, publish the
public half as a JWKS, and check both halves of the console's sign-in: the
configuration the console reads to start an authorization code flow, and the
verification of the access token it comes back with.

They stub the provider's HTTP endpoints. Qualifying against a real provider is still
a separate step; see docs/setup.md.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from jose import jwk, jwt
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from harness_api import security
from harness_api.app import create_app
from harness_api.config import Settings
from harness_api.models import Base, User
from harness_api.security import Authenticator
from harness_worker.errors import (
    AuthenticationError,
    ConfigurationError,
    IdentityProviderError,
    PolicyError,
)

ISSUER = "https://login.example.internal/realms/engineering"
JWKS_URL = f"{ISSUER}/protocol/openid-connect/certs"
DISCOVERY_URL = f"{ISSUER}/.well-known/openid-configuration"
KID = "test-key"


def _private_pem(key: rsa.RSAPrivateKey) -> str:
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


@pytest.fixture(scope="module")
def signing_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def provider(signing_key: rsa.RSAPrivateKey, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Serve a discovery document and a JWKS; record which URLs were fetched."""

    public_pem = (
        signing_key.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
    public_jwk = jwk.construct(public_pem, "RS256").to_dict()
    public_jwk.update({"kid": KID, "use": "sig", "alg": "RS256"})
    documents: dict[str, Any] = {
        JWKS_URL: {"keys": [public_jwk]},
        DISCOVERY_URL: {
            "issuer": ISSUER,
            "authorization_endpoint": f"{ISSUER}/protocol/openid-connect/auth",
            "token_endpoint": f"{ISSUER}/protocol/openid-connect/token",
            "jwks_uri": JWKS_URL,
        },
    }
    fetched: list[str] = []

    def fake_get(url: str, **_: Any) -> httpx.Response:
        fetched.append(url)
        if url not in documents:
            raise httpx.ConnectError("unreachable", request=httpx.Request("GET", url))
        return httpx.Response(200, json=documents[url], request=httpx.Request("GET", url))

    monkeypatch.setattr(security.httpx, "get", fake_get)
    return {"documents": documents, "fetched": fetched}


def _settings(tmp_path: Path, **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "env": "development",
        "metadata_url": f"sqlite+pysqlite:///{(tmp_path / 'oidc.sqlite3').as_posix()}",
        "secret_dir": str(tmp_path),
        "auth_mode": "oidc",
        "oidc_issuer": ISSUER,
        "oidc_jwks_url": JWKS_URL,
        "oidc_audience": "oracledbharness",
        "oidc_client_id": "oracledbharness-console",
        "oracle_backend": "fake",
        "oracle_fake_data_dir": str(tmp_path / "fake"),
    }
    values.update(overrides)
    return Settings(**values)


def _token(key: rsa.RSAPrivateKey, **claims: Any) -> str:
    now = int(time.time())
    body = {
        "iss": ISSUER,
        "aud": "oracledbharness",
        "sub": "alice@example.internal",
        "name": "Alice",
        "iat": now,
        "exp": now + 300,
    }
    body.update(claims)
    return jwt.encode(body, _private_pem(key), algorithm="RS256", headers={"kid": KID})


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Session]:
    engine = create_engine(f"sqlite+pysqlite:///{(tmp_path / 'users.sqlite3').as_posix()}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(User(subject="alice@example.internal", display_name="", roles=["developer"]))
        session.commit()
        yield session


# -- the configuration the console starts from ----------------------------------------


def test_sign_in_configuration_comes_from_discovery(
    tmp_path: Path, provider: dict[str, Any]
) -> None:
    config = Authenticator(_settings(tmp_path)).console_sign_in()
    assert config == {
        "issuer": ISSUER,
        "clientId": "oracledbharness-console",
        "authorizationEndpoint": f"{ISSUER}/protocol/openid-connect/auth",
        "tokenEndpoint": f"{ISSUER}/protocol/openid-connect/token",
        "scopes": ["openid", "profile", "email"],
        "audience": None,
    }
    assert provider["fetched"] == [DISCOVERY_URL]


def test_configured_endpoints_skip_discovery(tmp_path: Path, provider: dict[str, Any]) -> None:
    config = Authenticator(
        _settings(
            tmp_path,
            oidc_authorization_endpoint="https://idp.internal/authorize",
            oidc_token_endpoint="https://idp.internal/token",
            oidc_request_audience=True,
        )
    ).console_sign_in()
    assert config["authorizationEndpoint"] == "https://idp.internal/authorize"
    assert config["tokenEndpoint"] == "https://idp.internal/token"
    assert config["audience"] == "oracledbharness"
    assert provider["fetched"] == []


def test_a_discovery_document_for_another_issuer_is_refused(
    tmp_path: Path, provider: dict[str, Any]
) -> None:
    provider["documents"][DISCOVERY_URL]["issuer"] = ISSUER + "/"
    with pytest.raises(IdentityProviderError, match="different issuer"):
        Authenticator(_settings(tmp_path)).console_sign_in()


def test_an_unreachable_provider_is_reported_as_such(
    tmp_path: Path, provider: dict[str, Any]
) -> None:
    del provider["documents"][DISCOVERY_URL]
    with pytest.raises(IdentityProviderError, match="discovery document"):
        Authenticator(_settings(tmp_path)).console_sign_in()


def test_no_client_id_is_a_configuration_error(tmp_path: Path, provider: dict[str, Any]) -> None:
    settings = _settings(tmp_path, oidc_client_id="")
    with pytest.raises(ConfigurationError, match="HARNESS_OIDC_CLIENT_ID"):
        Authenticator(settings).console_sign_in()
    assert any("HARNESS_OIDC_CLIENT_ID" in warning for warning in settings.startup_warnings())


def test_development_mode_has_no_oidc_sign_in(tmp_path: Path) -> None:
    settings = _settings(tmp_path, auth_mode="dev", dev_token_secret="test-secret")
    with pytest.raises(PolicyError):
        Authenticator(settings).console_sign_in()


def test_the_sign_in_endpoint_serves_the_configuration_unauthenticated(
    tmp_path: Path, provider: dict[str, Any]
) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        response = client.get("/api/v1/auth/oidc")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["clientId"] == "oracledbharness-console"
        assert body["tokenEndpoint"].endswith("/token")

        # The provider goes away once the cached copy has expired.
        del provider["documents"][DISCOVERY_URL]
        client.app.state.harness.authenticator._discovery._document = None  # type: ignore[attr-defined]
        failed = client.get("/api/v1/auth/oidc")
        assert failed.status_code == 502
        assert failed.json()["error"]["code"] == "identity_provider_unavailable"


# -- the token the console comes back with -------------------------------------------


def test_a_provider_signed_token_for_a_registered_user_is_accepted(
    tmp_path: Path, provider: dict[str, Any], signing_key: rsa.RSAPrivateKey, db: Session
) -> None:
    principal = Authenticator(_settings(tmp_path)).authenticate(db, f"Bearer {_token(signing_key)}")
    assert principal.subject == "alice@example.internal"
    assert principal.display_name == "Alice"
    assert {role.value for role in principal.roles} == {"developer"}
    assert provider["fetched"] == [JWKS_URL]


def test_roles_in_the_token_grant_nothing(
    tmp_path: Path, provider: dict[str, Any], signing_key: rsa.RSAPrivateKey, db: Session
) -> None:
    token = _token(signing_key, roles=["administrator"], groups=["dba"])
    principal = Authenticator(_settings(tmp_path)).authenticate(db, f"Bearer {token}")
    assert {role.value for role in principal.roles} == {"developer"}


@pytest.mark.parametrize(
    "claims",
    [
        pytest.param({"aud": "some-other-api"}, id="audience"),
        pytest.param({"iss": "https://login.example.internal/realms/other"}, id="issuer"),
        pytest.param({"exp": int(time.time()) - 60}, id="expired"),
    ],
)
def test_a_token_meant_for_something_else_is_refused(
    tmp_path: Path,
    provider: dict[str, Any],
    signing_key: rsa.RSAPrivateKey,
    db: Session,
    claims: dict[str, Any],
) -> None:
    with pytest.raises(AuthenticationError):
        Authenticator(_settings(tmp_path)).authenticate(
            db, f"Bearer {_token(signing_key, **claims)}"
        )


def test_a_token_signed_by_another_key_is_refused(
    tmp_path: Path, provider: dict[str, Any], db: Session
) -> None:
    impostor = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(AuthenticationError):
        Authenticator(_settings(tmp_path)).authenticate(db, f"Bearer {_token(impostor)}")


def test_a_valid_token_for_an_unregistered_subject_is_refused(
    tmp_path: Path, provider: dict[str, Any], signing_key: rsa.RSAPrivateKey, db: Session
) -> None:
    token = _token(signing_key, sub="mallory@example.internal")
    with pytest.raises(AuthenticationError, match="not registered"):
        Authenticator(_settings(tmp_path)).authenticate(db, f"Bearer {token}")


def test_an_unreachable_key_set_is_not_reported_as_a_bad_token(
    tmp_path: Path, provider: dict[str, Any], signing_key: rsa.RSAPrivateKey, db: Session
) -> None:
    del provider["documents"][JWKS_URL]
    with pytest.raises(IdentityProviderError, match="signing keys"):
        Authenticator(_settings(tmp_path)).authenticate(db, f"Bearer {_token(signing_key)}")
