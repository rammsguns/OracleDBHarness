"""Application state and FastAPI dependencies."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, Request
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from harness_api.config import Settings
from harness_api.copilot import CopilotService
from harness_api.execution import ExecutionService
from harness_api.models import AppRole
from harness_api.runbooks import RunbookService
from harness_api.security import Authenticator, Principal
from harness_worker.errors import AuthorizationError


@dataclass
class AppState:
    settings: Settings
    engine: Engine
    session_factory: sessionmaker[Session]
    authenticator: Authenticator
    execution: ExecutionService
    runbooks: RunbookService
    copilot: CopilotService
    metadata_schema_version: str


def get_state(request: Request) -> AppState:
    return request.app.state.harness


def get_settings_dep(request: Request) -> Settings:
    return get_state(request).settings


def get_db(request: Request) -> Iterator[Session]:
    state = get_state(request)
    session = state.session_factory()
    try:
        yield session
    finally:
        session.close()


def get_principal(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    authorization: Annotated[str | None, Header()] = None,
) -> Principal:
    state = get_state(request)
    principal = state.authenticator.authenticate(db, authorization)
    db.commit()
    return principal


CurrentUser = Annotated[Principal, Depends(get_principal)]
Db = Annotated[Session, Depends(get_db)]
State = Annotated[AppState, Depends(get_state)]


def require_administrator(principal: CurrentUser) -> Principal:
    if not principal.has_role(AppRole.ADMINISTRATOR):
        raise AuthorizationError("This endpoint is restricted to administrators.")
    return principal


Administrator = Annotated[Principal, Depends(require_administrator)]
