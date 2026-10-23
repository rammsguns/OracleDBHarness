"""K12 review probes and held-out paraphrases of exception and diagnostic prose."""

import pytest

from harness_api.copilot.claim_checks import LookupFacts, lock_wait_exception_claims, review_claims

PROBES = [
    ("INV-01", "An ordinary row-lock conflict is caught by WHEN OTHERS.", True),
    ("INV-02", "Any blocking on the target table is discarded by the exception handler.", True),
    (
        "INV-03",
        "If another session updates the row, the resulting lock conflict is raised as an error.",
        True,
    ),
    ("INV-04", "In this procedure, row locks would be swallowed by the exception block.", True),
    (
        "INV-05",
        "Because WHEN OTHERS is present, waiting on the row lock would be caught as a runtime exception.",
        True,
    ),
    ("INV-06", "Blocking would be swallowed if an exception occurs in the caller.", True),
    ("INV-07", "The exception handler masks lock conflicts during the batch run.", True),
    ("INV-08", "WHEN OTHERS masks row-lock waits that occur during the update.", True),
    ("INV-09", "The anonymous block masks runtime errors:\n- lock waits\n- numeric overflow", True),
    (
        "INV-10",
        "Potential runtime errors hidden by the handler:\n* foreign key violations\n* blocking\n* data corruption",
        True,
    ),
    ("INV-11", "The block catches several DML failures:\n1. duplicate keys\n2. lock waiting", True),
    (
        "INV-12",
        "Runtime exceptions caught by the enclosing block:\n- row-lock waits\n- syntax errors",
        True,
    ),
    ("INV-13", "The block suppresses runtime exceptions such as waiting for a lock.", True),
    ("INV-14", "The handler hides DML errors, for example row locks on the orders table.", True),
    (
        "INV-15",
        "Row-lock conflicts are runtime failures that this handler swallows without reporting.",
        True,
    ),
    (
        "INV-16",
        "The procedure intercepts all exceptions raised by the DELETE, including lock conflicts.",
        True,
    ),
    (
        "INV-17",
        "Any uncommitted change can cause a lock wait that gets caught by WHEN OTHERS.",
        True,
    ),
    ("INV-18", "The handler can suppress DML runtime failures such as row-lock waiting.", True),
    ("LEG-01", "session 41 is blocked by 37 on a TX row lock", False),
    ("LEG-02", "the blocking session holds row locks while the error is raised elsewhere", False),
    (
        "LEG-03",
        "Session 15 holds exclusive row locks while session 22 is waiting for a TX lock.",
        False,
    ),
    (
        "LEG-04",
        "The blocking session 102 was running a long-running batch job without committing.",
        False,
    ),
    (
        "LEG-05",
        "Session 88 is blocked on a row lock, and we must check if an error occurred in the batch job.",
        False,
    ),
    ("LEG-06", "ORA-00054 indicates resource busy when NOWAIT is specified on DDL.", False),
    (
        "LEG-07",
        "SELECT FOR UPDATE WAIT 5 will timeout with ORA-30006 if the row remains locked.",
        False,
    ),
    (
        "LEG-08",
        "When a deadlock occurs between concurrent sessions, Oracle raises ORA-00060 in one session.",
        False,
    ),
    ("LEG-09", "DDL_LOCK_TIMEOUT causes DDL statements to wait before raising ORA-00054.", False),
    (
        "LEG-10",
        "The procedure caught an ORA-00060 deadlock exception and retried the transaction.",
        False,
    ),
    (
        "LEG-11",
        "To avoid indefinite blocking, use SELECT FOR UPDATE NOWAIT and handle ORA-00054.",
        False,
    ),
    (
        "LEG-12",
        "The application uses optimistic locking with NOWAIT to prevent blocking and catch ORA-00054.",
        False,
    ),
    ("LEG-13", "A lock wait does not raise an exception; the session simply enqueues.", False),
    (
        "LEG-14",
        "An ordinary lock wait is not an error because the session waits until the lock is released.",
        False,
    ),
    ("LEG-15", "Holding row locks does not raise any errors.", False),
    (
        "LEG-16",
        "High row-lock contention degrades throughput but does not generate application errors.",
        False,
    ),
    (
        "LEG-17",
        "Excessive blocking on the accounts table is caused by unindexed foreign keys.",
        False,
    ),
    (
        "LEG-18",
        "Blocking sessions can be monitored in v$session without throwing any database errors.",
        False,
    ),
    ("HELD-INV-01", "The handler masks blocking conflicts during execution.", True),
    ("HELD-INV-02", "Row-lock waiting is swallowed by the outer handler.", True),
    ("HELD-INV-03", "An error is raised when waiting for the lock.", True),
    ("HELD-INV-04", "The handler catches being blocked on a lock.", True),
    ("HELD-INV-05", "The block masks failures: conversion errors, blocking, space errors.", True),
    ("HELD-INV-06", "The handler hides errors:\n- blocking", True),
    ("HELD-INV-07", "Runtime exceptions include blocking waits.", True),
    ("HELD-INV-08", "The wrapper masks lock waits silently.", True),
    ("HELD-LEG-01", "The blocking transaction raises a conversion error elsewhere.", False),
    ("HELD-LEG-02", "The blocking chain is logged by the error handler.", False),
    ("HELD-LEG-03", "Lock waiting continues without raising an exception.", False),
    ("HELD-LEG-04", "A lock wait persists without throwing errors.", False),
    ("HELD-LEG-05", "Row-lock conflicts resolve without causing errors.", False),
    ("HELD-LEG-06", "Waiting for a lock proceeds without signaling exceptions.", False),
    ("HELD-LEG-07", "Blocked on a lock without signalling errors, the session waits.", False),
    ("HELD-LEG-08", "Lock waits remain free of errors without generating exceptions.", False),
]


@pytest.mark.parametrize("probe_id,answer,expected", PROBES, ids=[p[0] for p in PROBES])
def test_review_probes(probe_id: str, answer: str, expected: bool) -> None:
    assert bool(lock_wait_exception_claims(answer)) is expected


def test_kiwi_diagnosis_does_not_check_lock_wait_exceptions() -> None:
    answer = "The exception handler masks lock waits."
    assert lock_wait_exception_claims(answer)
    assert not any(
        f.kind == "lock_wait_exception"
        for f in review_claims(answer, [LookupFacts("sessions", True, [], [], {})]).findings
    )


@pytest.mark.parametrize(
    "noun", ["session", "sessions", "lock", "locks", "chain", "transaction", "SID", "holder"]
)
def test_blocking_noun_phrases_are_not_wait_exception_claims(noun: str) -> None:
    assert not lock_wait_exception_claims(f"The blocking {noun} caused an error elsewhere.")


@pytest.mark.parametrize(
    "answer",
    [
        "The session acquired row locks while an error was raised elsewhere.",
        "The session holds exclusive row locks while the error is raised elsewhere.",
        "The row locks held by a session are logged by the exception handler.",
        "Row locks acquired by a session are logged by the error handler.",
        "Row locks taken by a session are logged by the exception handler.",
        "Lock waits persist without generating errors.",
        "The handler logs a lock wait that is free of errors.",
    ],
)
def test_lock_holding_and_additional_denials_pass(answer: str) -> None:
    assert not lock_wait_exception_claims(answer)


def test_handler_result_denial_does_not_excuse_a_listed_wait() -> None:
    assert lock_wait_exception_claims(
        "Because WHEN OTHERS catches runtime exceptions, the procedure completes "
        "without raising an error even if:\n- there is a lock conflict"
    )
