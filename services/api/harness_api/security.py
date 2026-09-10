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
from harness_worker.errors import AuthenticationError, ConfigurationError

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


class JwksCache:
    """Small JWKS cache so every request does not fetch the key set."""

    def __init__(self, url: str, ttl_seconds: int = 300) -> None:
        self._url = url
        self._ttl = ttl_seconds
        self._keys: dict[str, Any] | None = None
        self._fetched_at = 0.0

    def keys(self) -> dict[str, Any]:
        if self._keys is None or (time.monotonic() - self._fetched_at) > self._ttl:
            response = httpx.get(self._url, timeout=5.0)
            response.raise_for_status()
            self._keys = response.json()
            self._fetched_at = time.monotonic()
        return self._keys


class Authenticator:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._jwks: JwksCache | None = None
        if settings.auth_mode == "oidc":
            if not settings.oidc_jwks_url or not settings.oidc_issuer:
                raise ConfigurationError(
                    "HARNESS_AUTH_MODE=oidc requires HARNESS_OIDC_ISSUER and HARNESS_OIDC_JWKS_URL."
                )
            self._jwks = JwksCache(settings.oidc_jwks_url)
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
        instance = session.scalars(
            select(IntegrationInstance).where(
                IntegrationInstance.token_prefix == token[:TOKEN_PREFIX_LENGTH]
            )
        ).first()
        # Compare in constant time even when no candidate was found, so a wrong prefix
        # and a wrong secret take the same path.
        expected = instance.token_hash if instance else "0" * 64
        if not hmac.compare_digest(digest, expected) or instance is None:
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
                self._jwks.keys(),
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
