# Oracle 19c qualification

By default the automated suite runs against the local stand-in, and that is what CI
runs. This directory and `tests/qualification/` are how a run reaches a real Oracle
target instead: with the `HARNESS_QUAL_*` variables below set, the backend suite runs
and the integration and end-to-end suites run through the API against that target.

**First run: 2026-09-11, against 19c EE 19.9.1 (non-CDB), thin mode.** It failed
before any test ran. Once the fixture script and a set of harness defects were fixed,
the whole suite passed. What was found and fixed is in
[docs/compatibility.md](../../docs/compatibility.md), "What the first Oracle 19c run
found", and the run is recorded there. The wiring is still checked in ordinary CI by
`tests/unit/test_qualification_fixtures.py` and `tests/unit/test_oracle_wiring.py`;
the run itself is manual.

## One switch, three suites

`tests/oracle_config.py` reads the `HARNESS_QUAL_*` variables below. With them set:

| Suite | Against the stand-in | With a target configured |
| --- | --- | --- |
| `tests/qualification` | Skips entirely | Drives the backend directly: transactions, cancellation, connection loss, compilation, binds, LOBs |
| `tests/integration`, `tests/e2e` | Runs, as it always has | Runs through the API against the real target |
| `tests/unit` | Runs | Runs, unchanged — it touches no database |

So `uv run pytest` covers the backend behaviour and the API-level acceptance criteria
in one run. What the API suites cannot do against Oracle, and why, is in
[docs/compatibility.md](../../docs/compatibility.md).

## What the backend suite covers

The six areas the release criteria name, each one currently answered only by a stub:

| Suite | What it settles |
| --- | --- |
| `test_identity_and_versions.py` | The exact database version, patch level, character set, container identity and driver version, written into the report. Also that `ALTER SESSION SET CURRENT_SCHEMA` really ran |
| `test_transactions.py` | Commit and rollback across two sessions, DDL committing pending DML, a PL/SQL block leaving its transaction open, `ROLLBACK TO SAVEPOINT` |
| `test_cancellation.py` | A break delivered to a genuinely running statement, whether it surfaces as cancelled rather than failed, whether the session survives it, and whether earlier work in the transaction survives it |
| `test_connection_loss.py` | Which ORA code a killed session raises mid-statement, mid-write and mid-`COMMIT`, and whether the harness reaches `outcome_unknown` rather than reporting a plain failure |
| `test_compilation.py` | Compiling a package body, line-level error rows, repair and recompile, and `DBMS_OUTPUT` read back through the `arrayvar` bind that has never been executed |
| `test_binds.py` | Scalar binds, `NUMBER(20,10)` precision, Unicode, nulls versus empty strings, timestamps with time zones, quoted identifiers, bounded fetch |
| `test_lobs.py` | The bounded LOB preview, `EMPTY_CLOB()` against NULL, hex previews for binary, and a zero-byte budget |

## Setting up

Use a **non-production** database. The fixtures drop and recreate their own objects on
every run, and the cancellation and connection-loss tests deliberately kill sessions.

1. Create a schema named **`HARNESS_APP`** and grant it what
   `oracle/grants/harness_roles.sql` describes, after reviewing that script. The name
   is not arbitrary: the integration and end-to-end suites address the demonstration
   schema by name, so a different one is refused up front. Only `tests/qualification`
   can run against a differently named schema.
2. Put the account's password in a file. It is read from a file rather than an
   environment variable so it does not appear in a process listing or shell history.
3. Optionally create a privileged account that can run
   `ALTER SYSTEM KILL SESSION`. Without it the connection-loss suite skips: a session
   cannot honestly lose itself, so those checks need something outside it.
4. Optionally provide a second, genuinely separate database. Without it the two-target
   isolation check skips, because two profiles pointing at one database would pass it
   without demonstrating anything.

```bash
export HARNESS_QUAL_ORACLE_DSN=dbhost:1521/ORCLPDB1
export HARNESS_QUAL_ORACLE_USER=harness_app
export HARNESS_QUAL_ORACLE_PASSWORD_FILE=/run/secrets/harness_app.password
export HARNESS_QUAL_REPORT=./qualification-report.md

# Optional, for the connection-loss suite:
export HARNESS_QUAL_ADMIN_DSN=dbhost:1521/ORCLPDB1
export HARNESS_QUAL_ADMIN_USER=system
export HARNESS_QUAL_ADMIN_PASSWORD_FILE=/run/secrets/system.password

# Optional, for the two-target isolation check:
export HARNESS_QUAL_SECOND_DSN=dbhost:1521/ORCLPDB2
export HARNESS_QUAL_SECOND_USER=harness_app
export HARNESS_QUAL_SECOND_PASSWORD_FILE=/run/secrets/harness_app.password

uv sync --extra oracle
uv run pytest -v
```

The DSN must be `host:port/service`. A connect descriptor or a tnsnames alias is
refused, because a connection profile has separate host, port and service columns and
nothing else — accepting one here would qualify a path the application cannot use.

Driver mode is process wide. To qualify Thick mode, run the suite again in a separate
process with `HARNESS_QUAL_DRIVER_MODE=thick` and
`HARNESS_QUAL_ORACLE_CLIENT_LIB_DIR` pointing at the Oracle Client libraries. A single
run cannot cover both.

Without `HARNESS_QUAL_ORACLE_DSN` the whole suite skips, which is what happens in CI.
A half-configured run fails rather than skipping: a qualification run that quietly did
nothing is indistinguishable from a passing one in a CI summary, and that is the
failure mode worth avoiding.

## The fixtures

`01_fixtures.sql` builds the objects the acceptance criteria refer to, mirroring what
`harness_worker.backend.fake` seeds into the stand-in so the same assertions mean the
same thing on both. `02_teardown.sql` removes them. Both are idempotent and both are
applied by `tests/oracle_fixtures.py`, through the same python-oracledb connection the
harness uses — so a fixture the driver cannot execute fails here rather than being
papered over by a separate client.

They use the ordinary names a sample schema uses — `EMPLOYEES`, `DEPARTMENTS`,
`ORDER_LINES`, `EMPLOYEE_REPORT` — because that is what the stand-in seeds and what
the suites query. Objects that exist only for qualification and have no counterpart in
the stand-in keep a `HARNESS_` prefix: `HARNESS_TYPES`, `HARNESS_LOBS`,
`HARNESS_BURN`. **The setup drops every one of these before creating it.** Use a
schema that holds nothing else.

The slow-query fixture is 400,000 rows. Building it takes a minute or two on the first
run and is the reason the setup budget is 15 minutes rather than the interactive 30
seconds.

## Recording a run

Set `HARNESS_QUAL_REPORT` to a path. The suite writes a Markdown block and a JSON
sidecar containing the database version and configuration it observed, the driver
version, and its answers to the open questions in
[docs/compatibility.md](../../docs/compatibility.md). Append that block to that file's
"Recording a run" section — do not replace what is there, because a compatibility
record that overwrites its predecessor cannot show a regression between two versions.

The report is written even when tests fail. A failed run against a recorded version is
evidence; an unrecorded one is not.
