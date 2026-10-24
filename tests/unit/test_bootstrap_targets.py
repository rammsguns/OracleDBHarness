"""Real target onboarding must refuse unsafe or ambiguous metadata."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from harness_api.config import Settings
from harness_api.copilot.standards import STANDARD_KEYS, load_standards, parse_standards
from harness_api.models import ConnectionProfile, SecretReference, UserTargetGrant
from scripts.bootstrap_targets import bootstrap, validate_manifest


def test_example_team_standards_load_and_render() -> None:
    path = Path(__file__).resolve().parents[2] / "oracle" / "standards.example.json"
    parsed = parse_standards(path.read_text(encoding="utf-8"), source=str(path))
    loaded = load_standards(str(path))
    assert loaded == parsed
    assert parsed.keys == list(STANDARD_KEYS)
    rendered = parsed.render()
    for key in STANDARD_KEYS:
        assert f"- {key}: " in rendered
    assert "parameter: p_" in rendered
    assert "app_log.error" in rendered
    assert "- bulkCollectLimit: 500" in rendered
    assert "re-raise" in rendered
    assert "-- Transaction ownership:" in rendered


@pytest.fixture
def manifest() -> dict:
    return {
        "version": 1,
        "authMode": "dev",
        "users": [
            {"subject": "dev@example.internal", "displayName": "Dev", "roles": ["developer"]},
            {"subject": "viewer@example.internal", "displayName": "Viewer", "roles": ["viewer"]},
        ],
        "targets": [
            {
                "name": "dev",
                "host": "localhost",
                "port": 1521,
                "serviceName": "ORCL",
                "username": "HARNESS_APP",
                "defaultSchema": "HARNESS_APP",
                "environment": "development",
                "secretReference": "app",
                "passwordFile": "app.pw",
                "worksheetsEnabled": True,
                "mutatingRunbooksEnabled": True,
            },
            {
                "name": "lab",
                "host": "localhost",
                "port": 1521,
                "serviceName": "ORCL",
                "username": "HARNESS_LAB",
                "defaultSchema": "SH",
                "environment": "production",
                "secretReference": "lab",
                "passwordFile": "lab.pw",
                "worksheetsEnabled": False,
                "mutatingRunbooksEnabled": False,
            },
        ],
    }


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    secrets = tmp_path / "secrets"
    secrets.mkdir(exist_ok=True)
    for name in ("app.pw", "lab.pw", "wallet.pw"):
        path = secrets / name
        path.write_text("dummy", encoding="utf-8")
        path.chmod(0o600)
    return Settings(
        _env_file=None,
        metadata_url=f"sqlite+pysqlite:///{tmp_path / 'meta.sqlite3'}",
        secret_dir=str(secrets),
        auth_mode="dev",
        oracle_backend="fake",
    )


def test_invalid_and_unsafe_manifest(manifest: dict) -> None:
    bad = copy.deepcopy(manifest)
    bad["targets"][1]["worksheetsEnabled"] = True
    with pytest.raises(ValueError, match="observation only"):
        validate_manifest(bad)
    bad = copy.deepcopy(manifest)
    bad["targets"][1]["passwordFile"] = "../escape.pw"
    with pytest.raises(ValueError, match="invalid secret"):
        validate_manifest(bad)
    bad = copy.deepcopy(manifest)
    bad["authMode"] = "oidc"
    with pytest.raises(ValueError, match="OIDC users"):
        validate_manifest(bad)


def test_secret_permissions_and_auth_mode(manifest: dict, settings: Settings) -> None:
    path = Path(settings.secret_dir) / "app.pw"
    path.chmod(0o644)
    with pytest.raises(ValueError, match="chmod 600"):
        bootstrap(manifest, settings, probe=False)
    path.chmod(0o600)
    with pytest.raises(ValueError, match="authMode"):
        bootstrap(
            {
                **manifest,
                "authMode": "oidc",
                "users": [
                    {"subject": "provider-sub", "oidcSub": "provider-sub", "roles": ["viewer"]}
                ],
            },
            settings,
            probe=False,
        )
    with pytest.raises(ValueError, match="HARNESS_ORACLE_BACKEND"):
        bootstrap(manifest, settings)


def test_idempotent_grants_and_drift(manifest: dict, settings: Settings) -> None:
    assert bootstrap(manifest, settings, probe=False) == []
    assert bootstrap(manifest, settings, probe=False) == []
    engine = create_engine(settings.metadata_url)
    with Session(engine) as db:
        assert len(db.scalars(select(ConnectionProfile)).all()) == 2
        assert len(db.scalars(select(SecretReference)).all()) == 2
        grants = db.scalars(select(UserTargetGrant)).all()
        assert len(grants) == 4
        for grant in grants:
            profile = db.get(ConnectionProfile, grant.profile_id)
            if profile.environment == "production":
                assert grant.permissions == ["read"]
            if grant.user.subject == "viewer@example.internal":
                assert grant.permissions == ["read"]
    changed = copy.deepcopy(manifest)
    changed["targets"][0]["host"] = "other.example"
    with pytest.raises(ValueError, match="stored profile differs"):
        bootstrap(changed, settings, probe=False)
    changed = copy.deepcopy(manifest)
    changed["targets"][0]["passwordFile"] = "different.pw"
    (Path(settings.secret_dir) / "different.pw").write_text("dummy", encoding="utf-8")
    (Path(settings.secret_dir) / "different.pw").chmod(0o600)
    with pytest.raises(ValueError, match="stored secret reference differs"):
        bootstrap(changed, settings, probe=False)


def test_wallet_reference_attached(manifest: dict, settings: Settings) -> None:
    target = manifest["targets"][1]
    target.update(
        protocol="tcps",
        walletDir="/tmp/wallet",
        walletSecretReference="wallet",
        walletPasswordFile="wallet.pw",
    )
    bootstrap(manifest, settings, probe=False)
    engine = create_engine(settings.metadata_url)
    with Session(engine) as db:
        profile = db.scalars(select(ConnectionProfile).where(ConnectionProfile.name == "lab")).one()
        wallet = db.get(SecretReference, profile.wallet_secret_reference_id)
        assert wallet is not None and wallet.locator == "wallet.pw"


def test_operator_role_change_reconciles_grants(manifest: dict, settings: Settings) -> None:
    bootstrap(manifest, settings, probe=False)
    manifest["users"][0]["roles"] = ["viewer"]
    bootstrap(manifest, settings, probe=False)
    engine = create_engine(settings.metadata_url)
    with Session(engine) as db:
        assert all(grant.permissions == ["read"] for grant in db.scalars(select(UserTargetGrant)))


def test_disabled_manifest_flag_is_persisted(manifest, settings):
    manifest["targets"][0]["enabled"] = False
    assert bootstrap(manifest, settings, probe=False) == []
    assert bootstrap(manifest, settings, probe=False) == []
    with Session(create_engine(settings.metadata_url)) as db:
        assert (
            db.scalar(select(ConnectionProfile).where(ConnectionProfile.name == "dev")).enabled
            is False
        )
        assert (
            db.scalar(select(ConnectionProfile).where(ConnectionProfile.name == "lab")).enabled
            is True
        )
    manifest["targets"][0]["enabled"] = "false"
    with pytest.raises(ValueError, match="enabled must be boolean"):
        validate_manifest(manifest)


def test_disabled_manifest_does_not_probe(manifest, settings, monkeypatch):
    from unittest.mock import Mock

    for target in manifest["targets"]:
        target["enabled"] = False
    service = Mock()
    service._backend.connect.side_effect = AssertionError("disabled target connected")
    monkeypatch.setattr("scripts.bootstrap_targets.ExecutionService", Mock(return_value=service))
    results = bootstrap(manifest, settings.model_copy(update={"oracle_backend": "oracledb"}))
    assert len(results) == 2
    assert all(row["skipped"] and row["error"] == "target_disabled" for row in results)
    service.probe_target.assert_not_called()
    service._backend.connect.assert_not_called()
