"""K12: raised lock conditions, list context, and bounded revision."""

import pytest

from harness_api.copilot.claim_checks import (
    lock_wait_exception_claims,
    review_claims,
    review_plain_claims,
)
from harness_api.copilot.service import _plain_review


@pytest.mark.parametrize(
    "answer",
    [
        "Important distinction: if this procedure is a standalone definer’s-rights procedure, static SQL problems such as a missing object, missing column, missing privilege, or incompatible `INSERT ... SELECT *` column count can fail when the unit compiles. The `WHEN OTHERS` handler never sees compile-time failures. The handler only hides run-time failures that occur while the statements are executing, such as constraint violations, lock waits/timeouts, space errors, conversion errors, or other SQL errors raised during execution.",
        "The handler can only hide failures that occur while the PL/SQL code is running. Examples include runtime problems such as:\n\n- constraint violations during the `INSERT`,\n- conversion errors,\n- lock waits or lock timeouts,\n- insufficient space,\n- errors raised by triggers,\n- other runtime DML failures.",
        "The handler hides only failures that occur while the procedure body runs. For example:\n\n- constraint violations in `orders_archive` during the `INSERT`;\n- foreign-key constraint errors when deleting rows from `orders`;\n- conversion errors;\n- lock waits or lock conflicts;\n- space errors;\n- other runtime errors raised by the DML statements.",
        "This hides only failures that occur while the procedure is running, such as constraint violations, conversion errors, lock waits or deadlocks, insufficient space, and similar runtime DML failures. It does not hide compile-time problems such as a missing table, missing column, missing privilege, or static SQL column-count mismatch. For this kind of definer’s-rights stored unit, those are checked when the unit compiles. If the unit is invalid or cannot be compiled, the call fails before the procedure body runs, so the `WHEN OTHERS` handler never sees that failure either.",
        "a blocked row lock would also be swallowed",
        "The handler hides errors such as deadlocks, lock waits and constraint violations.",
        "Exceptions raised include lock conflicts and conversion failures.",
        "The exception handler catches waiting on a lock.",
        "Blocking is swallowed by WHEN OTHERS.",
        "Lock waits are hidden by this handler.",
        "Lock conflicts are caught as exceptions.",
        "The handler suppresses runtime failures:\n\n- conversion errors\n- lock waits\n- constraints",
        "Raised exceptions:\n1. lock conflicts\n2. space errors",
        "The exception handler can discard a row-lock wait.",
        "Errors such as waiting for a row lock are caught.",
        "Caught exceptions include blocking and deadlocks.",
        "Caught errors include blocking or deadlocks.",
        "The handler hides errors such as row locks or lock timeouts.",
    ],
)
def test_waiting_is_not_a_handled_exception(answer: str) -> None:
    assert lock_wait_exception_claims(answer)
    assert any(f.kind == "lock_wait_exception" for f in review_claims(answer, []).findings)
    assert any(f.kind == "lock_wait_exception" for f in review_plain_claims(answer).findings)
    assert any(
        f.kind == "lock_wait_exception"
        for f in _plain_review("explain", ("BEGIN NULL; END;",), answer).findings
    )


@pytest.mark.parametrize(
    "answer",
    [
        "session 42 is in a row-lock wait on ORDERS",
        "Runtime exceptions include lock waits ending in errors.",
        "The handler hides lock waits that exceed a timeout.",
        "The handler hides foreign-key constraints blocking deletion from orders.",
        "No error text, no failed job, no blocking session and no slow statement.",
        "Playbook: no ORA error, no scheduler job, no blocking or slow statement.",
        "Its 118-second wait is a lock wait, not an error.",
        "ORA-00054 resource busy and acquire with NOWAIT",
        "a lock wait times out with ORA-30006 only under WAIT n",
        "a lock wait raises no exception, it blocks",
        "a deadlock raises ORA-00060",
        "DDL_LOCK_TIMEOUT controls DDL waiting.",
        "Could a handler swallow a lock wait?",
        "Whether this handler catches lock waits is unknown.",
        "The handler does not hide a lock wait.",
        "Lock conflicts are not exceptions by themselves.",
        "An exception handler logs ORA-00054 after NOWAIT fails.",
        "The session is blocking another session; the handler logs errors.",
        "The handler logs conversion errors while the session is blocking another session.",
        "I did not run dependencies because status and errors showed nothing invalid, and used no scheduler, blocking or tuning lookups.",
        "The handler catches conversion errors. The session is in a lock wait.",
        "Errors caught by the handler:\n- conversion errors\n\n## Diagnosis\n- session 42 is in a row-lock wait",
    ],
)
def test_diagnosis_qualified_errors_denials_and_questions_pass(answer: str) -> None:
    assert not lock_wait_exception_claims(answer)


def test_fenced_source_is_not_an_assertion() -> None:
    assert not lock_wait_exception_claims("```sql\n-- handler hides lock waits\n```")


def test_lock_wait_finding_triggers_one_bounded_revision(client, developer) -> None:
    from tests.copilot.test_claim_checks import answer_of
    from tests.copilot.test_copilot import sse
    from tests.copilot.test_k7_wave2k import RETAINED, checks, explain_payload
    from tests.copilot.test_plain_claims import Reply, use_replies

    prefix = RETAINED + " Log the error and re-raise it. "
    draft = prefix + "The handler hides lock waits and conversion errors."
    revised = prefix + "The handler hides conversion errors; an ordinary lock wait blocks."
    provider = use_replies(client, Reply(draft), Reply(revised))
    events = sse(client, developer, explain_payload())
    first, second = checks(events)
    assert first["action"] == "revise"
    assert [f["kind"] for f in first["findings"]] == ["lock_wait_exception"]
    assert second["stage"] == "revised" and second["findings"] == []
    assert len(provider.messages) == 2
    assert answer_of(events) == revised
