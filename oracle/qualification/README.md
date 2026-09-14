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
| `test_identity_and_versions.py` (container) | That `isCdb` and `containerName` agree with `CON_ID`/`CON_NAME`. A non-CDB or `CDB$ROOT` target is recorded as a gap in PDB coverage, not a pass |
| `test_target_isolation.py` | That the second target is a different container on the databases' own say-so (DBID, CON_DBID, instance, host), not merely a differently spelled DSN |
| `test_restricted_account.py` | That an account really missing grants probes as such, and that every DBA panel's outcome agrees with the probe: no panel failing with an Oracle error its probe allowed |

And through the API, in `tests/integration/test_process_death.py`, which runs against
whichever backend the switch selects:

| Scenario | What it settles |
| --- | --- |
| Process death before dispatch, during a read, during a write, during `COMMIT` (before it is sent and after it returns), and mid-statement while a write waits on a row lock | What restart reconciliation records, what Oracle actually did once it cleaned up the dead session, that nothing was replayed, and that no success was recorded for an answer nobody saw. See [Process death](#process-death) |

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

### A restricted account, a PDB, and requiring what the run is for

5. Optionally create a restricted account on the primary database (or on
   `HARNESS_QUAL_RESTRICTED_DSN`). It must be missing some of the grants the DBA panels
   need, or the checks fail and say the account is not restricted. One reviewed shape,
   for a DBA to adapt:

   ```sql
   CREATE USER harness_restricted IDENTIFIED BY "managed elsewhere";
   GRANT CREATE SESSION TO harness_restricted;
   GRANT SELECT ON sys.dba_tablespaces              TO harness_restricted;
   GRANT SELECT ON sys.dba_tablespace_usage_metrics TO harness_restricted;
   -- deliberately no V$SESSION, V$SQL or DBA_SCHEDULER_* grants
   ```

6. To qualify a PDB, point `HARNESS_QUAL_ORACLE_DSN` at the PDB's service. The container
   check records what the target is; a non-CDB or `CDB$ROOT` target is written to the
   report as a gap in PDB coverage.

```bash
# Optional, for the restricted-account checks:
export HARNESS_QUAL_RESTRICTED_USER=harness_restricted
export HARNESS_QUAL_RESTRICTED_PASSWORD_FILE=/run/secrets/harness_restricted.password

# For the run that is meant to close the NP-03 gate: a missing environment fails the
# run instead of skipping its checks.
export HARNESS_QUAL_REQUIRE=admin,second_target,restricted_account,pdb
```

Every optional environment the run did not have is listed under "Not exercised by this
run" in the report. `HARNESS_QUAL_REQUIRE` accepts `admin`, `second_target`,
`restricted_account` and `pdb`; an unknown name is refused.

## Process death

`tests/integration/test_process_death.py` is the Oracle half of restart recovery (NP-01).
It is not the fault-injection tests: nothing is raised inside the harness. Each scenario
starts the API as a separate Python process against the qualification target, sends it a
real request, waits until it reaches a chosen point, and ends it with `Popen.kill()` -
`SIGKILL` on Linux, `TerminateProcess` on Windows - so no handler in it runs. A second
process then starts over the same metadata store. The points are a pause in the child,
installed by `tests/process_death/child.py`, not a change to what the harness does.

Three sources are checked in every scenario: the execution and session records in the
metadata store, the administrator's reconciliation report from the restarted process, and
the row itself, read through a separate Oracle session only after that session has seen
the dead one cleaned up.

| Point | The database, once cleaned up | The record |
| --- | --- | --- |
| Record written, not dispatched | Row unchanged | `cancelled`, `interrupted_before_dispatch` |
| Read fetched, answer not returned | Unchanged | `failed`, no verification |
| UPDATE applied in an open transaction, answer not returned | Rolled back with the session | `outcome_unknown`, verification required; finding `not_applied` is recorded |
| UPDATE waiting inside Oracle on a row lock the observer holds | Rolled back once the wait ends | `outcome_unknown` |
| Commit intent recorded, `COMMIT` not sent | Rolled back | Session under `commitsUnknown`; finding `not_applied` |
| `COMMIT` returned, answer not recorded | **Durable** | Session under `commitsUnknown`, no successful commit audited; finding `applied` |

The last two leave identical records and opposite database states. That is the reason an
interrupted commit is reported as unknown rather than as either outcome.

A scenario only records evidence when the killed process's own backend reports
`oracledb` and its worksheet identity is not the stand-in's; otherwise it fails. The same
file runs against the stand-in in ordinary CI as a rehearsal of the machinery, and writes
nothing.

**Prerequisites**, beyond the setup above:

* A disposable `HARNESS_APP` schema on a non-production database. Scenarios write to
  `EMPLOYEES` row 101 and put it back; a run that is interrupted can leave it changed
  until the next fixture rebuild.
* `SELECT` on `V$SESSION` for the qualification account (`harness_diagnostics_role` has
  it). Without it the mid-statement scenario skips - it cannot show the statement was
  inside the database - and cleanup is judged by the row lock alone. Both are written to
  the report.
* A host that can start child Python processes and bind an ephemeral port on
  `127.0.0.1`. Each child uses a SQLite metadata store in the test's temporary directory;
  the scenarios are about the target database, and the metadata store's own restart
  behaviour is covered on PostgreSQL in CI.
* The client host and the database on a network where a closed socket reaches the server.
  A killed process's sockets are closed by the operating system, so the server notices at
  its next read or write. A host that loses power or network sends nothing; that needs
  `SQLNET.EXPIRE_TIME` (dead connection detection) and is **not** covered by these
  scenarios.

```bash
uv run pytest tests/integration/test_process_death.py -v
```

With `HARNESS_QUAL_REPORT=./qualification-report.md`, the observations are written to
`./qualification-report-process-death.md` (and a `.json` sidecar), separately from the
backend suite's report.

**Process cleanup.** Every child is killed in test teardown, pass or fail. If pytest itself
is killed, children can be left running: look for command lines containing
`tests.process_death.child` and end them. A killed child's Oracle session is cleaned up by
the server on its own; one that is still present after the run shows in `V$SESSION` with
`MODULE = 'OracleDBHarness'` under the qualification account. The mid-statement scenario
holds a row lock from its observer session until it releases it in a `finally`; if the
pytest process dies first, that session ends with it and the lock goes too. The scenarios
wait up to 180 seconds for cleanup and fail, rather than read a transaction in progress,
if it has not happened.

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
