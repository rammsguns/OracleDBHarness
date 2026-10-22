"""Request context shared with synchronous policy and audit calls."""

from contextvars import ContextVar

from starlette.requests import Request

audit_request: ContextVar[Request | None] = ContextVar("audit_request", default=None)
