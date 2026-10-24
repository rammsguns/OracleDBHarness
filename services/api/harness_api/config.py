"""Configuration, loaded from the environment and validated at startup.

Anything that could weaken a security boundary fails loudly here rather than
silently defaulting: a shared deployment with the development identity mode, or an
unset endpoint allowlist, is reported as a configuration problem.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator, model_validator
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
    # The console signs in as a public client (authorization code with PKCE). It has
    # no client secret: anything shipped to a browser is readable by its user.
    oidc_client_id: str = ""
    oidc_scopes: str = "openid profile email"
    # Leave both empty to take them from the issuer's discovery document.
    oidc_authorization_endpoint: str = ""
    oidc_token_endpoint: str = ""
    # Some providers (Auth0, for one) only issue a JWT access token for an API when
    # the authorization request names its audience. Others reject the parameter.
    oidc_request_audience: bool = False
    # This is the placeholder value, not a credential: startup_warnings() reports it.
    dev_token_secret: str = "change-me-in-any-shared-environment"  # noqa: S105

    oracle_backend: str = "fake"
    oracle_driver_mode: str = "thin"
    oracle_client_lib_dir: str = ""
    oracle_fake_data_dir: str = ""

    # A directory to read secrets from, not a secret.
    secret_dir: str = "./deploy/secrets"  # noqa: S105

    schema_panel_cache_seconds: float = Field(default=60, ge=0)

    max_rows: int = 1000
    max_response_bytes: int = 10 * 1024 * 1024
    statement_timeout_seconds: float = 30.0
    worksheet_idle_seconds: float = 300.0
    max_dbms_output_bytes: int = 64 * 1024
    lob_preview_bytes: int = 8 * 1024
    worker_processes: int = 4

    allowed_endpoints: str = ""

    copilot_enabled: bool = False
    # "local" (a model on this machine), "fake" (fixture answers) or "anthropic".
    copilot_provider: str = "fake"
    copilot_model: str = "claude-opus-5"
    # Prompts stay on this host: only "local" and "fake" are allowed, and the local
    # endpoint has to be a loopback address. Turning this off is a data-sharing decision.
    copilot_local_only: bool = True
    # The local server's OpenAI-style API root, for example http://127.0.0.1:8080/v1.
    copilot_base_url: str = ""
    copilot_api_key_ref: str = ""
    copilot_max_context_bytes: int = 128 * 1024
    # Thinking counts against this too, so it bounds the whole billed output of a call.
    copilot_max_output_tokens: int = 8000
    copilot_request_timeout_seconds: float = 600.0
    copilot_provider_max_retries: int = 2
    copilot_user_daily_requests: int = 100
    copilot_log_prompts: bool = False

    # Kiwi's read-only catalog lookups (ADR-0010, KIWI_PLAN.md K-3). Off by default:
    # turning it on widens what leaves the harness, see docs/copilot.md.
    kiwi_enabled: bool = False
    # One bounded no-tools answer retry; default awaits the user decision after K4.
    kiwi_answer_retry: bool = False
    # Per request. A step is one model turn; tokens are prompt plus completion summed
    # over every turn; bytes are what tool results add to the context.
    kiwi_max_steps: int = 8
    kiwi_max_tool_calls: int = 12
    kiwi_max_tokens: int = 200_000
    kiwi_max_wall_seconds: float = 180.0
    kiwi_max_rows_per_tool: int = 200
    kiwi_max_result_bytes: int = 16 * 1024
    kiwi_max_tool_bytes: int = 96 * 1024
    # Answers the harness may send back once for revision when its claim checks find a
    # statement the lookups or Oracle's semantics contradict (claim_checks.py). The
    # revision is one more model turn of the same request, with no tools: it takes a step
    # and counts its tokens, and only runs if one of each is left. It waits at most the
    # request's remaining wall time and at most kiwi_revision_max_wall_seconds, which can
    # narrow that, never extend it (round 18, C21). 0 turns revision off; the findings
    # are still reported.
    kiwi_max_revisions: int = 1
    kiwi_revision_max_wall_seconds: float = 120.0
    # Package and process explanations (K-6) page through source and summarise it in
    # several model calls, so they get their own, larger per-request budget.
    kiwi_explain_max_tool_calls: int = 40
    kiwi_explain_max_tool_bytes: int = 1024 * 1024
    kiwi_explain_max_model_calls: int = 16
    kiwi_explain_max_source_lines: int = 6000
    kiwi_explain_max_tokens: int = 400_000
    # Several summarising calls and a combining call: more than one Kiwi turn's time.
    kiwi_explain_max_wall_seconds: float = 420.0
    kiwi_explain_page_lines: int = 150
    # Optional JSON file of team PL/SQL standards (K-5), given to Kiwi in its system
    # prompt. See harness_api/copilot/standards.py for the accepted keys.
    kiwi_standards_file: str = ""
    # Evaluation only (K-7 remediation, step 1): also stream what each lookup returned to
    # the model, bounded and without rows from application queries, so a reviewer can
    # check the answer against it. Off by default; production events stay as they were.
    kiwi_capture_evidence: bool = False
    kiwi_evidence_max_bytes: int = 24 * 1024

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

    @model_validator(mode="after")
    def _copilot_stays_local(self) -> Settings:
        if self.copilot_provider not in ("fake", "local", "anthropic"):
            raise ValueError("HARNESS_COPILOT_PROVIDER must be 'local', 'fake' or 'anthropic'.")
        if self.copilot_local_only and self.copilot_provider not in ("fake", "local"):
            raise ValueError(
                f"HARNESS_COPILOT_PROVIDER is {self.copilot_provider!r}, which sends prompts "
                "off this host, and HARNESS_COPILOT_LOCAL_ONLY is on. Use 'local', or turn "
                "HARNESS_COPILOT_LOCAL_ONLY off deliberately."
            )
        return self

    def check_copilot_endpoint(self) -> None:
        """Refuse to start with a local endpoint that is not on this machine.

        The provider checks again when it is built; this makes a bad address a startup
        failure rather than the first user's error.
        """

        if self.copilot_enabled and self.copilot_provider == "local":
            from harness_api.copilot.local import LoopbackEndpoint

            LoopbackEndpoint.resolve(self.copilot_base_url)

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
        if self.auth_mode == "oidc" and not self.oidc_client_id:
            warnings.append(
                "HARNESS_OIDC_CLIENT_ID is empty, so the console cannot sign anyone in. "
                "Register the console with the identity provider as a public client."
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
        if not self.copilot_local_only:
            warnings.append(
                "HARNESS_COPILOT_LOCAL_ONLY is off. With a cloud provider configured, prompts "
                "-- database source, plans, errors -- leave this host."
            )
        if self.copilot_log_prompts:
            warnings.append(
                "HARNESS_COPILOT_LOG_PROMPTS is on. Prompt text can contain database "
                "source and user-supplied errors; keep it off outside debugging."
            )
        if self.kiwi_enabled and self.copilot_enabled:
            warnings.append(
                "HARNESS_KIWI_ENABLED is on. Kiwi may run reviewed read-only catalog "
                "lookups as the requesting user, and what it fetches is sent to the "
                "model provider. The data-sharing approval has to cover that."
            )
        return warnings


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()
