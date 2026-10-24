"""Oracle keepalive configuration is validated before opening any connections."""

import pytest
from pydantic import ValidationError

from harness_api.config import Settings


def test_keepalive_defaults_to_one_minute() -> None:
    assert Settings(_env_file=None).oracle_expire_time_minutes == 1


@pytest.mark.parametrize("minutes", [0, 1, 7])
def test_keepalive_environment_override(monkeypatch, minutes: int) -> None:
    monkeypatch.setenv("HARNESS_ORACLE_EXPIRE_TIME_MINUTES", str(minutes))
    assert Settings(_env_file=None).oracle_expire_time_minutes == minutes


@pytest.mark.parametrize("value", ["-1", "1.5", "invalid"])
def test_invalid_keepalive_interval_is_rejected(monkeypatch, value: str) -> None:
    monkeypatch.setenv("HARNESS_ORACLE_EXPIRE_TIME_MINUTES", value)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)
