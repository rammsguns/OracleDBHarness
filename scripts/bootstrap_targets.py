"""Provision real Oracle targets from a credential-free manifest."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

from sqlalchemy import select

from harness_api.config import Settings, get_settings
from harness_api.db import build_engine, build_session_factory, initialize_schema
from harness_api.execution import ExecutionService
from harness_api.models import ConnectionProfile, SecretReference, User, UserTargetGrant
from harness_api.policy import is_production
from harness_worker.types import ExecutionLimits, StatementKind

ROLES = {"viewer", "developer", "dba", "administrator"}
NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]*$")


def _required(value: dict, key: str, kind: type):
    result = value.get(key)
    if not isinstance(result, kind) or (kind is str and not result.strip()):
        raise ValueError(f"{key} must be a nonempty {kind.__name__}")
    return result


def validate_manifest(data: dict) -> dict:
    if not isinstance(data, dict) or data.get("version") != 1:
        raise ValueError("manifest version must be 1")
    if not isinstance(data.get("users"), list) or not isinstance(data.get("targets"), list):
        raise ValueError("users and targets must be lists")
    if not data["users"] or not data["targets"]:
        raise ValueError("users and targets must be nonempty")
    subjects, names, refs = set(), set(), {}
    for user in data["users"]:
        subject = _required(user, "subject", str)
        roles = _required(user, "roles", list)
        if subject in subjects or not roles or set(roles) - ROLES:
            raise ValueError("duplicate subject or invalid roles")
        if data.get("authMode") == "oidc" and user.get("oidcSub") != subject:
            raise ValueError("OIDC users require an explicit oidcSub equal to the token sub")
        subjects.add(subject)
    for target in data["targets"]:
        name = _required(target, "name", str)
        if name in names or not NAME.fullmatch(name):
            raise ValueError("duplicate or invalid target name")
        names.add(name)
        for key in ("host", "serviceName", "username", "defaultSchema", "secretReference"):
            _required(target, key, str)
        if type(target.get("port")) is not int or not 1 <= target["port"] <= 65535:
            raise ValueError(f"{name}: invalid port")
        if target.get("environment") not in {"development", "test", "production"}:
            raise ValueError(f"{name}: invalid environment")
        for flag in ("worksheetsEnabled", "mutatingRunbooksEnabled"):
            if type(target.get(flag)) is not bool:
                raise ValueError(f"{name}: {flag} must be boolean")
        if type(target.get("enabled", True)) is not bool:
            raise ValueError(f"{name}: enabled must be boolean")
        if is_production(target["environment"]) and (
            target["worksheetsEnabled"] or target["mutatingRunbooksEnabled"]
        ):
            raise ValueError(f"{name}: production targets must be observation only")
        if target.get("protocol", "tcp") not in {"tcp", "tcps"}:
            raise ValueError(f"{name}: invalid protocol")
        if target.get("protocol") == "tcps" and not target.get("walletDir"):
            raise ValueError(f"{name}: TCPS requires walletDir")
        if bool(target.get("walletSecretReference")) != bool(target.get("walletDir")):
            raise ValueError(f"{name}: walletDir and walletSecretReference must be paired")
        for ref_key in ("secretReference", "walletSecretReference"):
            ref = target.get(ref_key)
            if not ref:
                continue
            locator = _required(
                target,
                "passwordFile" if ref_key == "secretReference" else "walletPasswordFile",
                str,
            )
            if (
                not NAME.fullmatch(ref)
                or Path(locator).name != locator
                or "/" in locator
                or "\\" in locator
                or locator in {".", ".."}
            ):
                raise ValueError(f"{name}: invalid secret reference or file name")
            if ref in refs and refs[ref] != locator:
                raise ValueError(f"{ref}: conflicting secret files")
            refs[ref] = locator
    if data.get("authMode") not in {"dev", "oidc"}:
        raise ValueError("authMode must be dev or oidc")
    return data


def _validate_secret_files(refs: dict[str, str], secret_dir: Path) -> None:
    for name, locator in refs.items():
        path = secret_dir / locator
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"{name}: secret file is missing or is a symlink")
        if os.name == "posix" and path.stat().st_mode & 0o777 != 0o600:
            raise ValueError(f"{name}: secret file must be chmod 600")
        if path.stat().st_size == 0:
            raise ValueError(f"{name}: secret file is empty")


def bootstrap(data: dict, settings: Settings, *, probe: bool = True) -> list[dict]:
    data = validate_manifest(data)
    if settings.auth_mode != data["authMode"]:
        raise ValueError("manifest authMode does not match HARNESS_AUTH_MODE")
    if probe and settings.oracle_backend != "oracledb":
        raise ValueError("live probing requires HARNESS_ORACLE_BACKEND=oracledb")
    refs = {}
    for target in data["targets"]:
        refs[target["secretReference"]] = target["passwordFile"]
        if target.get("walletSecretReference"):
            refs[target["walletSecretReference"]] = target["walletPasswordFile"]
    _validate_secret_files(refs, Path(settings.secret_dir))
    engine = build_engine(settings)
    initialize_schema(engine)
    factory = build_session_factory(engine)
    results = []
    try:
        with factory() as db:
            # Check every row before writing any row.
            secrets = {row.name: row for row in db.scalars(select(SecretReference)).all()}
            profiles = {row.name: row for row in db.scalars(select(ConnectionProfile)).all()}
            users = {row.subject: row for row in db.scalars(select(User)).all()}
            for name, locator in refs.items():
                if name in secrets and (secrets[name].provider, secrets[name].locator) != (
                    "file",
                    locator,
                ):
                    raise ValueError(f"{name}: stored secret reference differs")
            for spec in data["targets"]:
                old = profiles.get(spec["name"])
                if old is None:
                    continue
                expected = (
                    spec.get("enabled", True),
                    spec["environment"],
                    spec["host"],
                    spec["port"],
                    spec["serviceName"],
                    spec["username"],
                    spec["defaultSchema"],
                    spec.get("protocol", "tcp"),
                    spec.get("walletDir", ""),
                    spec["worksheetsEnabled"],
                    spec["mutatingRunbooksEnabled"],
                    spec["secretReference"],
                    spec.get("walletSecretReference", ""),
                )
                wallet = (
                    db.get(SecretReference, old.wallet_secret_reference_id)
                    if old.wallet_secret_reference_id
                    else None
                )
                stored = (
                    old.enabled,
                    old.environment,
                    old.host,
                    old.port,
                    old.service_name,
                    old.username,
                    old.default_schema,
                    old.protocol,
                    old.wallet_dir,
                    old.worksheets_enabled,
                    old.mutating_runbooks_enabled,
                    old.secret.name,
                    wallet.name if wallet else "",
                )
                if stored != expected:
                    raise ValueError(
                        f"{spec['name']}: stored profile differs; use a fresh metadata store"
                    )
            for name, locator in refs.items():
                if name not in secrets:
                    secrets[name] = SecretReference(name=name, provider="file", locator=locator)
                    db.add(secrets[name])
            for spec in data["users"]:
                if spec["subject"] not in users:
                    users[spec["subject"]] = User(
                        subject=spec["subject"],
                        display_name=spec.get("displayName", ""),
                        roles=spec["roles"],
                    )
                    db.add(users[spec["subject"]])
                else:
                    users[spec["subject"]].display_name = spec.get("displayName", "")
                    users[spec["subject"]].roles = spec["roles"]
            db.flush()
            for spec in data["targets"]:
                if spec["name"] not in profiles:
                    profiles[spec["name"]] = ConnectionProfile(
                        name=spec["name"],
                        environment=spec["environment"],
                        host=spec["host"],
                        port=spec["port"],
                        service_name=spec["serviceName"],
                        username=spec["username"],
                        default_schema=spec["defaultSchema"],
                        protocol=spec.get("protocol", "tcp"),
                        wallet_dir=spec.get("walletDir", ""),
                        secret_reference_id=secrets[spec["secretReference"]].id,
                        wallet_secret_reference_id=secrets[spec["walletSecretReference"]].id
                        if spec.get("walletSecretReference")
                        else None,
                        enabled=spec.get("enabled", True),
                        worksheets_enabled=spec["worksheetsEnabled"],
                        mutating_runbooks_enabled=spec["mutatingRunbooksEnabled"],
                        notes=spec.get("notes", ""),
                    )
                    db.add(profiles[spec["name"]])
            db.flush()
            for user_spec in data["users"]:
                user = users[user_spec["subject"]]
                for target_spec in data["targets"]:
                    target = profiles[target_spec["name"]]
                    permissions = ["read"]
                    if not is_production(target) and set(user.roles) & {
                        "developer",
                        "dba",
                    }:
                        permissions += ["worksheet", "compile", "runbook"]
                    row = db.scalars(
                        select(UserTargetGrant).where(
                            UserTargetGrant.user_id == user.id,
                            UserTargetGrant.profile_id == target.id,
                        )
                    ).first()
                    if row is None:
                        row = UserTargetGrant(
                            user_id=user.id, profile_id=target.id, permissions=permissions
                        )
                        db.add(row)
                    else:
                        row.permissions = permissions
            db.commit()
        if probe:
            execution = ExecutionService(settings, factory)
            try:
                with factory() as db:
                    for spec in data["targets"]:
                        profile = db.scalars(
                            select(ConnectionProfile).where(ConnectionProfile.name == spec["name"])
                        ).one()
                        if not profile.enabled:
                            results.append(
                                {
                                    "name": profile.name,
                                    "connected": False,
                                    "error": "target_disabled",
                                    "skipped": True,
                                }
                            )
                            continue
                        try:
                            identity, caps = execution.probe_target(db, profile, None)
                            connection = execution._backend.connect(
                                execution.connection_spec(profile, None)
                            )
                            try:
                                charset_result = connection.execute(
                                    "SELECT value FROM nls_database_parameters "
                                    "WHERE parameter = 'NLS_CHARACTERSET'",
                                    {},
                                    StatementKind.QUERY,
                                    ExecutionLimits(maxRows=1, deadlineSeconds=10),
                                )
                                charset = (
                                    charset_result.result_set.rows[0][0]
                                    if charset_result.result_set and charset_result.result_set.rows
                                    else "unavailable"
                                )
                            finally:
                                connection.close()
                            results.append(
                                {
                                    "name": profile.name,
                                    "connected": True,
                                    "identity": identity.model_dump(by_alias=True, mode="json"),
                                    "characterSet": charset,
                                    "capabilities": {cap.capability: cap.available for cap in caps},
                                }
                            )
                        except Exception:
                            results.append(
                                {
                                    "name": profile.name,
                                    "connected": False,
                                    "error": (
                                        "connection or capability probe failed; "
                                        "inspect server logs without credentials"
                                    ),
                                }
                            )
            finally:
                execution.shutdown()
        return results
    finally:
        engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=Path("databases/targets.json"))
    parser.add_argument("--probe", action=argparse.BooleanOptionalAction, default=True)
    args = parser.parse_args()
    try:
        results = bootstrap(
            json.loads(args.manifest.read_text(encoding="utf-8")), get_settings(), probe=args.probe
        )
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"bootstrap refused: {exc}", file=sys.stderr)
        return 2
    for row in results:
        if row["connected"]:
            ident = row["identity"]
            print(
                f"{row['name']}: connected | version={ident.get('version', '?')} "
                f"| cdb={ident.get('isCdb', '?')} "
                f"| container={ident.get('containerName', '?')} "
                f"| charset={row['characterSet']} | capabilities={row['capabilities']}"
            )
        else:
            print(f"{row['name']}: {row['error']}")
    return 1 if any(not row["connected"] and not row.get("skipped") for row in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
