"""One execution service owns the metadata store, and that is enforced, not assumed.

The service owns the worksheet connections, so a second one on the same store would
reconcile work the first is still running: recording writes as uncertain that are about to
succeed, and statements as never dispatched that are about to be sent. Claiming the store
is what makes reconciliation safe to run at all, so the claim itself has to be correct
under concurrency and the loser of a race has to actually stop.

The claim is serialized on a single row rather than by reading the live runtimes first.
Reading first is not enough: under PostgreSQL's default READ COMMITTED isolation two
simultaneous startups cannot see each other's uncommitted runtime row, so each would find
no previous owner and both would go on serving. SQLite serializes writers and so cannot
show that bug, which is exactly why the PostgreSQL check below exists and is worth having
even though it only runs when a disposable database is configured.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine, create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from harness_api.db import build_session_factory, initialize_schema
from harness_api.models import Base, ExecutionRuntime, StoreOwner
from harness_api.recovery import (
    OWNER_ROW_ID,
    claim_store,
    heartbeat,
    new_runtime_id,
    owns_store,
    record_clean_stop,
)

POSTGRES_URL = os.environ.get("HARNESS_TEST_POSTGRES_URL", "")


@pytest.fixture
def store(tmp_path: Path) -> Iterator[sessionmaker[Session]]:
    engine: Engine = create_engine(f"sqlite+pysqlite:///{(tmp_path / 'store.sqlite3').as_posix()}")
    initialize_schema(engine)
    try:
        yield build_session_factory(engine)
    finally:
        engine.dispose()


def _owner(store: sessionmaker[Session]) -> str:
    with store() as db:
        row = db.get(StoreOwner, OWNER_ROW_ID)
        assert row is not None
        return row.runtime_id


def _live_runtimes(store: sessionmaker[Session]) -> list[str]:
    with store() as db:
        return list(
            db.scalars(
                select(ExecutionRuntime.id).where(
                    ExecutionRuntime.superseded_by.is_(None),
                    ExecutionRuntime.stopped_at.is_(None),
                )
            ).all()
        )


def test_a_new_store_has_an_unclaimed_owner_row(store: sessionmaker[Session]) -> None:
    """The row exists before anyone claims it, because claiming locks it.

    A claim that had to create the row would have nothing to serialize on for the one case
    that matters: the first two processes to reach a fresh store.
    """

    assert _owner(store) == ""


def test_claiming_records_the_owner(store: sessionmaker[Session]) -> None:
    runtime = new_runtime_id()
    claim_store(store, runtime)

    assert _owner(store) == runtime
    assert owns_store(store, runtime) is True


def test_a_later_claim_takes_the_store_from_the_earlier_one(
    store: sessionmaker[Session],
) -> None:
    first = new_runtime_id()
    claim_store(store, first)
    second = new_runtime_id()

    report = claim_store(store, second)

    assert report.superseded_runtimes == [first]
    assert _owner(store) == second
    assert owns_store(store, first) is False
    assert owns_store(store, second) is True
    assert _live_runtimes(store) == [second]


def test_exactly_one_of_many_concurrent_claims_owns_the_store(
    store: sessionmaker[Session],
) -> None:
    """Whatever the interleaving, the store ends with one owner and one live runtime.

    On SQLite the writers are serialized by the engine, so this is a check that the claim
    is correct when ordered rather than a reproduction of the READ COMMITTED race. It still
    earns its place: it would fail if claiming left more than one runtime unsuperseded for
    any ordering at all.
    """

    runtimes = [new_runtime_id() for _ in range(6)]
    ready = threading.Barrier(len(runtimes))
    failures: list[BaseException] = []

    def claim(runtime_id: str) -> None:
        try:
            ready.wait(10.0)
            claim_store(store, runtime_id)
        except BaseException as exc:  # noqa: BLE001 - reported below, not swallowed
            failures.append(exc)

    threads = [threading.Thread(target=claim, args=(runtime,)) for runtime in runtimes]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30.0)

    assert not failures, f"a claim raised: {failures[0]!r}"
    live = _live_runtimes(store)
    assert len(live) == 1, f"{len(live)} runtimes think they own the store: {live}"
    assert live == [_owner(store)], "the owner row and the runtime rows disagree"
    # And the one that owns it is the only one that will dispatch.
    owning = [runtime for runtime in runtimes if owns_store(store, runtime)]
    assert owning == live


def test_a_superseded_runtime_stops_owning_and_stops_heartbeating(
    store: sessionmaker[Session],
) -> None:
    first = new_runtime_id()
    claim_store(store, first)
    assert heartbeat(store, first) is True

    claim_store(store, new_runtime_id())

    assert owns_store(store, first) is False
    assert heartbeat(store, first) is False


def test_a_store_without_an_owner_row_falls_back_to_the_runtime_record(
    store: sessionmaker[Session],
) -> None:
    """A restored or hand-edited store must still be usable.

    ``owns_store`` is consulted before every write, so it cannot be the thing that takes a
    deployment down because a row is missing. With no owner row recorded it answers from
    the runtime's own supersession instead, which is the same answer in every case except a
    concurrent start -- and a concurrent start on a store in that state is already outside
    what is supported.
    """

    runtime = new_runtime_id()
    claim_store(store, runtime)
    with store() as db:
        owner = db.get(StoreOwner, OWNER_ROW_ID)
        assert owner is not None
        db.delete(owner)
        db.commit()

    assert owns_store(store, runtime) is True

    # A claim recreates it rather than failing.
    second = new_runtime_id()
    claim_store(store, second)
    assert _owner(store) == second
    assert owns_store(store, runtime) is False


def test_a_clean_stop_leaves_no_live_runtime(store: sessionmaker[Session]) -> None:
    runtime = new_runtime_id()
    claim_store(store, runtime)
    record_clean_stop(store, runtime)

    assert _live_runtimes(store) == []
    # And the claim is released, so the store reads as unowned between an orderly stop and
    # the next start rather than naming a process that is gone.
    assert _owner(store) == ""
    assert owns_store(store, runtime) is False


# -- PostgreSQL, the pilot store, where the isolation level makes this real -------------


@pytest.mark.skipif(
    not POSTGRES_URL,
    reason="Set HARNESS_TEST_POSTGRES_URL to an empty, disposable PostgreSQL database.",
)
def test_concurrent_claims_on_postgresql_leave_one_owner() -> None:
    """The race the singleton row exists to close.

    Under READ COMMITTED, two startups that each read the live runtimes before writing
    cannot see each other, so both would find no previous owner and both would serve one
    store. Each claim here uses its own connection, as separate processes would.
    """

    pytest.importorskip("psycopg")
    engine = create_engine(POSTGRES_URL)
    Base.metadata.drop_all(engine)
    try:
        initialize_schema(engine)
        factory = build_session_factory(engine)
        runtimes = [new_runtime_id() for _ in range(8)]
        ready = threading.Barrier(len(runtimes))
        failures: list[BaseException] = []

        def claim(runtime_id: str) -> None:
            try:
                ready.wait(20.0)
                claim_store(factory, runtime_id)
            except BaseException as exc:  # noqa: BLE001 - reported below
                failures.append(exc)

        threads = [threading.Thread(target=claim, args=(runtime,)) for runtime in runtimes]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(60.0)

        assert not failures, f"a claim raised: {failures[0]!r}"
        with factory() as db:
            live = list(
                db.scalars(
                    select(ExecutionRuntime.id).where(
                        ExecutionRuntime.superseded_by.is_(None),
                        ExecutionRuntime.stopped_at.is_(None),
                    )
                ).all()
            )
            owner = db.get(StoreOwner, OWNER_ROW_ID)
        assert len(live) == 1, f"{len(live)} runtimes think they own the store: {live}"
        assert owner is not None and live == [owner.runtime_id]
        assert sum(owns_store(factory, runtime) for runtime in runtimes) == 1
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()
