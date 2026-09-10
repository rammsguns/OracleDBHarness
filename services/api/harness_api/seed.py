"""Seed a demonstration environment.

This creates the accounts, credential references, targets and grants that the local
walkthrough and the test suite use. It is safe to run repeatedly.

It is a *development* convenience. It refuses to run when the identity mode is not
the development one, because the accounts it creates would otherwise be real access.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from sqlalchemy import select

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


def seed(settings: Settings | None = None, *, probe: bool = True) -> dict:
    settings = settings or get_settings()
    if settings.auth_mode != "dev":
        raise ConfigurationError(
            "The demonstration seed only runs with HARNESS_AUTH_MODE=dev. In a pilot, "
            "create accounts through the admin API against your identity provider."
        )

    secret_dir = Path(settings.secret_dir)
    secret_dir.mkdir(parents=True, exist_ok=True)
    password_file = secret_dir / "harness_app.password"
    if not password_file.exists():
        # The stand-in backend ignores the value; a real target reads its password
        # from a mounted file exactly like this one.
        password_file.write_text("not-a-real-password", encoding="utf-8")

    engine = build_engine(settings)
    initialize_schema(engine)
    factory = build_session_factory(engine)
    execution = ExecutionService(settings, factory)
    created: dict[str, list[str]] = {"users": [], "targets": [], "grants": []}

    try:
        with factory() as db:
            register_definitions(db, execution)

            secret = db.scalars(
                select(SecretReference).where(SecretReference.name == "harness-app")
            ).first()
            if secret is None:
                secret = SecretReference(
                    name="harness-app",
                    provider="file",
                    locator="harness_app.password",
                    description="Oracle account used by the demonstration targets.",
                )
                db.add(secret)
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
                if profile is None:
                    profile = ConnectionProfile(
                        name=spec["name"],
                        environment=spec["environment"],
                        host="localhost",
                        port=1521,
                        service_name=spec["service_name"],
                        username="harness_app",
                        default_schema="HARNESS_APP",
                        secret_reference_id=secret.id,
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
