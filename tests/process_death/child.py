"""The API process that gets killed.

Run as ``python -m tests.process_death.child <config.json>``. The configuration names
the settings to serve with, a directory to signal through, and optionally one *barrier*:
the point at which the process stops and waits to be killed. Without a barrier this is
simply the API, which is how the restarted process runs.

Barriers wrap real methods and then wait; they never raise and never replace a result.

``before_dispatch``
    The execution record has been committed as ``queued``; ``_mark_dispatched`` has not
    run and nothing has been sent to the database.
``statement_sent``
    The statement is about to be handed to the driver. The signal is written first, the
    statement then runs for real, and the thread waits after it returns so its answer is
    never recorded. Used with a statement the observer keeps blocked in the database.
``statement_returned``
    The driver call has returned - a read has fetched its rows, a write is applied and
    uncommitted - and the answer has not been recorded.
``commit_before_send``
    The session's commit intent is committed to the store; COMMIT has not been sent.
``commit_returned``
    COMMIT has returned from the database, so the transaction is durable; the answer has
    not been recorded.

Statement barriers only stop on a statement containing ``match``, so identity probes,
capability probes and catalog queries pass straight through.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import threading
from pathlib import Path
from typing import Any

BARRIERS = (
    "before_dispatch",
    "statement_sent",
    "statement_returned",
    "commit_before_send",
    "commit_returned",
)

READY_FILE = "ready.json"
BARRIER_FILE = "barrier.json"


def _write_atomically(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload), encoding="utf-8")
    os.replace(temporary, path)


class Barrier:
    def __init__(self, name: str, match: str, signal_dir: Path) -> None:
        if name not in BARRIERS:
            raise SystemExit(f"Unknown barrier {name!r}; expected one of {BARRIERS}.")
        self.name = name
        self.match = match
        self.signal_dir = signal_dir
        self._reached = threading.Event()

    def matches(self, statement: str) -> bool:
        return bool(self.match) and self.match in statement

    def hold(self, **detail: Any) -> None:
        """Say where this process stopped, then wait to be killed.

        Only the first arrival signals and waits. A second statement reaching the same
        point would be a test that drove the process further than it meant to, and the
        parent is already about to kill it.
        """

        if self._reached.is_set():
            return
        self._reached.set()
        _write_atomically(
            self.signal_dir / BARRIER_FILE,
            {"barrier": self.name, "pid": os.getpid(), **detail},
        )
        threading.Event().wait()


def install(barrier: Barrier) -> None:
    """Wrap the one method the barrier stops in. Called before the app is built."""

    from harness_api import execution as execution_module

    service_class = execution_module.ExecutionService

    if barrier.name == "before_dispatch":
        begin = service_class._begin_execution  # noqa: SLF001 - the point under test

        def begin_then_hold(self: Any, db: Any, **kwargs: Any) -> Any:
            record = begin(self, db, **kwargs)
            if barrier.matches(kwargs["statement"]):
                barrier.hold(backend=self.backend_name, executionId=record.id)
            return record

        service_class._begin_execution = begin_then_hold  # type: ignore[method-assign]
        return

    create_backend = execution_module.create_backend

    def create_wrapped_backend(*args: Any, **kwargs: Any) -> Any:
        backend = create_backend(*args, **kwargs)
        connect = backend.connect

        def connect_wrapped(spec: Any) -> Any:
            return _wrap_connection(connect(spec), barrier, backend.name)

        backend.connect = connect_wrapped  # type: ignore[method-assign]
        return backend

    execution_module.create_backend = create_wrapped_backend  # type: ignore[assignment]


def _wrap_connection(connection: Any, barrier: Barrier, backend_name: str) -> Any:
    execute = connection.execute
    commit = connection.commit

    if barrier.name in ("statement_sent", "statement_returned"):

        def execute_wrapped(statement: str, *args: Any, **kwargs: Any) -> Any:
            if not barrier.matches(statement):
                return execute(statement, *args, **kwargs)
            if barrier.name == "statement_sent":
                # Signal first: the parent needs to know the statement is on its way
                # while the database is still holding it.
                _write_atomically(
                    barrier.signal_dir / "sending.json",
                    {"barrier": barrier.name, "pid": os.getpid(), "backend": backend_name},
                )
            result = execute(statement, *args, **kwargs)
            barrier.hold(backend=backend_name)
            return result

        connection.execute = execute_wrapped

    elif barrier.name == "commit_before_send":

        def commit_held() -> None:
            barrier.hold(backend=backend_name)
            commit()

        connection.commit = commit_held

    elif barrier.name == "commit_returned":

        def commit_then_hold() -> None:
            commit()
            barrier.hold(backend=backend_name)

        connection.commit = commit_then_hold

    return connection


def main(argv: list[str]) -> None:
    if len(argv) != 2:
        raise SystemExit("usage: python -m tests.process_death.child <config.json>")
    config = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    signal_dir = Path(config["signalDir"])

    if config.get("barrier"):
        install(Barrier(config["barrier"], config.get("match", ""), signal_dir))

    # Imported after the barrier is installed, so the service is built with it.
    import uvicorn

    from harness_api.app import create_app
    from harness_api.config import Settings

    settings = Settings(**config["settings"])
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    _write_atomically(
        signal_dir / READY_FILE,
        {"pid": os.getpid(), "port": listener.getsockname()[1]},
    )
    server = uvicorn.Server(
        uvicorn.Config(create_app(settings), log_level="warning", lifespan="on")
    )
    server.run(sockets=[listener])


if __name__ == "__main__":
    main(sys.argv)
