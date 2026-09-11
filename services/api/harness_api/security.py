"""Authentication and actor identity.

Two kinds of caller reach this API:

* A person, authenticated by the identity provider. In a pilot that is OIDC; the
  ``dev`` mode issues locally signed tokens and is refused outside development.
* A registered IDE adapter, authenticated by a scoped integration credential. The
  adapter asserts which of *its* users is acting; the harness namespaces that actor
  by integration instance and never accepts a browser-supplied role as authority.

Both end up as a Principal. Everything downstream authorises against the Principal,
never against a header.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets as pysecrets
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.orm import Session

from harness_api.config import Settings
from harness_api.models import AppRole, IntegrationInstance, User, utcnow
from harness_worker.errors import (
    AuthenticationError,
    ConfigurationError,
    IdentityProviderError,
    PolicyError,
)

TOKEN_PREFIX_LENGTH = 8


@dataclass
class Principal:
    """Who is acting, and on whose authority."""

    subject: str
    display_name: str = ""
    roles: set[AppRole] = field(default_factory=set)
    user_id: str | None = None
    integration_id: str | None = None
    integration_scopes: tuple[str, ...] = ()
    delegated_actor: str | None = None
    durable_identity: bool = True

    @property
    def is_integration(self) -> bool:
        return self.integration_id is not None

    @property
    def actor_key(self) -> str:
        """Stable key for budgets and rate limits, namespaced by integration."""

        if self.integration_id:
            return f"{self.integration_id}:{self.delegated_actor or 'anonymous'}"
        return self.subject

    def has_role(self, *roles: AppRole) -> bool:
        return bool(self.roles.intersection(roles))

    def describe(self) -> dict[str, Any]:
        return {
            "subject": self.subject,
            "displayName": self.display_name,
            "roles": sorted(role.value for role in self.roles),
            "userId": self.user_id,
            "integrationId": self.integration_id,
            "integrationScopes": list(self.integration_scopes),
            "delegatedActor": self.delegated_actor,
            "durableIdentity": self.durable_identity,
        }


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_integration_token() -> tuple[str, str, str]:
    """Return (token, prefix, hash). Only the caller ever sees the token."""

    raw = pysecrets.token_urlsafe(32)
    token = f"odbh_{raw}"
    return token, token[:TOKEN_PREFIX_LENGTH], hash_token(token)


class ProviderDocument:
    """One of the identity provider's published JSON documents, cached.

    The key set and the discovery document change rarely; fetching them on every
    request would make the provider a per-request dependency.
    """

    def __init__(self, url: str, what: str, ttl_seconds: int = 300) -> None:
        self._url = url
        self._what = what
        self._ttl = ttl_seconds
        self._document: dict[str, Any] | None = None
        self._fetched_at = 0.0

    def get(self) -> dict[str, Any]:
        if self._document is None or (time.monotonic() - self._fetched_at) > self._ttl:
            try:
                response = httpx.get(self._url, timeout=5.0)
                response.raise_for_status()
                document = response.json()
            except (httpx.HTTPError, ValueError) as exc:
                raise IdentityProviderError(
                    f"Could not fetch the identity provider's {self._what}.",
                    detail={"url": self._url, "reason": str(exc)},
                ) from exc
            if not isinstance(document, dict):
                raise IdentityProviderError(
                    f"The identity provider's {self._what} is not a JSON object.",
                    detail={"url": self._url},
                )
            self._document = document
            self._fetched_at = time.monotonic()
        return self._document


class Authenticator:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._jwks: ProviderDocument | None = None
        self._discovery: ProviderDocument | None = None
        if settings.auth_mode == "oidc":
            if not settings.oidc_jwks_url or not settings.oidc_issuer:
                raise ConfigurationError(
                    "HARNESS_AUTH_MODE=oidc requires HARNESS_OIDC_ISSUER and HARNESS_OIDC_JWKS_URL."
                )
            self._jwks = ProviderDocument(settings.oidc_jwks_url, "signing keys")
            self._discovery = ProviderDocument(
                settings.oidc_issuer.rstrip("/") + "/.well-known/openid-configuration",
                "discovery document",
            )
        elif settings.env != "development" and not settings.allow_dev_auth_outside_development:
            raise ConfigurationError(
                "HARNESS_AUTH_MODE=dev issues locally signed tokens and is refused "
                f"when HARNESS_ENV={settings.env!r}. Configure OIDC, or set "
                "HARNESS_ALLOW_DEV_AUTH_OUTSIDE_DEVELOPMENT=true if you accept that."
            )

    # -- token issuing (development only) -----------------------------------------

    def issue_dev_token(self, subject: str, roles: list[str], display_name: str = "") -> str:
        if self._settings.auth_mode != "dev":
            raise ConfigurationError(
                "Development tokens are only issued when HARNESS_AUTH_MODE=dev."
            )
        now = datetime.now(UTC)
        claims = {
            "iss": "oracledbharness-dev",
            "aud": self._settings.oidc_audience,
            "sub": subject,
            "name": display_name or subject,
            "roles": roles,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(hours=8)).timestamp()),
        }
        return jwt.encode(claims, self._settings.dev_token_secret, algorithm="HS256")

    # -- console sign-in -----------------------------------------------------------

    def console_sign_in(self) -> dict[str, Any]:
        """What the console needs to run an authorization code flow with PKCE.

        Nothing here is secret. The console is a public client, so the answer is the
        same for everyone and is served without authentication.
        """

        settings = self._settings
        if settings.auth_mode != "oidc":
            raise PolicyError(
                "This deployment uses the development identity mode. Sign in with a "
                "development token."
            )
        if not settings.oidc_client_id:
            raise ConfigurationError(
                "HARNESS_OIDC_CLIENT_ID is not set, so the console cannot sign anyone in. "
                "Register the console with the identity provider as a public client "
                "and set its client ID."
            )
        authorization = settings.oidc_authorization_endpoint
        token = settings.oidc_token_endpoint
        if not (authorization and token):
            assert self._discovery is not None
            discovered = self._discovery.get()
            # A token's iss is checked against the configured issuer, so a provider
            # that describes itself differently would issue tokens this API refuses.
            if discovered.get("issuer") != settings.oidc_issuer:
                raise IdentityProviderError(
                    "The identity provider's discovery document names a different "
                    "issuer from HARNESS_OIDC_ISSUER. They must match exactly, "
                    "including any trailing slash.",
                    detail={
                        "configuredIssuer": settings.oidc_issuer,
                        "discoveredIssuer": discovered.get("issuer"),
                    },
                )
            authorization = authorization or discovered.get("authorization_endpoint", "")
            token = token or discovered.get("token_endpoint", "")
            if not (authorization and token):
                raise IdentityProviderError(
                    "The identity provider's discovery document has no authorization "
                    "or token endpoint. Set HARNESS_OIDC_AUTHORIZATION_ENDPOINT and "
                    "HARNESS_OIDC_TOKEN_ENDPOINT."
                )
        return {
            "issuer": settings.oidc_issuer,
            "clientId": settings.oidc_client_id,
            "authorizationEndpoint": authorization,
            "tokenEndpoint": token,
            "scopes": settings.oidc_scopes.split(),
            "audience": settings.oidc_audience if settings.oidc_request_audience else None,
        }

    # -- verification --------------------------------------------------------------

    def authenticate(self, session: Session, authorization: str | None) -> Principal:
        token = _bearer(authorization)
        if token.startswith("odbh_"):
            raise AuthenticationError(
                "This is an integration credential. Use the integration endpoints, "
                "which require the adapter to assert which of its users is acting."
            )
        claims = self._verify(token)
        subject = claims.get("sub")
        if not subject:
            raise AuthenticationError("The token carries no subject claim.")
        user = session.scalars(select(User).where(User.subject == subject)).first()
        if user is None:
            raise AuthenticationError(
                "This account is authenticated but not registered in the harness. An "
                "administrator has to add it and grant target access.",
                detail={"subject": subject},
            )
        if user.disabled:
            raise AuthenticationError("This account is disabled.", detail={"subject": subject})
        return Principal(
            subject=user.subject,
            display_name=user.display_name or claims.get("name", ""),
            roles=user.role_set(),
            user_id=user.id,
        )

    def authenticate_integration(
        self,
        session: Session,
        authorization: str | None,
        *,
        actor_reference: str | None,
        actor_is_durable: bool = True,
    ) -> Principal:
        token = _bearer(authorization)
        if not token.startswith("odbh_"):
            raise AuthenticationError("An integration credential is required on this endpoint.")
        digest = hash_token(token)
        # The prefix narrows the search; it does not identify a credential. Two
        # credentials can share one, so every candidate is checked - stopping at the
        # first would make the other one unusable.
        candidates = session.scalars(
            select(IntegrationInstance).where(
                IntegrationInstance.token_prefix == token[:TOKEN_PREFIX_LENGTH]
            )
        ).all()
        instance: IntegrationInstance | None = None
        for candidate in candidates:
            if hmac.compare_digest(digest, candidate.token_hash):
                instance = candidate
        if instance is None:
            # Still perform a digest comparison when the prefix has no candidates.
            hmac.compare_digest(digest, "0" * 64)
            raise AuthenticationError("The integration credential is not recognised.")
        if not instance.enabled or instance.revoked_at is not None:
            raise AuthenticationError(
                "The integration credential has been revoked or disabled.",
                detail={"integrationId": instance.id},
            )
        instance.last_seen_at = utcnow()
        return Principal(
            subject=f"integration:{instance.id}",
            display_name=instance.name,
            roles=set(),
            integration_id=instance.id,
            integration_scopes=tuple(instance.scopes or []),
            delegated_actor=actor_reference,
            durable_identity=actor_is_durable and bool(actor_reference),
        )

    def _verify(self, token: str) -> dict[str, Any]:
        try:
            if self._settings.auth_mode == "dev":
                return jwt.decode(
                    token,
                    self._settings.dev_token_secret,
                    algorithms=["HS256"],
                    audience=self._settings.oidc_audience,
                )
            assert self._jwks is not None
            return jwt.decode(
                token,
                self._jwks.get(),
                algorithms=["RS256", "ES256"],
                audience=self._settings.oidc_audience,
                issuer=self._settings.oidc_issuer,
            )
        except JWTError as exc:
            raise AuthenticationError(
                "The access token is not valid.", detail={"reason": str(exc)}
            ) from exc


def _bearer(authorization: str | None) -> str:
    if not authorization:
        raise AuthenticationError("An Authorization header is required.")
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        raise AuthenticationError("Expected an Authorization header of the form 'Bearer <token>'.")
    return value.strip()
