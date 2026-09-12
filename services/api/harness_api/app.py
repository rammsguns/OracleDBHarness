"""Application factory.

The API is a modular monolith: one process serves HTTP and owns the execution
service, which in turn owns the worksheet connections and a bounded pool of worker
threads. Splitting the execution service onto its own host is a deployment change,
not a rewrite, because everything already goes through its interface.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from harness_api import __version__
from harness_api.config import Settings, get_settings
from harness_api.copilot import CopilotService
from harness_api.db import build_engine, build_session_factory, initialize_schema
from harness_api.deps import AppState
from harness_api.execution import ExecutionService
from harness_api.recovery import (
    claim_store,
    new_runtime_id,
    reconcile_interrupted_work,
)
from harness_api.routers import (
    admin,
    copilot,
    history,
    runbooks,
    system,
    targets,
    tuning,
    worksheet,
)
from harness_api.runbooks import RunbookService, register_definitions
from harness_api.security import Authenticator
from harness_worker.errors import HarnessError

log = logging.getLogger("harness.api")

DESCRIPTION = """\
OracleDBHarness is the shared execution, policy and copilot layer for Oracle work.

Every operation carries an operation ID, an actor, a target, typed parameters,
capability requirements, a risk class and limits, and produces an execution record
with a structured result and verification evidence.

Notes that apply to the whole API:

* Free-form SQL runs only on targets where an administrator enabled worksheets. A
  keyword filter is not a read-only boundary, so the Oracle account is the boundary.
* Production targets are observation only in this release.
* Applying a copilot proposal changes an editor buffer. It never runs, compiles or
  commits anything.
"""


def build_state(settings: Settings) -> AppState:
    engine = build_engine(settings)
    session_factory = build_session_factory(engine)
    version = initialize_schema(engine)

    # Claim the store and reconcile before the execution service exists, so no request
    # can be dispatched against a store whose interrupted work has not been resolved.
    # The claim comes first and commits on its own: a process that has been superseded
    # has to learn that even if the reconciliation that follows fails.
    runtime_id = new_runtime_id()
    reconciliation = claim_store(session_factory, runtime_id)
    reconciliation = reconcile_interrupted_work(session_factory, reconciliation)

    execution = ExecutionService(settings, session_factory, runtime_id=runtime_id)
    with session_factory() as db:
        register_definitions(db, execution)
    return AppState(
        settings=settings,
        engine=engine,
        session_factory=session_factory,
        authenticator=Authenticator(settings),
        execution=execution,
        runbooks=RunbookService(execution),
        copilot=CopilotService(settings, session_factory),
        metadata_schema_version=version,
        runtime_id=runtime_id,
        reconciliation=reconciliation,
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        state = build_state(settings)
        app.state.harness = state
        for warning in settings.startup_warnings():
            log.warning("configuration: %s", warning)
        try:
            yield
        finally:
            state.execution.shutdown()

    app = FastAPI(
        title="OracleDBHarness API",
        version=__version__,
        description=DESCRIPTION,
        lifespan=lifespan,
    )

    if settings.cors_origin_list:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origin_list,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    @app.exception_handler(HarnessError)
    async def harness_error_handler(request: Request, exc: HarnessError) -> JSONResponse:
        # Every failure the caller sees carries a stable code, so the console and IDE
        # adapters can react without parsing message text.
        status = exc.http_status
        if status == 499:  # a client-cancel code nginx understands but HTTP does not
            status = 408
        return JSONResponse(status_code=status, content={"error": exc.as_dict()})

    for module in (system, admin, targets, worksheet, tuning, runbooks, copilot, history):
        app.include_router(module.router)
    return app


def main() -> None:  # pragma: no cover - entry point
    import uvicorn

    uvicorn.run(
        "harness_api.app:create_app",
        factory=True,
        host="0.0.0.0",  # noqa: S104 - bound inside a container in the pilot deployment
        port=8000,
        reload=False,
    )


if __name__ == "__main__":  # pragma: no cover
    main()
