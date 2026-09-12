"""The verification predicates, read directly.

Each mutating runbook declares what its evidence has to say before the run counts as
verified. Through the API those queries are filtered by the runbook's own parameters,
so the cases here -- evidence for a different object, or missing the column the check
reads -- are reached by calling the predicates with the evidence they would see.
"""

from __future__ import annotations

from harness_api.runbooks import (
    RUNBOOKS,
    VerificationEvidence,
    _recompiled_object_is_valid,
    _statistics_were_recorded,
)

OBJECT_COLUMNS = ["OWNER", "OBJECT_NAME", "OBJECT_TYPE", "STATUS", "LAST_DDL_TIME"]
TABLE_COLUMNS = ["OWNER", "TABLE_NAME", "NUM_ROWS", "LAST_ANALYZED", "TABLESPACE_NAME"]
RECOMPILE = {
    "owner": "HARNESS_APP",
    "object_name": "EMPLOYEE_REPORT",
    "object_kind": "PACKAGE BODY",
}
GATHER = {"owner": "HARNESS_APP", "table_name": "ORDER_LINES"}


def _objects(rows: list[list[object]], parameters: dict | None = None) -> VerificationEvidence:
    return VerificationEvidence(
        columns=list(OBJECT_COLUMNS), rows=rows, parameters=parameters or dict(RECOMPILE)
    )


def _tables(rows: list[list[object]], parameters: dict | None = None) -> VerificationEvidence:
    return VerificationEvidence(
        columns=list(TABLE_COLUMNS), rows=rows, parameters=parameters or dict(GATHER)
    )


def test_every_mutating_runbook_declares_what_verifies_it() -> None:
    """Without a rule, any returned row counts as evidence. That is the old behaviour."""

    for spec in RUNBOOKS:
        if not spec.mutating:
            continue
        assert spec.verification_operation_id, spec.id
        assert spec.verification_rule is not None, spec.id
        assert spec.verification_rule.requirement, spec.id
        assert spec.describe()["verificationRequirement"] == spec.verification_rule.requirement


def test_a_valid_object_satisfies_the_recompile_rule() -> None:
    rows = [
        ["HARNESS_APP", "EMPLOYEE_REPORT", "PACKAGE", "VALID", "2026-09-11"],
        ["HARNESS_APP", "EMPLOYEE_REPORT", "PACKAGE BODY", "VALID", "2026-09-11"],
    ]
    assert _recompiled_object_is_valid(_objects(rows)) == ""


def test_a_body_left_invalid_fails_the_recompile_rule() -> None:
    """A valid specification does not make the package usable while its body is not."""

    rows = [
        ["HARNESS_APP", "EMPLOYEE_REPORT", "PACKAGE", "VALID", "2026-09-11"],
        ["HARNESS_APP", "EMPLOYEE_REPORT", "PACKAGE BODY", "INVALID", "2026-09-11"],
    ]
    shortfall = _recompiled_object_is_valid(_objects(rows))
    assert "HARNESS_APP.EMPLOYEE_REPORT (PACKAGE BODY) is INVALID" in shortfall
    assert "not VALID" in shortfall


def test_another_object_being_valid_is_not_evidence() -> None:
    """The row has to be about the object the runbook was asked to recompile."""

    rows = [["HARNESS_APP", "ORDER_REPORT", "PACKAGE BODY", "VALID", "2026-09-11"]]
    shortfall = _recompiled_object_is_valid(_objects(rows))
    assert "no object HARNESS_APP.EMPLOYEE_REPORT (PACKAGE BODY)" in shortfall


def test_the_check_is_scoped_to_the_kind_that_was_compiled() -> None:
    """A specification and its body share a name; the runbook compiles one of them.

    Recompiling the specification is verified by the specification being VALID. The
    body is a real problem and stays in the evidence, but it is not what this run was
    asked to change, so it does not decide this run's verdict.
    """

    rows = [
        ["HARNESS_APP", "EMPLOYEE_REPORT", "PACKAGE", "VALID", "2026-09-11"],
        ["HARNESS_APP", "EMPLOYEE_REPORT", "PACKAGE BODY", "INVALID", "2026-09-11"],
    ]
    spec_run = dict(RECOMPILE, object_kind="PACKAGE")
    assert _recompiled_object_is_valid(_objects(rows, spec_run)) == ""

    body_run = dict(RECOMPILE, object_kind="package body")
    assert "INVALID" in _recompiled_object_is_valid(_objects(rows, body_run))


def test_a_kind_the_dictionary_does_not_hold_is_unconfirmed() -> None:
    """Recompiling a body no row describes is not verified by the specification."""

    rows = [["HARNESS_APP", "EMPLOYEE_REPORT", "PACKAGE", "VALID", "2026-09-11"]]
    shortfall = _recompiled_object_is_valid(_objects(rows))
    assert "no object HARNESS_APP.EMPLOYEE_REPORT (PACKAGE BODY)" in shortfall


def test_the_recompile_rule_says_so_when_the_status_column_is_missing() -> None:
    evidence = VerificationEvidence(
        columns=["OWNER", "OBJECT_NAME"],
        rows=[["HARNESS_APP", "EMPLOYEE_REPORT"]],
        parameters=dict(RECOMPILE),
    )
    assert "columns this check reads" in _recompiled_object_is_valid(evidence)


def test_recorded_statistics_satisfy_the_gather_rule() -> None:
    rows = [["HARNESS_APP", "ORDER_LINES", 400000, "2026-09-11 10:00:00", "USERS"]]
    assert _statistics_were_recorded(_tables(rows)) == ""


def test_a_table_with_no_recorded_statistics_fails_the_gather_rule() -> None:
    """A row exists for every table. Only a gathered one carries these two values."""

    for rows in (
        [["HARNESS_APP", "ORDER_LINES", None, "2026-09-11 10:00:00", "USERS"]],
        [["HARNESS_APP", "ORDER_LINES", 400000, None, "USERS"]],
    ):
        shortfall = _statistics_were_recorded(_tables(rows))
        assert "still has no recorded statistics" in shortfall


def test_another_table_having_statistics_is_not_evidence() -> None:
    rows = [["HARNESS_APP", "EMPLOYEES", 120, "2026-09-11 10:00:00", "USERS"]]
    assert "no table HARNESS_APP.ORDER_LINES" in _statistics_were_recorded(_tables(rows))
