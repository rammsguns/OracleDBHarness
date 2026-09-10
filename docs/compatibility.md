# Compatibility and test evidence

This file records what has been run. It is not a support matrix and it is not a
claim. Where something has not been tested, it says so.

## What has been tested

| Component | Version | Evidence |
| --- | --- | --- |
| Python | 3.11 | Full test suite |
| Node | 24 | Console typecheck, unit tests and production build; adapter tests |
| Metadata store | SQLite 3 | Full test suite |
| Oracle backend | Local stand-in (SQLite) | Full test suite |
| Copilot provider | Fixture provider | Copilot test suite |
| Protocol | 1.0 | Contract tests, adapter tests |

## What has not been tested

| Component | Status |
| --- | --- |
| **Oracle 19c** | **Never connected to.** This is the primary compatibility target and it is unqualified. |
| Oracle Database Free (23ai) | Never connected to. |
| python-oracledb thin mode | The adapter is written and typechecked; no statement has been executed through it. |
| python-oracledb thick mode | Not exercised. Needs Oracle Client libraries and a separate worker (ADR-0002). |
| TCPS and wallets | Not exercised. `ConnectionSpec` carries the fields; the path is untested. |
| PostgreSQL as the metadata store | The schema is portable SQLAlchemy and the container is configured, but the suite runs on SQLite. |
| OIDC | The verification path is written against JWKS; only the development signer has been exercised. |
| A real model provider | The Anthropic adapter is written against the current Messages API; no call has been made. |
| OracleDataForge | Not integrated. See `integrations/dataforge/COMPATIBILITY.md`. |
| Load | The pilot target is ten concurrent users across three databases. Not measured. |

## Why the stand-in is not evidence

`harness_worker.backend.fake` runs real SQL, real binds, real transactions and real
row counts, so the harness code above it - policy, session leases, limits, commit and
rollback, bounded fetch, cancellation paths - is genuinely exercised. That is what the
test suite demonstrates.

It is not Oracle. It does not implement PL/SQL beyond a small documented slice, it has
no optimizer, and its dictionary views are seeded tables. Behaviours that only appear
against a real database - DDL committing an open transaction for real, `ORA-00060`
deadlocks, `DBMS_XPLAN` output, cursor invalidation, LOB streaming, NUMBER precision,
time-zone handling - are untested.

Where the stand-in cannot honour something it raises an error naming itself, so a
passing test never quietly stands in for Oracle behaviour.

## Qualifying against Oracle 19c

An earlier version of this section said to set `HARNESS_ORACLE_BACKEND=oracledb` and
run the existing suites. That was wrong, and worth recording as a trap: the fixtures in
`tests/conftest.py` construct `Settings` directly with `oracle_backend="fake"`, so the
environment variable changes nothing and the suite would have gone on passing against
the stand-in while appearing to qualify Oracle.

The switch is `tests/qualification/config.py`, which reads its own `HARNESS_QUAL_*`
namespace. `oracle/qualification/README.md` has the full procedure; in outline:

1. Provision a 19c instance and an isolated schema on a **non-production** database,
   and run `oracle/grants/harness_roles.sql` after review.
2. Put the account password in a file, and optionally provide a privileged account
   that can run `ALTER SYSTEM KILL SESSION` — without it the connection-loss checks
   skip, because a session cannot honestly lose itself.
3. `uv sync --extra oracle`, set `HARNESS_QUAL_ORACLE_DSN`, `HARNESS_QUAL_ORACLE_USER`,
   `HARNESS_QUAL_ORACLE_PASSWORD_FILE` and `HARNESS_QUAL_REPORT`, then
   `uv run pytest`. The same variables switch `tests/integration` and `tests/e2e`
   over as well, so one run covers the backend behaviour and the API-level
   acceptance criteria together.
4. The fixture objects the acceptance criteria refer to are built and dropped by the
   suite itself, from the reviewed DDL in `oracle/qualification/`. They mirror what
   the stand-in seeds, so the same assertions mean the same thing on both.
5. Append the generated report block to "Recording a run" below. It carries the exact
   version, patch level, character set, container identity and driver version, and the
   suite's answers to the open questions in the gap table above.

Driver mode is process wide, so Thick mode is a second run in a separate process with
`HARNESS_QUAL_DRIVER_MODE=thick`.

### What runs, and what cannot

With a target configured, `tests/integration` and `tests/e2e` run against Oracle too:
the `settings` fixture switches the backend, the demonstration seed is pointed at the
real endpoints, and the fixture schema is built from the same reviewed DDL. Two groups
are excluded, and both are excluded for a reason rather than because they are
inconvenient:

| Excluded | Why | Where the evidence comes from instead |
| --- | --- | --- |
| Five commit-outcome tests and three tuning tests, marked `stand_in_only` | They inject a fault the stand-in exposes for the purpose (`fail_next_commit`), or patch its class. There is nothing to patch on a real driver | `tests/qualification/test_connection_loss.py`, which ends real sessions from a privileged connection |
| `test_two_targets_do_not_mix_identity_or_state`, marked `needs_second_target` | It compares two databases. Without `HARNESS_QUAL_SECOND_DSN` both profiles point at one, and it would pass trivially | Set a second DSN and it runs |

Three constraints follow from the fixtures being a mirror of the demonstration schema:

* The schema must be `HARNESS_APP`. The API suites name it in queries, runbook
  parameters and object lookups. A different name is refused up front with one
  message rather than several dozen `ORA-00942`s.
* The account needs the grants in `oracle/grants/harness_roles.sql`. The health-report
  check asserts every DBA panel is available, so a missing grant fails it — which is
  the useful answer, not a nuisance.
* The tuning checks read a cursor Oracle already has. `EXPLAIN PLAN` executes nothing,
  so the fixture setup runs the slow query once to put one in the shared pool.

Until this has been run and recorded, no release claim about Oracle support is
supportable.

## Open Oracle gaps in the transaction, cancellation and output safety work

Several failure paths were fixed and covered by regression tests, all of them against
the stand-in or against a stub driver connection. The harness logic is proven; the
Oracle behaviour each one depends on is not. Confirming these is what the
qualification suite above is for, and until it has been run, the fixes are
unqualified against Oracle:

| Workflow | What the tests prove | What only Oracle can confirm |
| --- | --- | --- |
| A commit whose answer is lost is reported as `outcome_unknown`, audited as such, and its session retired | `tests/integration/test_commit_outcomes.py`, `tests/unit/test_oracle_adapter.py` | That a real connection loss during `COMMIT` surfaces as one of the codes in `_FATAL_CODES` (`ORA-03113`, `ORA-03135`, `DPY-4011`), rather than some other code that would be classified as a plain failure. Reproduce by killing the session or the network mid-commit and recording the code the driver raises. |
| A statement broken on request is reported as `cancelled` rather than `failed` | `tests/unit/test_oracle_adapter.py`, `tests/unit/test_session_lifetime.py` | That breaking a running statement really does surface as `ORA-01013` from python-oracledb in the driver mode the deployment uses, and that it rolls back that statement alone while earlier work in the same transaction stays pending. Reproduce by cancelling a long `UPDATE` that follows an earlier one in the same transaction, then checking what survives a commit. |
| A statement that ignores a break leaves its connection untouched until the driver call returns | `tests/unit/test_session_quarantine.py`, `tests/unit/test_oneshot_cleanup.py` | That `Connection.cancel()` behaves as assumed against a genuinely long-running statement, and that a connection abandoned this way is reclaimed rather than leaking a server-side session. Check `v$session` after the statement finally ends. |
| Statement terminator handling | `tests/unit/test_statement.py` | That the statements the parser now passes through unchanged - a trailing comment after a removed `;`, a block comment ending in `/` - are accepted by Oracle as written. |
| Transaction state tracking in the adapter | `tests/unit/test_oracle_adapter.py` | That Oracle's implicit commits match what the adapter records: DDL and `CREATE OR REPLACE` of a program unit clearing the pending transaction, and PL/SQL blocks leaving one open. |
| A `COMMIT` or `ROLLBACK` typed into the worksheet resolves the same state the toolbar buttons do, and a lost connection during one is `outcome_unknown` rather than a plain failure | `tests/unit/test_oracle_adapter.py` | That `ROLLBACK TO SAVEPOINT` really does leave the transaction open where the adapter says it does, and that `COMMIT FORCE`/`ROLLBACK FORCE` against an in-doubt distributed transaction leave the local one untouched. Reproduce with a savepoint and with a distributed transaction left in doubt. |
| The profile's default schema is applied with `ALTER SESSION SET CURRENT_SCHEMA`, and a schema that cannot be set fails the connection rather than silently running elsewhere | `tests/unit/test_oracle_adapter.py` | That the quoted, upper-cased identifier the adapter generates addresses the intended schema on the target, including a schema whose name needs quoting; and which ORA code a non-existent or unauthorised schema raises. The stand-in reports `default_schema` as the current schema without ever setting it. |
| `DBMS_OUTPUT.GET_LINES` is bound as a collection, and output that cannot be read back is a warning rather than a failed block | `tests/unit/test_oracle_adapter.py` | That `Cursor.arrayvar` binds acceptably to `DBMSOUTPUT_LINESARRAY` on the target's driver and character set, that lines longer than the buffer behave as expected, and that a real block's output arrives in the order it was written. The stand-in implements no PL/SQL, so no `DBMS_OUTPUT` has ever been produced or read. |
| A cleanup failure after the statement has already run is a warning on the result, not a failed execution | `tests/unit/test_oracle_adapter.py` | Which code the driver raises when closing a cursor, or resetting `call_timeout`, finds the session already gone. Only the codes in `_FATAL_CODES` mark the connection broken, so any other code would leave a dead connection leased to the session. Reproduce by killing the session between the statement and its cleanup. |
| `EXPLAIN PLAN` and the read of `PLAN_TABLE` share one connection | `tests/integration/test_tuning.py` | That the target's `PLAN_TABLE` is the default global temporary table, so the rows really are session-private, and that reading them back on the same connection returns the plan. The stand-in keeps one shared, persistent `plan_table`, where a read on any connection succeeds -- so it cannot show this failing, only that the connection is shared. If a site has replaced `PLAN_TABLE` with a permanent table, record that here: the sharing is still correct, but it is no longer what makes the read work. |

The stub driver connection in `tests/unit/test_oracle_adapter.py` asserts the
adapter's own logic. It asserts nothing about python-oracledb, and its ORA codes are
taken from documentation rather than observation.

`tests/qualification/` is written to answer the right-hand column of that table. Each
check records what the database actually did, including when it contradicts the
assumption — a run where `ROLLBACK TO SAVEPOINT` closes the transaction, or where a
cancellation discards earlier work, produces a report saying so rather than a failure
nobody can interpret. None of it has been executed.

## Recording a run

Append; do not replace. Each entry should carry the date, the harness build, the exact
database version and configuration, which suites were run, and what failed.
