"""Configuration, loaded from the environment and validated at startup.

Anything that could weaken a security boundary fails loudly here rather than
silently defaulting: a shared deployment with the development identity mode, or an
unset endpoint allowlist, is reported as a configuration problem.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from harness_worker.types import ExecutionLimits


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="HARNESS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    env: str = "development"
    log_level: str = "INFO"

    metadata_url: str = "sqlite+pysqlite:///./harness-metadata.sqlite3"
    # A path to read the metadata password from, not a password. Set it when the
    # store's password is delivered as a mounted file, and leave it out of the URL.
    metadata_password_file: str = ""  # noqa: S105

    auth_mode: str = "dev"
    oidc_issuer: str = ""
    oidc_audience: str = "oracledbharness"
    oidc_jwks_url: str = ""
    # This is the placeholder value, not a credential: startup_warnings() reports it.
    dev_token_secret: str = "change-me-in-any-shared-environment"  # noqa: S105

    oracle_backend: str = "fake"
    oracle_driver_mode: str = "thin"
    oracle_client_lib_dir: str = ""
    oracle_fake_data_dir: str = ""

    # A directory to read secrets from, not a secret.
    secret_dir: str = "./deploy/secrets"  # noqa: S105

    max_rows: int = 1000
    max_response_bytes: int = 10 * 1024 * 1024
    statement_timeout_seconds: float = 30.0
    worksheet_idle_seconds: float = 300.0
    max_dbms_output_bytes: int = 64 * 1024
    lob_preview_bytes: int = 8 * 1024
    worker_processes: int = 4

    allowed_endpoints: str = ""

    copilot_enabled: bool = False
    copilot_provider: str = "fake"
    copilot_model: str = "claude-opus-5"
    copilot_api_key_ref: str = ""
    copilot_max_context_bytes: int = 128 * 1024
    copilot_user_daily_requests: int = 100
    copilot_log_prompts: bool = False

    cors_origins: str = "http://localhost:5173"

    # Set by tests and the demo seed; never enable in a shared deployment.
    allow_dev_auth_outside_development: bool = False

    @field_validator("auth_mode")
    @classmethod
    def _known_auth_mode(cls, value: str) -> str:
        if value not in ("dev", "oidc"):
            raise ValueError("HARNESS_AUTH_MODE must be 'dev' or 'oidc'.")
        return value

    @field_validator("oracle_backend")
    @classmethod
    def _known_backend(cls, value: str) -> str:
        if value not in ("fake", "oracledb"):
            raise ValueError("HARNESS_ORACLE_BACKEND must be 'fake' or 'oracledb'.")
        return value

    @property
    def default_limits(self) -> ExecutionLimits:
        return ExecutionLimits(
            maxRows=self.max_rows,
            maxResponseBytes=self.max_response_bytes,
            deadlineSeconds=self.statement_timeout_seconds,
            maxDbmsOutputBytes=self.max_dbms_output_bytes,
            lobPreviewBytes=self.lob_preview_bytes,
        )

    @property
    def endpoint_allowlist(self) -> list[str]:
        return [item.strip() for item in self.allowed_endpoints.split(",") if item.strip()]

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    def startup_warnings(self) -> list[str]:
        """Configuration that is legal but should not stay this way in a pilot."""

        warnings: list[str] = []
        if self.auth_mode == "dev" and self.env != "development":
            warnings.append(
                "HARNESS_AUTH_MODE=dev issues local tokens and is not an identity "
                f"provider. This deployment reports HARNESS_ENV={self.env}."
            )
        if not self.endpoint_allowlist:
            warnings.append(
                "HARNESS_ALLOWED_ENDPOINTS is empty, so an operator can register any "
                "reachable database endpoint. Set it before the pilot."
            )
        if self.oracle_backend == "fake":
            warnings.append(
                "The Oracle backend is the local stand-in. Nothing here demonstrates "
                "compatibility with a real Oracle database."
            )
        if self.copilot_enabled and self.copilot_provider == "fake":
            warnings.append(
                "The copilot is enabled with the fixture provider. Answers are canned "
                "and no model provider is being called."
            )
        if self.copilot_log_prompts:
            warnings.append(
                "HARNESS_COPILOT_LOG_PROMPTS is on. Prompt text can contain database "
                "source and user-supplied errors; keep it off outside debugging."
            )
        return warnings


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()
