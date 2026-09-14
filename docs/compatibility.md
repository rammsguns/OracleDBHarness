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
| **Oracle Database** | **19c Enterprise Edition 19.9.1.0.0, non-CDB, AL32UTF8** | `tests/qualification` (47 checks), `tests/integration` and `tests/e2e` against one instance, two recorded runs on 2026-09-11. See [Recording a run](#recording-a-run) |
| python-oracledb | 4.0.2, thin mode | The same run |
| Copilot provider | Fixture provider | Copilot test suite |
| Protocol | 1.0 | Contract tests, adapter tests |
| Identity provider | Keycloak 26.3, local container | `tests/identity` and `apps/web/src/oidc.keycloak.test.ts`, in CI. See [Identity providers](#identity-providers) |

## What has not been tested

| Component | Status |
| --- | --- |
| **Oracle 19c beyond one instance** | One non-CDB 19.9 instance has been qualified. Not run: a container database or PDB, a later release update, RAC, a restricted account rather than the full harness roles, and the two-target isolation check, which needs a second database. The checks for the PDB, the restricted account and the second target's database identity are written (`tests/qualification/test_identity_and_versions.py`, `test_restricted_account.py`, `test_target_isolation.py`) and have never been run against a database. |
| **Restart after process death, on Oracle** | Never run. `tests/integration/test_process_death.py` kills a real API process before dispatch, during a read, during a write, mid-statement and during `COMMIT`, and reads the outcome through an independent session. It runs against the stand-in in CI, which exercises the harness's reconciliation after a real OS kill and says nothing about what Oracle does with the dead session. See `oracle/qualification/README.md`, "Process death". A host that loses power or network, rather than a killed process, is not covered at all. |
| Oracle Database Free (23ai) | Never connected to. |
| python-oracledb thick mode | Not exercised. Needs Oracle Client libraries and a separate worker (ADR-0002). |
| python-oracledb 26.0.0 | Tried on 2026-09-11 against the qualified 19c instance, for the `call_timeout` crash below only. **It does not fix it:** assigning `call_timeout` on a session dropped with `DPY-4011` still ends the process with `0xC0000005`, in every attempt that dropped the session (13 of 13 in one batch, and every later attempt). On the dropped session it otherwise behaves like 4.0.2: reading `call_timeout`, `close()`, `cursor.close()` and `is_healthy()` are safe; `ping()` and `rollback()` raise `DPY-1001`. The suites have not been run on it, so 4.0.2 stays pinned. Not yet reported upstream. |
| TCPS and wallets | Not exercised. `ConnectionSpec` carries the fields; the path is untested. |
| PostgreSQL as the metadata store | The schema is portable SQLAlchemy and the container is configured, but the suite runs on SQLite. |
| OIDC providers other than Keycloak | Entra ID, Okta and Auth0 are documented in docs/setup.md but have not been signed in against. Nor has the pilot's own provider registration. |
| Sign-in in a real browser | The console's sign-in code has run against Keycloak under Node, and the token endpoint's CORS answer has been checked. `tests/browser` now drives Chromium through the console's sign-in, callback, API access, sign-out and expiry: its fixture mode runs in the CI `identity` job against the Keycloak realm (first passed on pull request #16, CI run 34788083498: 24 checks passed, 2 observed), and a rehearsal against a local stand-in provider has passed. **No browser run against the pilot's registration, deployed origin or proxy has happened**, and a fixture run cannot stand in for one. See tests/identity/README.md, "Browser qualification". |
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
against a real database are not exercised by the default suite. The qualification run
below covered DDL committing an open transaction, `DBMS_XPLAN` output, LOB previews,
NUMBER precision and time-zone handling against one 19c instance; `ORA-00060`
deadlocks and cursor invalidation remain untested anywhere.

Where the stand-in cannot honour something it raises an error naming itself, so a
passing test never quietly stands in for Oracle behaviour.

## Identity providers

Console sign-in is qualified against Keycloak 26.3, running locally with the realm in
`tests/identity/keycloak/realm.json`. That realm registers the console the way
docs/setup.md tells an operator to. Nothing in the run is stubbed:

| Checked | How |
| --- | --- |
| Discovery gives the console its endpoints, and the issuer matches exactly | `Authenticator.console_sign_in()` against the realm |
| The provider refuses the console an authorization request without PKCE | Request with no `code_challenge` |
| The token endpoint allows the console's origin, and no other | `Access-Control-Allow-Origin` on the code exchange |
| The console's own `authorizationUrl` and `completeSignIn` complete a sign-in | `oidc.keycloak.test.ts`, under Node against the realm |
| An intercepted code cannot be redeemed twice | Replayed exchange, refused by the provider |
| The API accepts the access token for a registered account, and says which subject to register otherwise | `/api/v1/auth/me` before and after registering |
| Tokens the realm issued to another application, and ID tokens, are refused | Audience check |
| A running API follows a signing-key rotation | New key added through Keycloak's admin API mid-run |

The run found one defect, now fixed. The API cached the provider's key set for five
minutes, and a token signed with a key from after the cache was filled was refused.
After a rotation, every sign-in would have failed until the cache expired. A token
naming a key the cache does not list now causes one fetch, at most once a minute.

It did not cover a real browser completing the redirect, providers other than
Keycloak, or a pilot's own registration. The suite drives Keycloak's login form and
admin API, so it does not carry over to another provider as it stands. Check a
pilot's registration by signing in through the console; tests/identity/README.md
has the checklist.

## Qualifying against Oracle 19c

An earlier version of this section said to set `HARNESS_ORACLE_BACKEND=oracledb` and
run the existing suites. That was wrong, and worth recording as a trap: the fixtures in
`tests/conftest.py` construct `Settings` directly with `oracle_backend="fake"`, so the
environment variable changes nothing and the suite would have gone on passing against
the stand-in while appearing to qualify Oracle.

The switch is `tests/oracle_config.py`, which reads its own `HARNESS_QUAL_*`
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

One run is recorded below. It supports a claim about that configuration only - 19c
19.9 as a non-CDB, thin mode, python-oracledb 4.0.2 - and the gaps listed under "What
has not been tested" still stand. No broader claim about Oracle support is supportable
until they are run and recorded too.

## What the first Oracle 19c run found

The first run, on 2026-09-11, was the first time the harness met Oracle. It failed
before any test ran, and every failure below was fixed and re-run until the whole
suite passed. Each has a regression test that fails without the fix.

**Defects in the harness**, each invisible to the stand-in:

| Defect | Effect before the fix | Fix |
| --- | --- | --- |
| **A statement deadline could crash the API process.** When the driver cannot deliver a call timeout as a break, python-oracledb 4.0.2 (thin) drops the session with `DPY-4011`, and assigning `call_timeout` on that session crashes the process with an access violation. The adapter reset `call_timeout` after every statement. | One statement past its deadline, inside an open transaction, ended the API for every user. Reproduced in 6 of 6 attempts under those conditions, and still present in 26.0.0 (see "What has not been tested"). | `call_timeout` is only touched on a session `is_healthy()` reports alive, and a session that is not is retired before anything else touches it. Measured on a dropped session: `close()`, `cursor.close()` and `is_healthy()` are safe; `ping()` and `rollback()` raise `DPY-1001`; only the `call_timeout` setter crashes. |
| `NUMBER` arrived as a float | `NUMBER(20,10)` value `1234567890.0123456789` was shown as `1234567890.0123458` | Fetched as `Decimal`; kept as a JSON number where a float is exact, and as an exact string otherwise |
| The driver deadline (`DPY-4024`) was reported as an Oracle error | A timeout looked like SQL Oracle refused | Reported as `execution_timeout`. Measured: the session survives and earlier work in the transaction stays pending |
| `DBMS_OUTPUT.ENABLE` was sized to the budget | A block writing more than 20,000 bytes failed with ORU-10027 instead of being truncated | The server buffer is 1,000,000 bytes, the largest finite size ENABLE accepts, and the budget applies when reading, so output past the budget is truncated. Scaling the buffer to the budget was tried and failed against 19c: a 512-byte budget hit ENABLE's 2,048-byte floor and a 30 KB block was stopped. It is not unlimited: a block writing in a loop would grow session memory until its deadline. A block that fills even the server buffer is stopped by Oracle and reported as such. Unread or overflowed output is discarded, so it cannot appear as the next statement's |
| A zero LOB preview budget read zero bytes | `DPY-2047` on every query returning a LOB | No read when the budget is zero. Text previews are also now trimmed to the byte budget, not a character count |
| A CLOB's length was reported as `byteLength` | The driver counts a CLOB or NCLOB in characters, so multibyte text was under-reported: 4,000 two-byte characters as 4,000 bytes | A text LOB reports `charLength` and the result grid says "characters"; a BLOB keeps `byteLength`. A true byte length would mean reading the whole value |
| `isCdb` came from the container name | A non-CDB reported itself as a container database | Read from `CON_ID` |
| The session serial number was never read | `KILL SESSION` could not name a harness session | Read from `V$SESSION` where the account can. Only a missing grant leaves it unknown; a session lost during the probe fails the identity rather than opening a worksheet on a dead connection |
| The recompile runbook generated `ALTER PACKAGE BODY x COMPILE` | ORA-00922 for every package or type body | `ALTER PACKAGE x COMPILE BODY`. The stand-in now refuses the old form as Oracle does |
| `dba.scheduler_jobs` selected region-named `TIMESTAMP WITH TIME ZONE` columns | The panel was unavailable on every 19c in thin mode (`DPY-3022`) | Converted with `SYS_EXTRACT_UTC` and labelled `_utc` |
| `dba.scheduler_failures` selected `ERROR_NUMBER` | ORA-00904; the column is `ERROR#` | `error# AS error_number` |

**Driver limitations** the harness cannot fix, and now reports:

* `TIMESTAMP WITH TIME ZONE` loses its offset in thin mode. python-oracledb returns
  the wall-clock time as a naive datetime, even when the column is fetched as a
  string. A result where such a column's values arrived without an offset carries a
  warning naming it and the `TO_CHAR` that shows the offset. The warning is raised
  from the fetched values, not the column type, because thick mode is unqualified.
* A `TIMESTAMP WITH TIME ZONE` stored with a region name cannot be fetched at all in
  thin mode (`DPY-3022`). The error now says what to select instead.

**Defects in the setup and the tests**:

* `oracle/grants/harness_roles.sql` cannot be run as `SYSTEM`: granting `SELECT` on
  `SYS` views needs `SYS` or the grant option. The run substituted
  `SELECT_CATALOG_ROLE` for those seven grants; the script now says so.
* The fixture script had never executed. It wrote a `BINARY_DOUBLE` literal
  without its `d` suffix (ORA-01426), used 29 February 2026, and built LOBs in SQL,
  where `RPAD` stops at 4,000 bytes and `RAW` at 2,000, so they either failed or came
  out under the size they exist to exceed.
* Several API tests assumed the stand-in: a fresh database per test, its row counts,
  its fake SQL ID, its seeded blocked session and failed job, and a PL/SQL block using
  a scalar subquery the stand-in accepted and Oracle refuses (PLS-00405). Tests that
  change the shared Oracle schema now restore it. The blocking panel now has a
  qualification check against a genuinely blocked session instead.

## Oracle gaps in the transaction, cancellation and output safety work

Several failure paths were fixed and covered by regression tests against the stand-in
or a stub driver connection. The 2026-09-11 run answered most of what only Oracle can
confirm; the answers are in "Recording a run", and what that run could not reach is
listed after the table. The table is kept as written, so each question can be traced
to its answer:

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
nobody can interpret.

Still open after the 2026-09-11 run: `COMMIT FORCE`/`ROLLBACK FORCE` against an
in-doubt distributed transaction; a schema whose name needs quoting; whether a
session abandoned after ignoring a break is reclaimed on the server; and a site whose
`PLAN_TABLE` is not the default temporary table.

## Recording a run

Append; do not replace. Each entry should carry the date, the harness build, the exact
database version and configuration, which suites were run, and what failed.

### Run 2026-09-11 19:04 UTC

- Harness build: `4a0bf24` plus the fixes listed in "What the first Oracle 19c run found", uncommitted at the time and committed as `12f6902`. The `charLength` field, the finite DBMS_OUTPUT buffer, the value-based offset warning and the narrowed identity probe came after this run, so its LOB rows below still say `byteLength`
- Target: a single-instance non-CDB on a private network, service `ORCL`, as `harness_app`, schema `HARNESS_APP`
- Platform: Windows-10-10.0.26200-SP0, Python 3.11.15
- driverMode: `thin`
- pythonOracledb: `4.0.2`

Suites: `tests/qualification`, `tests/integration`, `tests/e2e` and `tests/unit`, in
one run. Result: 384 passed, 18 skipped, 0 failed. The skips: 8 checks marked
`stand_in_only` (their Oracle equivalents are in `tests/qualification`), the
two-target isolation check (no second database), 7 Keycloak checks and 1 PostgreSQL
check (not configured for this run), and 1 symlink check the Windows account cannot
create. Nine earlier runs that day are not recorded separately; their failures are
the findings above.

Account setup: `HARNESS_APP` with `harness_developer_role` and
`harness_maintenance_role`, created as `SYSTEM`. `SELECT_CATALOG_ROLE` stood in for
the seven `SYS` view grants in `oracle/grants/harness_roles.sql`, which `SYSTEM` cannot
make. `SYSTEM` was the privileged account for the connection-loss checks.

Database, as reported by the database:

- characterSet: `AL32UTF8`
- containerName: `ORCL`
- currentSchema: `HARNESS_APP`
- currentUser: `HARNESS_APP`
- databaseName: `ORCL`
- driver.driverMode: `thin`
- driver.pythonOracledb: `4.0.2`
- hostName: `90520b67e617`
- instanceName: `ORCL`
- isCdb: `False`
- nationalCharacterSet: `AL16UTF16`
- version: `19.9.1.0.0`
- versionFull: `19.9.1.0.0`

Observed Oracle behaviour:

| Question | What this database did |
| --- | --- |
| BLOB under a 256-byte preview limit | byteLength `20000`, preview 512 hex characters, truncated=`True` |
| Capabilities on this account | connect: available<br>session_schema: available<br>all_objects: available<br>explain_plan: available<br>display_cursor: available<br>v_session: available<br>v_sql: available<br>dba_tablespaces: available<br>dba_scheduler_jobs: available<br>dbms_stats: available<br>compile_objects: available |
| Compiler errors for the seeded invalid body | line 5 col 39: PL/SQL: ORA-00942: table or view does not exist; line 5 col 5: PL/SQL: SQL Statement ignored |
| Database major version | 19c (`19.9.1.0.0`) |
| Does DBMS_OUTPUT reach the adapter? | Yes - 1 line(s): ['headcount=3'] |
| Does DDL commit pending DML? | Yes - the row inserted before the CREATE was visible to another session without an explicit commit. |
| Does ROLLBACK TO SAVEPOINT leave the transaction open? | Adapter reports open=True; the row before the savepoint survived (1 == 1) and the row after it did not (0 == 0). |
| Does a PL/SQL block leave a transaction open? | Yes - the adapter's assumption holds. |
| Does a cancellation roll back earlier work in the same transaction? | No - the earlier insert was still pending, as assumed. |
| Does the blocking panel show a real blocked session? | Session 3513 waiting on 3270: shown as `[3513, 'HARNESS_APP', 'enq: TX - row lock contention', 0]` blocked by SID `3270` |
| EMPTY_CLOB() vs NULL | EMPTY_CLOB() arrived as `{'kind': 'lob', 'preview': '', 'byteLength': 0, 'truncated': False}`; a NULL CLOB as `None`; EMPTY_BLOB() as `{'kind': 'lob', 'preview': '', 'byteLength': 0, 'truncated': False}`. |
| How do TIMESTAMP columns arrive? | TIMESTAMP: `datetime.datetime(2026, 3, 1, 12, 34, 56, 789012)`<br>WITH TIME ZONE: `datetime.datetime(2026, 3, 1, 12, 34, 56, 789012)`<br>WITH LOCAL TIME ZONE: `datetime.datetime(2026, 3, 1, 18, 34, 56, 789012)`<br>Warning: `TIMESTAMP WITH TIME ZONE column(s) TS_TZ are shown without their offset: the driver returns the wall-clock time only. Select them with TO_CHAR(column, 'YYYY-MM-DD HH24:MI:SS.FF TZH:TZM') to see the offset.` |
| How does NUMBER(20,10) arrive? | As `Decimal` with value `1234567890.0123456789` |
| Is DBMS_OUTPUT bounded? | Yes - a 512-byte budget returned 8 line(s) (480 bytes) and set the truncated flag. |
| Is a lost connection during a write reported as outcome_unknown? | Raised `OutcomeUnknownError` with code `outcome_unknown`. |
| Is a session reusable after a cancellation? | Yes - the connection stayed healthy and answered a query afterwards. |
| Is an empty VARCHAR2 stored as NULL? | v_ascii read back as `None`; NVL says `'was-null'`. |
| Large CLOB under a 1 KiB preview limit | reported byteLength `65534`, preview is 1024 characters / 1024 bytes, truncated=`True` |
| Lost connection during COMMIT | Raised `OutcomeUnknownError` with code `outcome_unknown` |
| NCLOB of 4,000 two-byte characters: what is byteLength? | `4000`. 4000 means characters; 8000 means bytes. The field name promises bytes and the driver counted characters. |
| Runtime error from a PL/SQL block | Oracle code `ORA-20001`, message `ORA-20001: qualification probe`<br>`ORA-06512: at line 1` |
| Shape of a small CLOB | `{'kind': 'lob', 'preview': 'short clob', 'byteLength': 10, 'truncated': False}` |
| Unicode read back | `'こんにちは — café — مرحبا'` |
| What does a broken statement raise? | `CancelledError_` with code `execution_cancelled`. Returned after 3.0s of a 30s statement. |
| What does a killed session raise mid-statement? | `OutcomeUnknownError` / harness code `outcome_unknown` |
| What enforces the execution deadline? | A 5s budget stopped a 30s statement after 5.0s, raising `TimeoutError_`. |

### Run 2026-09-11 19:40 UTC

- Harness build: `12f6902` plus the review fixes on PR #7 (`charLength`, the 1,000,000-byte DBMS_OUTPUT buffer, the value-based offset warning, the narrowed identity probe), uncommitted at the time
- Target: the same instance, service `ORCL` as `harness_app`, schema `HARNESS_APP`
- Platform: Windows-10-10.0.26200-SP0, Python 3.11.15
- driverMode: `thin`
- pythonOracledb: `4.0.2`

Suites: the same four, in one run. Result: 398 passed, 18 skipped, 0 failed. The
skips are the same as the previous run's. An earlier run that hour, with the server
buffer scaled to four times the budget, failed one check: a 512-byte budget hit
ENABLE's 2,048-byte floor and a 30 KB block was stopped rather than truncated. That
run is not recorded separately; the fix was the fixed 1,000,000-byte buffer.

Database, as reported by the database:

- characterSet: `AL32UTF8`
- containerName: `ORCL`
- currentSchema: `HARNESS_APP`
- currentUser: `HARNESS_APP`
- databaseName: `ORCL`
- driver.driverMode: `thin`
- driver.pythonOracledb: `4.0.2`
- hostName: `90520b67e617`
- instanceName: `ORCL`
- isCdb: `False`
- nationalCharacterSet: `AL16UTF16`
- version: `19.9.1.0.0`
- versionFull: `19.9.1.0.0`

Observed Oracle behaviour:

| Question | What this database did |
| --- | --- |
| BLOB under a 256-byte preview limit | byteLength `20000`, preview 512 hex characters, truncated=`True` |
| Capabilities on this account | connect: available<br>session_schema: available<br>all_objects: available<br>explain_plan: available<br>display_cursor: available<br>v_session: available<br>v_sql: available<br>dba_tablespaces: available<br>dba_scheduler_jobs: available<br>dbms_stats: available<br>compile_objects: available |
| Compiler errors for the seeded invalid body | line 5 col 39: PL/SQL: ORA-00942: table or view does not exist; line 5 col 5: PL/SQL: SQL Statement ignored |
| Database major version | 19c (`19.9.1.0.0`) |
| Does DBMS_OUTPUT reach the adapter? | Yes - 1 line(s): ['headcount=3'] |
| Does DDL commit pending DML? | Yes - the row inserted before the CREATE was visible to another session without an explicit commit. |
| Does ROLLBACK TO SAVEPOINT leave the transaction open? | Adapter reports open=True; the row before the savepoint survived (1 == 1) and the row after it did not (0 == 0). |
| Does a PL/SQL block leave a transaction open? | Yes - the adapter's assumption holds. |
| Does a cancellation roll back earlier work in the same transaction? | No - the earlier insert was still pending, as assumed. |
| Does the blocking panel show a real blocked session? | Session 2789 waiting on 1938: shown as `[2789, 'HARNESS_APP', 'enq: TX - row lock contention', 0]` blocked by SID `1938` |
| EMPTY_CLOB() vs NULL | EMPTY_CLOB() arrived as `{'kind': 'lob', 'preview': '', 'charLength': 0, 'truncated': False}`; a NULL CLOB as `None`; EMPTY_BLOB() as `{'kind': 'lob', 'preview': '', 'byteLength': 0, 'truncated': False}`. |
| How do TIMESTAMP columns arrive? | TIMESTAMP: `datetime.datetime(2026, 3, 1, 12, 34, 56, 789012)`<br>WITH TIME ZONE: `datetime.datetime(2026, 3, 1, 12, 34, 56, 789012)`<br>WITH LOCAL TIME ZONE: `datetime.datetime(2026, 3, 1, 18, 34, 56, 789012)`<br>Warning: `TIMESTAMP WITH TIME ZONE column(s) TS_TZ are shown without their offset: the driver returns the wall-clock time only. Select them with TO_CHAR(column, 'YYYY-MM-DD HH24:MI:SS.FF TZH:TZM') to see the offset.` |
| How does NUMBER(20,10) arrive? | As `Decimal` with value `1234567890.0123456789` |
| Is DBMS_OUTPUT bounded? | Yes - a 512-byte budget returned 8 line(s) (480 bytes) and set the truncated flag. |
| Is a lost connection during a write reported as outcome_unknown? | Raised `OutcomeUnknownError` with code `outcome_unknown`. |
| Is a session reusable after a cancellation? | Yes - the connection stayed healthy and answered a query afterwards. |
| Is an empty VARCHAR2 stored as NULL? | v_ascii read back as `None`; NVL says `'was-null'`. |
| Large CLOB under a 1 KiB preview limit | reported charLength `65534`, preview is 1024 characters / 1024 bytes, truncated=`True` |
| Lost connection during COMMIT | Raised `OutcomeUnknownError` with code `outcome_unknown` |
| NCLOB of 4,000 two-byte characters: reported length | charLength `4000`, byteLength `None` |
| Runtime error from a PL/SQL block | Oracle code `ORA-20001`, message `ORA-20001: qualification probe<br>ORA-06512: at line 1` |
| Shape of a small CLOB | `{'kind': 'lob', 'preview': 'short clob', 'charLength': 10, 'truncated': False}` |
| Unicode read back | `'こんにちは — café — مرحبا'` |
| What does a broken statement raise? | `CancelledError_` with code `execution_cancelled`. Returned after 3.0s of a 30s statement. |
| What does a killed session raise mid-statement? | `OutcomeUnknownError` / harness code `outcome_unknown` |
| What enforces the execution deadline? | A 5s budget stopped a 30s statement after 5.0s, raising `TimeoutError_`. |
| What happens when a block fills the DBMS_OUTPUT server buffer? | Stopped with `ORA-20000`; the next block's output was `['after']`. |
