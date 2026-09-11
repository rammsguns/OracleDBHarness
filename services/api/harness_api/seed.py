"""Seed a demonstration environment.

This creates the accounts, credential references, targets and grants that the local
walkthrough and the test suite use. It is safe to run repeatedly with the same
endpoints; it refuses, rather than ignores, endpoints that differ from those already
stored.

It is a *development* convenience. It refuses to run when the identity mode is not
the development one, because the accounts it creates would otherwise be real access.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from harness_api.config import Settings, get_settings
from harness_api.db import build_engine, build_session_factory, initialize_schema
from harness_api.execution import ExecutionService
from harness_api.models import (
    AppRole,
    ConnectionProfile,
    SecretReference,
    User,
    UserTargetGrant,
)
from harness_api.policy import (
    PERMISSION_COMPILE,
    PERMISSION_READ,
    PERMISSION_RUNBOOK,
    PERMISSION_WORKSHEET,
)
from harness_api.runbooks import register_definitions
from harness_worker.errors import ConfigurationError

DEMO_USERS = [
    ("dev@example.internal", "Dev Developer", [AppRole.DEVELOPER.value]),
    ("dba@example.internal", "Dana DBA", [AppRole.DBA.value]),
    ("viewer@example.internal", "Vic Viewer", [AppRole.VIEWER.value]),
    ("admin@example.internal", "Ada Administrator", [AppRole.ADMINISTRATOR.value]),
]


@dataclass(frozen=True)
class TargetEndpoint:
    """Where one demonstration target actually points.

    The defaults describe the local stand-in, which invents a database per
    host/port/service and needs no real credential. A qualification run overrides
    them so the same seeded users, grants and profiles sit in front of a real Oracle
    target — which is what lets the integration and end-to-end suites run against
    one without a parallel set of fixtures.
    """

    host: str = "localhost"
    port: int = 1521
    service_name: str = ""
    username: str = "harness_app"
    default_schema: str = "HARNESS_APP"
    # Names of a credential reference and the file it points at - not a credential.
    secret_name: str = "harness-app"  # noqa: S105
    secret_locator: str = "harness_app.password"  # noqa: S105
    description: str = "Oracle account used by the demonstration targets."


DEMO_TARGETS: list[dict[str, Any]] = [
    {
        "name": "development",
        "environment": "development",
        "service_name": "DEVPDB1",
        "worksheets_enabled": True,
        "mutating_runbooks_enabled": True,
        "notes": "Development database. Free-form worksheets and reviewed runbooks are on.",
    },
    {
        "name": "test",
        "environment": "test",
        "service_name": "TESTPDB1",
        "worksheets_enabled": True,
        "mutating_runbooks_enabled": True,
        "notes": "Test database used for the second-target isolation checks.",
    },
    {
        "name": "production",
        "environment": "production",
        "service_name": "PRODPDB1",
        "worksheets_enabled": False,
        "mutating_runbooks_enabled": False,
        "notes": "Observation only. Reviewed diagnostics; no worksheets, no mutations.",
    },
]


def _resolve_endpoints(
    overrides: dict[str, TargetEndpoint] | None,
) -> dict[str, TargetEndpoint]:
    """One endpoint per demonstration target, defaulting to the stand-in's.

    An override for a target the seed does not create is refused rather than
    ignored: it means the caller expected a target that will not exist, and finding
    that out from a later connection error is a much longer trip.
    """

    names = [str(spec["name"]) for spec in DEMO_TARGETS]
    unknown = set(overrides or {}) - set(names)
    if unknown:
        raise ConfigurationError(
            f"No demonstration target is named {sorted(unknown)}.",
            detail={"known": names},
        )
    resolved: dict[str, TargetEndpoint] = {}
    for spec in DEMO_TARGETS:
        name = str(spec["name"])
        override = (overrides or {}).get(name)
        resolved[name] = override or TargetEndpoint(service_name=str(spec["service_name"]))

    # Targets may share a credential reference, but only if they mean the same file.
    # The same name over two files would leave one target authenticating with the
    # other's password.
    locators: dict[str, tuple[str, str]] = {}
    for name, endpoint in resolved.items():
        seen = locators.setdefault(endpoint.secret_name, (name, endpoint.secret_locator))
        if seen[1] != endpoint.secret_locator:
            raise ConfigurationError(
                f"Targets {seen[0]!r} and {name!r} both use the credential reference "
                f"{endpoint.secret_name!r} but point it at different files.",
                detail={seen[0]: seen[1], name: endpoint.secret_locator},
            )
    return resolved


def _refuse_drift(db: Session, resolved: dict[str, TargetEndpoint]) -> None:
    """Refuse a re-seed whose endpoints differ from what is already stored.

    The seed never changes an existing target or credential reference, so a
    difference would otherwise be silently ignored: the target would go on pointing
    at the old database, or reading the old credential. Updating in place is not
    the answer either. Execution history, audit records and the probed identity
    and capabilities all hang off the profile, and would quietly be attributed to
    a different database. ``seed`` calls this before it writes any seed data.
    """

    for name, endpoint in resolved.items():
        secret = db.scalars(
            select(SecretReference).where(SecretReference.name == endpoint.secret_name)
        ).first()
        if secret is not None and (secret.provider, secret.locator) != (
            "file",
            endpoint.secret_locator,
        ):
            raise ConfigurationError(
                f"The credential reference {endpoint.secret_name!r} already exists and "
                "does not point at the requested file. Seed into a fresh metadata "
                "database.",
                detail={
                    "stored": {"provider": secret.provider, "locator": secret.locator},
                    "requested": {"provider": "file", "locator": endpoint.secret_locator},
                },
            )

        profile = db.scalars(
            select(ConnectionProfile).where(ConnectionProfile.name == name)
        ).first()
        if profile is None:
            continue
        stored = {
            "host": profile.host,
            "port": profile.port,
            "service_name": profile.service_name,
            "username": profile.username,
            "default_schema": profile.default_schema,
            "secret_name": profile.secret.name,
        }
        requested = {field: getattr(endpoint, field) for field in stored}
        differences = {
            field: {"stored": stored[field], "requested": requested[field]}
            for field in stored
            if stored[field] != requested[field]
        }
        if differences:
            raise ConfigurationError(
                f"The target {name!r} already exists and points somewhere else "
                f"({', '.join(differences)}). The seed will not repoint an existing "
                "target. Seed into a fresh metadata database.",
                detail=differences,
            )


def seed(
    settings: Settings | None = None,
    *,
    probe: bool = True,
    endpoints: dict[str, TargetEndpoint] | None = None,
) -> dict:
    settings = settings or get_settings()
    if settings.auth_mode != "dev":
        raise ConfigurationError(
            "The demonstration seed only runs with HARNESS_AUTH_MODE=dev. In a pilot, "
            "create accounts through the admin API against your identity provider."
        )

    resolved = _resolve_endpoints(endpoints)

    engine = build_engine(settings)
    initialize_schema(engine)
    factory = build_session_factory(engine)
    # First, so that a refused re-seed leaves no seed data behind: no runbook
    # definitions, no rows, no placeholder password files.
    with factory() as db:
        _refuse_drift(db, resolved)

    secret_dir = Path(settings.secret_dir)
    secret_dir.mkdir(parents=True, exist_ok=True)
    for endpoint in resolved.values():
        password_file = secret_dir / endpoint.secret_locator
        if not password_file.exists():
            # The stand-in backend ignores the value; a real target reads its password
            # from a mounted file exactly like this one. A caller pointing the seed at
            # a real database writes the file itself before calling.
            password_file.write_text("not-a-real-password", encoding="utf-8")

    execution = ExecutionService(settings, factory)
    created: dict[str, list[str]] = {"users": [], "targets": [], "grants": []}

    try:
        with factory() as db:
            register_definitions(db, execution)

            secrets: dict[str, SecretReference] = {}
            for endpoint in resolved.values():
                if endpoint.secret_name in secrets:
                    continue
                secret = db.scalars(
                    select(SecretReference).where(SecretReference.name == endpoint.secret_name)
                ).first()
                if secret is None:
                    secret = SecretReference(
                        name=endpoint.secret_name,
                        provider="file",
                        locator=endpoint.secret_locator,
                        description=endpoint.description,
                    )
                    db.add(secret)
                secrets[endpoint.secret_name] = secret
            db.commit()

            users: dict[str, User] = {}
            for subject, display_name, roles in DEMO_USERS:
                user = db.scalars(select(User).where(User.subject == subject)).first()
                if user is None:
                    user = User(subject=subject, display_name=display_name, roles=roles)
                    db.add(user)
                    created["users"].append(subject)
                users[subject] = user
            db.commit()

            profiles: dict[str, ConnectionProfile] = {}
            for spec in DEMO_TARGETS:
                profile = db.scalars(
                    select(ConnectionProfile).where(ConnectionProfile.name == str(spec["name"]))
                ).first()
                endpoint = resolved[str(spec["name"])]
                if profile is None:
                    profile = ConnectionProfile(
                        name=spec["name"],
                        environment=spec["environment"],
                        host=endpoint.host,
                        port=endpoint.port,
                        service_name=endpoint.service_name,
                        username=endpoint.username,
                        default_schema=endpoint.default_schema,
                        secret_reference_id=secrets[endpoint.secret_name].id,
                        worksheets_enabled=spec["worksheets_enabled"],
                        mutating_runbooks_enabled=spec["mutating_runbooks_enabled"],
                        notes=spec["notes"],
                    )
                    db.add(profile)
                    created["targets"].append(str(spec["name"]))
                profiles[str(spec["name"])] = profile
            db.commit()

            grants = {
                "dev@example.internal": {
                    "development": [PERMISSION_READ, PERMISSION_WORKSHEET, PERMISSION_COMPILE],
                    "test": [PERMISSION_READ, PERMISSION_WORKSHEET, PERMISSION_COMPILE],
                },
                "dba@example.internal": {
                    "development": [PERMISSION_READ, PERMISSION_WORKSHEET, PERMISSION_RUNBOOK],
                    "test": [PERMISSION_READ, PERMISSION_WORKSHEET, PERMISSION_RUNBOOK],
                    "production": [PERMISSION_READ],
                },
                "viewer@example.internal": {"production": [PERMISSION_READ]},
                "admin@example.internal": {"development": [PERMISSION_READ]},
            }
            for subject, target_permissions in grants.items():
                for target_name, permissions in target_permissions.items():
                    user = users[subject]
                    profile = profiles[target_name]
                    row = db.scalars(
                        select(UserTargetGrant).where(
                            UserTargetGrant.user_id == user.id,
                            UserTargetGrant.profile_id == profile.id,
                        )
                    ).first()
                    if row is None:
                        row = UserTargetGrant(user_id=user.id, profile_id=profile.id)
                        db.add(row)
                        created["grants"].append(f"{subject} -> {target_name}")
                    row.permissions = permissions
            db.commit()

            if probe:
                for name, profile in profiles.items():
                    grant = db.scalars(
                        select(UserTargetGrant).where(UserTargetGrant.profile_id == profile.id)
                    ).first()
                    try:
                        execution.probe_target(db, profile, grant)
                    except Exception as exc:  # noqa: BLE001 - a failed probe is reportable
                        created.setdefault("probeFailures", []).append(f"{name}: {exc}")
    finally:
        execution.shutdown()

    return created


def main() -> None:  # pragma: no cover - entry point
    parser = argparse.ArgumentParser(description="Seed the OracleDBHarness demo data.")
    parser.add_argument(
        "--no-probe",
        action="store_true",
        help="Skip connecting to each target to prove its identity and capabilities.",
    )
    args = parser.parse_args()
    result = seed(probe=not args.no_probe)
    for key, values in result.items():
        print(f"{key}: {len(values)}")
        for value in values:
            print(f"  {value}")


if __name__ == "__main__":  # pragma: no cover
    main()
