"""Error taxonomy shared by the execution engine and the API.

Every failure surfaced to a user carries a stable ``code`` so the console and IDE
adapters can react without parsing message text, and a ``retryable`` flag that is
deliberately conservative: writes are never marked retryable, because a network
timeout does not tell us whether Oracle applied the statement.
"""

from __future__ import annotations


class HarnessError(Exception):
    code = "harness_error"
    http_status = 500
    retryable = False

    def __init__(self, message: str, *, detail: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail or {}

    def as_dict(self) -> dict:
        return {
            "code": self.code,
            "message": self.message,
            "detail": self.detail,
            "retryable": self.retryable,
        }


class ConfigurationError(HarnessError):
    code = "configuration_error"
    http_status = 500


class AuthenticationError(HarnessError):
    code = "authentication_required"
    http_status = 401


class AuthorizationError(HarnessError):
    code = "not_authorized"
    http_status = 403


class NotFoundError(HarnessError):
    code = "not_found"
    http_status = 404


class ValidationError(HarnessError):
    code = "invalid_request"
    http_status = 400


class PolicyError(HarnessError):
    """The request is well formed but the policy layer refuses it."""

    code = "policy_refused"
    http_status = 403


class CapabilityError(HarnessError):
    """A required Oracle privilege or view is not available on this target."""

    code = "capability_unavailable"
    http_status = 409


class ConnectionFailedError(HarnessError):
    code = "connection_failed"
    http_status = 502


class SessionExpiredError(HarnessError):
    code = "session_expired"
    http_status = 409


class SessionBusyError(HarnessError):
    """Operations inside one worksheet session are serialized."""

    code = "session_busy"
    http_status = 409


class LimitExceededError(HarnessError):
    code = "limit_exceeded"
    http_status = 413


class TimeoutError_(HarnessError):
    code = "execution_timeout"
    http_status = 504


class CancelledError_(HarnessError):
    code = "execution_cancelled"
    http_status = 499


class OutcomeUnknownError(HarnessError):
    """The connection broke while a statement was in flight.

    The statement may or may not have been applied inside Oracle. Never retry
    automatically and never report this as either success or a clean failure.
    """

    code = "outcome_unknown"
    http_status = 502


class OracleError(HarnessError):
    """An error raised by Oracle itself, carrying its ORA/PLS code."""

    code = "oracle_error"
    http_status = 400

    def __init__(self, message: str, *, oracle_code: str = "", detail: dict | None = None) -> None:
        super().__init__(message, detail=detail)
        self.oracle_code = oracle_code

    def as_dict(self) -> dict:
        payload = super().as_dict()
        payload["oracleCode"] = self.oracle_code
        return payload


class ProviderError(HarnessError):
    """The configured AI provider failed. Database workflows must stay usable."""

    code = "provider_failure"
    http_status = 502
