"""Second K12 held-out wording and paragraph-scope regressions."""

import pytest

from harness_api.copilot.claim_checks import lock_wait_exception_claims

COORDINATOR_BAD = [
    "The WHEN OTHERS clause will quietly absorb errors like unique-key violations, lock waits and full tablespaces.",
    "Things the handler can hide:\n\n- check constraint failures\n- waits on row locks held by other sessions\n- datatype conversion errors",
    "If another session holds the row, the resulting lock conflict is caught by WHEN OTHERS and ignored.",
    "Run-time problems such as lock contention or space exhaustion end up silently swallowed.",
    "Any lock wait the DELETE runs into is turned into a silent success by the handler.",
    "The handler suppresses everything, including a session left waiting for a row lock.",
]

COORDINATOR_GOOD = [
    "Session 212 has been waiting on a TX row lock held by session 87 for 14 minutes.",
    "A lock wait is not an exception: the DELETE simply blocks until the holder commits.",
    "Use SELECT ... FOR UPDATE NOWAIT if you want ORA-00054 instead of waiting on the lock.",
    "Long lock waits slow the batch but raise no error the handler could catch.",
    "The blocking session 87 holds row locks on ORDERS; the error in the job log came from a different statement.",
    "Deadlocks raise ORA-00060 in one session, which this handler would hide.",
    "Lock waits show up in V$SESSION as enq: TX - row lock contention.",
]

HELD_BAD = [
    "The wrapper absorbs lock contention during the update.",
    "The handler ignores failures, for instance waits for table locks.",
    "WHEN OTHERS eats waits on TX locks.",
    "Errors eaten by the handler include waiting for the row locks.",
    "Failures turned into success: lock contention.",
    "The handler hides problems, e.g. waits for a table lock.",
    "The handler absorbs failures, among them lock contention.",
    "The handler ignores problems:\n\n- conversion failures\n\n- waits on locks",
]

HELD_GOOD = [
    "The handler does not absorb lock contention.",
    "Waits for table locks are not exceptions.",
    "The handler never ignores waiting on TX locks.",
    "A wait on a row lock is not eaten by the handler.",
    "Lock contention is not turned into a silent success by this handler.",
    "The handler absorbs conversion errors. The DELETE waits for row locks.",
    "The handler ignores failures:\n\n- conversion errors\n\n## Lock diagnosis\n- waits on locks held by another session",
    "The handler absorbs errors, e.g. ORA-00054 with NOWAIT rather than waiting for a table lock.",
]


@pytest.mark.parametrize("answer", COORDINATOR_BAD + HELD_BAD)
def test_handler_claims_flag(answer: str) -> None:
    assert lock_wait_exception_claims(answer)


@pytest.mark.parametrize("answer", COORDINATOR_GOOD + HELD_GOOD)
def test_correct_waiting_prose_stays_clean(answer: str) -> None:
    assert not lock_wait_exception_claims(answer)


@pytest.mark.parametrize(
    "enumerator",
    ["like", "e.g.", "for example", "for instance", "including", "such as", "among them"],
)
def test_error_enumerators(enumerator: str) -> None:
    assert lock_wait_exception_claims(f"Errors {enumerator} lock contention.")
