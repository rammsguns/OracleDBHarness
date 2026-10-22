"""The production examples must be loadable and preserve the fixed decisions.

`deploy/production.env.example` is the production environment template and
`databases.example/targets.production.json.example` is the production target
manifest. Both are placeholders: no real host, wallet, username or secret may
appear, and every setting name must be one the application actually reads.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness_api.config import Settings
from scripts.bootstrap_targets import validate_manifest

REPO = Path(__file__).resolve().parents[2]
ENV_EXAMPLE = REPO / "deploy" / "production.env.example"
MANIFEST_EXAMPLE = REPO / "databases.example" / "targets.production.json.example"


def _env_keys(path: Path) -> list[str]:
    keys: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped:
            continue
        key = stripped.split("=", 1)[0].strip()
        if key.startswith("HARNESS_"):
            keys.append(key)
    return keys


@pytest.fixture
def clean_harness_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in list(__import__("os").environ):
        if name.startswith("HARNESS_"):
            monkeypatch.delenv(name, raising=False)


def test_production_env_example_loads(clean_harness_env: None) -> None:
    settings = Settings(_env_file=str(ENV_EXAMPLE))
    assert settings.env == "production"
    assert settings.copilot_enabled is False
    assert settings.kiwi_enabled is False
    assert settings.auth_mode == "oidc"
    assert settings.oracle_backend == "oracledb"
    assert settings.oracle_driver_mode == "thin"


def test_production_env_example_has_no_wildcard_origins(clean_harness_env: None) -> None:
    settings = Settings(_env_file=str(ENV_EXAMPLE))
    origins = settings.cors_origin_list
    assert origins, "the production example must set an exact origin allowlist"
    assert "*" not in origins
    for origin in origins:
        assert origin.startswith("https://"), origin


def test_production_env_example_keys_are_known_settings() -> None:
    known = set(Settings.model_fields)
    for key in _env_keys(ENV_EXAMPLE):
        field = key[len("HARNESS_") :].lower()
        assert field in known, f"{key} is not a known setting"


def test_production_env_example_uses_documentation_endpoints(clean_harness_env: None) -> None:
    settings = Settings(_env_file=str(ENV_EXAMPLE))
    assert settings.endpoint_allowlist, "production must restrict the endpoint allowlist"
    for endpoint in settings.endpoint_allowlist:
        host = endpoint.rsplit(":", 1)[0]
        assert host.startswith("203.0.113.") or host.endswith(
            (".example.internal", ".example.com", ".example.org", ".example.net")
        ), endpoint


def test_production_manifest_is_observation_only() -> None:
    data = json.loads(MANIFEST_EXAMPLE.read_text(encoding="utf-8"))
    validated = validate_manifest(data)
    assert validated["authMode"] == "oidc"
    for target in validated["targets"]:
        assert target["environment"] == "production"
        assert target["worksheetsEnabled"] is False
        assert target["mutatingRunbooksEnabled"] is False
