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
| **Oracle Database** | **19c Enterprise Edition 19.9.1.0.0, non-CDB, AL32UTF8** | `tests/qualification` (47 checks), `tests/integration` and `tests/e2e` against one instance, two recorded runs on 2026-09-11 (Windows). Repeated on 2026-10-14 (Linux) against a second 19.9.1.0.0 non-CDB, in thin and thick mode, with a restricted account, the K-4 catalog and process death. See [Recording a run](#recording-a-run) |
| **Oracle Database** | **Oracle AI Database 26ai Free 23.26.3.0.0, PDB `FREEPDB1` in a CDB, AL32UTF8** | The same three suites, thin mode, one recorded run on 2026-10-14 (Linux), including the PDB container identity, the restricted account, the K-4 catalog and process death |
| **Oracle Database** | **19c Enterprise Edition 19.9.1.0.0, non-CDB, WE8MSWIN1252, on a remote host** | The whole test suite in thin mode, one recorded run on 2026-10-14 (Linux client, database across a real network), including the K-4 catalog and process death. Provisioned without `SYS`; the `SYS` grants were added later the same day, and the restricted account is now claimed. See "The D4 run, 2026-10-14" |
| **Oracle Database** | **Autonomous Database 26ai (Always Free) 23.26.4.1.0, a PDB, AL32UTF8, over TCPS with its wallet** | The same three suites in thin mode and in thick mode with Instant Client 21.4, recorded runs on 2026-10-14 (Linux client, Oracle Cloud), with D1 as the second target, the admin account for the kills, the K-4 catalog and process death. A shared database, so no restricted or lab account. See "The D3 run, 2026-10-14" and "Thick mode over TCPS, and VECTOR binds, 2026-10-14" |
| python-oracledb | 4.0.2, thin mode | The runs above |
| python-oracledb | 4.0.2, thick mode, Oracle Instant Client 21.4.0.0.0 | The same three suites against the 19.9 non-CDB, the 23.26 Free PDB, D4 (19.9, `WE8MSWIN1252`, remote) and D3 (Autonomous Database 23.26.4.1.0 over TCPS), recorded runs on 2026-10-14 (Linux). Against 23.26 this client predates `BOOLEAN` and `VECTOR`; see "Thick mode against 23.26, 2026-10-14" |
| Copilot provider | Fixture provider | Copilot test suite |
| Protocol | 1.0 | Contract tests, adapter tests |
| Identity provider | Keycloak 26.3, local container | `tests/identity` and `apps/web/src/oidc.keycloak.test.ts`, in CI. See [Identity providers](#identity-providers) |

## What has not been tested

| Component | Status |
| --- | --- |
| **Oracle beyond two local instances** | Two instances have been qualified on 2026-10-14: a 19.9 non-CDB and a 23.26 Free PDB, each as the other's second target, so the two-target isolation check has run in both directions. A third, D4, was qualified the same day: a 19.9 non-CDB in `WE8MSWIN1252` on a remote host, with D1 as its second target. Not run: a later 19c release update, a PDB on 19c, `CDB$ROOT`, RAC, Standard Edition, and character sets other than AL32UTF8 and WE8MSWIN1252. The restricted account was the README's shape only (`CREATE SESSION` plus the two tablespace views); other partial grant sets have not been probed. |
| **Restart after process death, on Oracle** | Run on 2026-10-14 against 19.9 (thin and thick) and 23.26 (thin), on a Linux host: on both releases the dead session left `V$SESSION` with its row lock freed at once, an applied but uncommitted write was rolled back, and both `COMMIT` cases were reconciled correctly. See the process-death entries under [Recording a run](#recording-a-run). Not run on Windows, where the 2026-09-11 runs were. A host that loses power or network, rather than a killed process, is not covered at all. |
| **Kiwi's K-4 lookups on Oracle** | Run on 2026-10-14 against 19.9 and 23.26: every entry returned rows of the shape the stand-in returns, and PL/Scope absence was reported for the unit compiled without it. Needs the extra grants in `oracle/qualification/README.md` (`CREATE TRIGGER`, `CREATE JOB` and the rules-engine privileges for chains). No database link exists in either fixture, so `db_links_referenced` was checked for shape with no rows. |
| Oracle 23ai/26ai beyond two databases | One Free 23.26.3.0.0 PDB and one Autonomous Database 23.26.4.1.0, each in thin mode and in thick mode with Instant Client 21.4. On both, `BOOLEAN` and `VECTOR` were checked as selected values, a `BOOLEAN` bind, `FLOAT32` and `INT8` vector binds into `VECTOR` columns, `VECTOR_DISTANCE` with a bound vector and an IVF vector index with an approximate search. An HNSW index was created on Autonomous Database only: the Free PDB has no vector memory pool (`ORA-51962`). Not checked: `BINARY` and `SPARSE` binds, hybrid and partitioned vector indexes, and `VECTOR` in PL/SQL. See "BOOLEAN and VECTOR, 2026-10-14" and "Thick mode over TCPS, and VECTOR binds, 2026-10-14" |
| python-oracledb thick mode beyond one client | Instant Client 21.4 on Linux against 19.9 (D1 and D4) and 23.26 (the Free PDB, and Autonomous Database over TCPS). Other client versions (23.4 or later in particular, which knows `BOOLEAN` and `VECTOR`) and Windows are not run. |
| python-oracledb 26.0.0 | Tried on 2026-09-11 against the qualified 19c instance, for the `call_timeout` crash below only. **It does not fix it:** assigning `call_timeout` on a session dropped with `DPY-4011` still ends the process with `0xC0000005`, in every attempt that dropped the session (13 of 13 in one batch, and every later attempt). On the dropped session it otherwise behaves like 4.0.2: reading `call_timeout`, `close()`, `cursor.close()` and `is_healthy()` are safe; `ping()` and `rollback()` raise `DPY-1001`. Repeated on 2026-10-14 on Linux (Python 3.11) against 19.9, with SYSTEM killing the session either idle or during a call: the setter ends the process with `SIGSEGV` in 13 of 13 attempts per case on **both 26.0.0 and 4.0.2**, so the crash is in the thin driver, not specific to Windows. Controls stayed clean (0 of 13): the setter on a live session, and every other call on the dropped session. A session killed server-side but not yet noticed by the driver reports `is_healthy()` true and takes the setter safely; `is_healthy()` turns false on the first call that hits `DPY-4011`, which is exactly when the setter becomes fatal, so the adapter's guard holds. The suites have not been run on 26.0.0, and it fixes nothing here, so 4.0.2 stays pinned. Reported upstream as [oracle/python-oracledb#619](https://github.com/oracle/python-oracledb/issues/619) on 2026-10-14. |
| TCPS and wallets beyond one | One TCPS path, to Autonomous Database with its downloaded wallet. Thin mode opens its `ewallet.pem` with the wallet password; thick mode opens its auto-login `cwallet.sso` and cannot take a password per connection, so in thick mode a wallet must have one. Not run: a self-signed or private CA wallet, mutual TLS to an on-premises listener, and a wallet that holds only `cwallet.sso` in thin mode. |
| PostgreSQL as the metadata store | The schema is portable SQLAlchemy and the container is configured, but the suite runs on SQLite. |
| OIDC providers other than Keycloak | Entra ID, Okta and Auth0 are documented in docs/setup.md but have not been signed in against. Nor has the pilot's own provider registration. |
| Sign-in in a real browser | The console's sign-in code has run against Keycloak under Node, and the token endpoint's CORS answer has been checked. `tests/browser` now drives Chromium through the console's sign-in, callback, API access, sign-out and expiry: its fixture mode runs in the CI `identity` job against the Keycloak realm (first passed on pull request #16, CI run 34788083498: 24 checks passed, 2 observed), and a rehearsal against a local stand-in provider has passed. **No browser run against the pilot's registration, deployed origin or proxy has happened**, and a fixture run cannot stand in for one. See tests/identity/README.md, "Browser qualification". |
| A real model provider | The Anthropic adapter is written against the current Messages API; no call has been made. |
| OracleDataForge | Not integrated. See `integrations/dataforge/COMPATIBILITY.md`. |
| Load | The pilot target is ten concurrent users across three databases. **Rehearsed, not qualified.** On 2026-10-14 `python -m tests.capacity` ran ten users against two local Oracle targets (D1 19.9 and D2 23.26 FREEPDB1), with the API, the load client and both databases on one workstation: every correctness check passed, with no errors under saturation. See "Capacity rehearsal, 2026-10-14 (T-5)" below. Three databases, a separate API host and agreed thresholds are still missing; the thresholds in the example workload are proposals nobody has agreed. |

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

The runs recorded below support claims about those configurations only: 19c 19.9 as
a non-CDB in thin and thick mode, and 23.26 Free as a PDB in thin mode, each with
python-oracledb 4.0.2. The gaps listed under "What has not been tested" still stand,
and no broader claim about Oracle support is supportable until they are run and
recorded too.

## What the first Oracle 19c run found

The first run, on 2026-09-11, was the first time the harness met Oracle. It failed
before any test ran, and every failure below was fixed and re-run until the whole
suite passed. Each has a regression test that fails without the fix.

**Defects in the harness**, each invisible to the stand-in:

| Defect | Effect before the fix | Fix |
| --- | --- | --- |
| **A statement deadline could crash the API process.** When the driver cannot deliver a call timeout as a break, python-oracledb 4.0.2 (thin) drops the session with `DPY-4011`, and assigning `call_timeout` on that session crashes the process with an access violation. The adapter reset `call_timeout` after every statement. | One statement past its deadline, inside an open transaction, ended the API for every user. Reproduced in 6 of 6 attempts under those conditions, and still present in 26.0.0. On Linux the setter crashes the same way (`SIGSEGV`, 13 of 13 on 4.0.2 and 26.0.0; see "What has not been tested"). | `call_timeout` is only touched on a session `is_healthy()` reports alive, and a session that is not is retired before anything else touches it. Measured on a dropped session: `close()`, `cursor.close()` and `is_healthy()` are safe; `ping()` and `rollback()` raise `DPY-1001`; only the `call_timeout` setter crashes. |
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

## What the 2026-10-14 runs found

The plan is `DB_TEST_PLAN.md`; runs R1 to R4 there. They ran on a Linux host against
two local containers: D1, 19c 19.9.1.0.0 non-CDB at `localhost:1522/ORCL`, and D2,
26ai Free 23.26.3.0.0 at `localhost:1521/FREEPDB1`. `HARNESS_APP` and
`HARNESS_RESTRICTED` were created on both, with the roles from
`oracle/grants/harness_roles.sql` granted as `SYS` (inside the PDB on D2). `SYSTEM`
was the privileged account. Runs went through `scripts/qual_run.sh`.

No harness defect was found. Every product behaviour checked on 19.9 held on 23.26
too, including the ones the 2026-09-11 findings were about: `NUMBER` as `Decimal`,
`DPY-4024` as `execution_timeout`, the DBMS_OUTPUT buffer, LOB lengths, `isCdb` from
`CON_ID` (now confirmed on a real PDB: `isCdb` true, container 3, `FREEPDB1`) and the
session serial number. The restricted account probed as restricted on both, and every
capability it lacked was reported unavailable with Oracle's reason, rather than as an
error or an empty result.

| Finding | Kind | Effect | Fix |
| --- | --- | --- | --- |
| Fixture teardown failed with `ORA-23308` on a schema that had never held the scheduler chains | Fixture defect, already known | The first R1 attempt, on `ed60218`, errored in every test's setup. | The fix in pull request #20 (the generated drop tolerates `ORA-23308`). Every later run used `fa5eb09`, which includes it. Pull request #20 is not merged yet |
| `ORA-01031` creating the K-4 trigger fixture | Environment | The second R1 attempt errored in setup. Provisioning had granted `harness_roles.sql` but not the qualification-only grants the K-4 fixtures need. | `CREATE TRIGGER`, `CREATE JOB` and the rules-engine privileges for chains granted to `HARNESS_APP` on both databases, as `oracle/qualification/README.md` lists. Added to the provisioning step in `DB_TEST_PLAN.md` |
| The e2e workflow asserted the database version started with `19.` | Test defect | Failed on 23.26, which is a correct answer. | `tests/e2e/test_mvp_workflows.py` checks the five-part version format and a major of 19 or later |
| The lost-connection-during-a-write check raced the kill against the update | Test defect | On 23.26 the 400,000-row update finished before the session was killed; the check then saw a dropped session on rollback (`DPY-4011`) instead of a lost write. It passed on 19.9 only because the update was slower. | `tests/qualification/test_connection_loss.py` holds a row lock from a second session, so the update is waiting inside the database when the kill lands, and tolerates a rollback on a session that is already gone |

The two test fixes were uncommitted during the runs; R2, R3 and R4 used them, and the
connection-loss check was rerun on D1 with them. The fixed tests are themselves the
regression checks; neither needs a separate unit check, and both now pass on 19.9 and
23.26.

Observed differences, none of them a harness problem: 23.26 names the missing object
in `ORA-00942` text where 19.9 does not; thin mode appends a `Help:` link to Oracle
error text where thick mode does not; and 23.26 is far faster on the 400,000-row
update the connection-loss check uses.

Still open after the 2026-10-14 runs: TCPS and wallets (R5, which needs Autonomous
Database and has not been approved), and everything listed under "Still open after the 2026-09-11 run".

### BOOLEAN and VECTOR, 2026-10-14

The SQL types 23ai added, checked on D2 (23.26.3.0.0, thin) by
`tests/qualification/test_23ai_types.py` and, through a worksheet, by
`tests/integration/test_23ai_types.py`. Both select literals from `DUAL`, so they need
no fixture objects. Before 23ai they skip with the reason; on D1 (19.9) all six did.

`BOOLEAN` needed nothing. It arrives as a Python `bool` (and `NULL` as `None`), so the
grid gets JSON `true`, `false` or `null`, and a JSON `true` or `false` binds as
`BOOLEAN`.

`VECTOR` found a harness defect. python-oracledb returns a dense `VECTOR` as an
`array.array` and a `SPARSE` one as a `SparseVector`. `_shape` passed both through, and
the API could not serialise them, so any query that returned a vector failed with
`PydanticSerializationError` instead of reaching the grid. With the fix removed, the
new checks fail that way on D2.

| Finding | Kind | Effect | Fix |
| --- | --- | --- | --- |
| A `VECTOR` value could not be serialised | Harness defect | Every query returning a `VECTOR` column failed in the API | `_shape` returns `{"kind": "vector", "format", "dimensions", "values", "truncated"}`, bounded by the LOB preview budget at 8 bytes per element (a `VECTOR` can have 65,535 dimensions). `BINARY` reports its dimensions in bits and a hex preview; `SPARSE` adds `sparse`, `storedCount` and the stored `indices`. `FLOAT32` elements are given as the shortest decimal that rounds to the same float32, so 0.1 is not shown as 0.10000000149011612. The console renders the shape. Pinned by unit tests on `_shape` |

Binding a `VECTOR`, `VECTOR` columns in a table, `VECTOR_DISTANCE` and vector indexes
were checked later the same day; see "Thick mode over TCPS, and VECTOR binds,
2026-10-14". Thick mode with an older client is covered in the next section.

### Thick mode against 23.26, 2026-10-14

R3's configuration pointed at D2: thick mode, Instant Client 21.4, the three suites.
The first run failed the six `BOOLEAN` and `VECTOR` checks. Everything else passed.

Instant Client 21.4 predates both types, so the server converts them for it. A
`BOOLEAN` is described as `NUMBER` and arrives as `1`, `0` or `NULL`. A bound Python
`bool` goes as a `NUMBER` too, so `CASE WHEN :value` fails with `ORA-00920`. A
`VECTOR` is described as a `CLOB` but arrives as a plain Python list:

- its format is lost;
- a `BINARY` vector arrives as its bytes;
- a `SPARSE` vector arrives with every dimension filled in;
- `FLOAT32` 0.1 arrives as 0.100000001.

This is how that client works, not a harness defect. Two things in the harness did
need changing, though. The list went to the grid whole, bypassing the preview limit
that bounds a vector in thin mode, and nothing told the user why the values looked
wrong.

| Finding | Kind | Effect | Fix |
| --- | --- | --- | --- |
| An Oracle Client older than 23 receives `BOOLEAN` and `VECTOR` converted | Expected client difference | `BOOLEAN` shows as 1 or 0 and cannot be bound; a vector loses its format and, if `SPARSE`, its indices | None in the harness; use thin mode or Oracle Client 23.4 or later |
| A vector from that client was not bounded | Harness defect | A 65,535-dimension vector went to the grid in full | `_fetch_bounded` shapes a list in a `CLOB` or `NCLOB` column as `{"kind": "vector", "format": null, "length", "values", "truncated"}`, under the same preview limit. The console shows "N elements" |
| Nothing explained the converted values | Missing warning | A user saw 1 and 0, or a vector with no format, and no reason | The statement warns, naming the columns and the Oracle Client version |

`tests/qualification/test_23ai_types.py` and `tests/integration/test_23ai_types.py`
now check the old client's behaviour exactly: the 1/0 values, the `ORA-00920` on a
bind, the expanded `SPARSE` vector and the warning. A client that changes any of them
fails a check. The rerun had 162 passed, 17 skipped and 0 failed. The skips are 13
`stand_in_only` tests and 4 PostgreSQL store tests.

The same suites in thick mode against D4 (remote host, see `databases/INVENTORY.md`;
19.9, `WE8MSWIN1252`) had 155 passed, 24 skipped and 0 failed in 23 minutes. The six
`BOOLEAN` and `VECTOR` checks skip on 19.9, and the lab checks skip because the profile
names no lab account. Every evidence row matches thin R6, including the
`WE8MSWIN1252` Unicode notes and the cancellation and deadline timings.

With these changes the whole test suite was rerun against D4 in thin mode (R6): 790
passed, 0 failed and 41 skipped, in 46 minutes. Each skip names its reason: 16
`stand_in_only` tests, 11 PostgreSQL store tests, 7 Keycloak tests, 6 23ai type checks
and the lab checks.

### The D4 run, 2026-10-14

D4 is a 19c 19.9.1.0.0 non-CDB in `WE8MSWIN1252` on a remote host, the first target
that is neither AL32UTF8 nor local. `SYS` was not available when it was provisioned, so
it was provisioned as `SYSTEM` (see `DB_TEST_PLAN.md`): the restricted account could not
read the tablespace views, and `HARNESS_APP` already held `SELECT_CATALOG_ROLE`. The
`SYS` grants were added afterwards; see "D4 with the `SYS` grants" below. The run used
D1 as its second target (run R6 in the plan), thin mode and
`HARNESS_QUAL_REQUIRE=admin,second_target`, and
ran the whole test suite rather than the three Oracle suites alone.

Result: 779 passed, 4 failed, 33 skipped, in 45 minutes against R1's 100 seconds.
Every test pays the network round trip, but no deadline or timeout check failed. No
harness defect was found, and nothing failed because of the character set: the bind,
LOB, PL/SQL, source and Unicode read-back checks all passed. The Unicode lookup by
bind is skipped with its reason, as `WE8MSWIN1252` cannot carry the sample characters.
Process death on a remote database cleaned up the dead session in 0.4 to 0.5 s,
against 0.0 s locally, with the same outcomes.

| Finding | Kind | Effect | Fix |
| --- | --- | --- | --- |
| Three Kiwi catalog tests in `tests/copilot` read objects only the stand-in seeds: a reference from `EMPLOYEE_REPORT` to `EMPLOYEES`, the `HARNESS_REMOTE_ORDERS` link and a run log written to look like instructions | Test defect | They failed on D4 and would on any Oracle target; R1 to R3 never ran `tests/copilot`. The Oracle versions in `tests/qualification/test_kiwi_catalog.py` passed | Marked `stand_in_only`, whose meaning now includes objects only the stand-in seeds |
| The two-target isolation check compared database names, and D4 and D1 are both `ORCL` | Test defect, exposed by the environment | It failed before reaching the isolation check. It passed in R1 to R3 only because the other database was `FREEPDB1` | It compares database name, host and container together |

Rerun of the two changed files on D4: 23 passed, 4 skipped. The commit-window check
was inconclusive on this run (the commit finished before the kill landed); it passed
on D1 and D2.

### D4 with the `SYS` grants, 2026-10-14

The server's administrator ran the grants `SYSTEM` could not: the seven `SYS` view
grants to `HARNESS_DIAGNOSTICS_ROLE` from `oracle/grants/harness_roles.sql`, and
`DBA_TABLESPACES` and `DBA_TABLESPACE_USAGE_METRICS` to `HARNESS_RESTRICTED`, the shape in
`oracle/qualification/README.md`. All nine were checked as `SYSTEM` in `DBA_TAB_PRIVS`,
and again after the server rebooted. `HARNESS_APP` keeps `SELECT_CATALOG_ROLE`, so it
still reads more of the dictionary than the script intends.

The restricted account now probes as the README describes. `DBA_TABLESPACES` is
available, so the tablespaces panel works. `V$SESSION`, `V$SQL`, `DBMS_XPLAN.DISPLAY_CURSOR`
and the scheduler views fail with `ORA-00942`, and the sessions, blocking and scheduler
panels report `capability_unavailable` naming the missing view. Before the grants the
tablespaces panel was unavailable too. Both restricted-account checks passed, and D4 runs
now claim `restricted_account`.

The three Oracle suites were then rerun in thin mode with `restricted_account` required:
824 passed, 45 skipped and 0 failed in 48 minutes. No finding.

### The D3 run, 2026-10-14

D3 is an Autonomous Database 26ai Always Free instance (23.26.4.1.0, a PDB, AL32UTF8) in
Oracle Cloud, reached over TCPS with its downloaded wallet: the first TCPS path, the first
wallet and the first managed database. It is shared with other projects, so `HARNESS_APP`
was created there for this run (default tablespace `DATA`) with the roles from
`oracle/grants/harness_roles.sql` and the K-4 extras, all granted by `ADMIN`.
**Deliberately not granted:** `ANALYZE ANY` and `ALTER ANY PROCEDURE`, which reach into
the other projects' schemas; no check needed them. `HARNESS_APP` sees no tables in the
other schemas. No restricted or lab account was created. The run used D1 as its second
target (R5 in the plan), thin mode, `ADMIN` as the admin account and
`HARNESS_QUAL_REQUIRE=admin,second_target`.

R5 could not start before three harness defects were fixed:

| Finding | Kind | Effect | Fix |
| --- | --- | --- | --- |
| No wallet password anywhere: not in the test configuration, not in a connection profile. An ADB wallet's `ewallet.pem` holds an encrypted key | Harness defect | Thin mode could not open the wallet, so no ADB target could be registered or tested | `HARNESS_QUAL_WALLET_PASSWORD_FILE`; a profile's `walletSecretReference` names a credential reference for it (`connection_profiles.wallet_secret_reference_id`, metadata schema 5) |
| One protocol and wallet for every endpoint in a test run | Harness defect | A TCPS primary could not be paired with a TCP second database | Each endpoint has its own transport. `HARNESS_QUAL_SECOND_PROTOCOL`, `_SECOND_WALLET_DIR` and `_SECOND_WALLET_PASSWORD_FILE` describe the second; the admin, restricted and lab accounts share the primary's |
| The seed and the e2e registration fixture never passed protocol or wallet to the API's targets | Test-harness defect | The integration and e2e suites would have connected over TCP to a TCPS listener | `TargetEndpoint` and `spare_endpoint` carry them |

The first full attempt then failed 2 of 180 checks. The e2e registration workflow was
the third row above. The other was the blocking panel:

| Finding | Kind | Effect | Fix |
| --- | --- | --- | --- |
| On ADB, `V$SESSION.BLOCKING_SESSION` reads NULL, with `BLOCKING_SESSION_STATUS` `UNKNOWN`, for the first seconds of a row-lock wait, then names the holder. Measured 5.9 s in the harness runs and 11 to 15 s in four separate probes. On D1, D2 and D4 the check passed within its 15 s wait | Environment behaviour | `dba.blocking` shows a block only after that delay, so the check's 15 s wait was too short | The check waits up to 25 s, under the waiter's 30 s deadline, and records the delay. The query is unchanged: it is correct, only late. An operator looking at a fresh block on ADB may not see it for up to about 15 s |

Result after the fixes: 160 passed, 20 skipped, 0 failed, in 262 s.
`BOOLEAN` arrived as `bool` and `VECTOR` (dense, sparse, wide) exactly as
on the Free PDB. Process death cleaned up the dead session at once (0.0 to 0.1 s), with
the same outcomes as on D1. The commit-window kill landed this time and raised
`outcome_unknown`. The two-target isolation check passed between ADB and D1.

### Thick mode over TCPS, and VECTOR binds, 2026-10-14

R5's configuration in thick mode: D3 over TCPS with its wallet, Instant Client 21.4, D1
as the second target. It could not connect at first. python-oracledb's `config_dir` and
`wallet_location` connect parameters are read by thin mode only; the Oracle Client
looked for a wallet in its own configuration and failed with `ORA-28759: failure to
open file`. It does read `MY_WALLET_DIRECTORY` from the connect descriptor. It cannot
be given a wallet password per connection, so it opens the wallet's auto-login
`cwallet.sso`, which an ADB wallet download includes.

| Finding | Kind | Effect | Fix |
| --- | --- | --- | --- |
| Thick mode ignored the profile's wallet | Harness defect | No TCPS target could be reached in thick mode (`ORA-28759`) | In thick mode a TCPS descriptor carries `(SECURITY=(MY_WALLET_DIRECTORY="<wallet dir>"))`. Thin mode keeps the connect parameters: it needs the password for the encrypted `ewallet.pem`, and with the descriptor clause alone OpenSSL prompts for a pass phrase and the connection fails. A wallet directory with a parenthesis, equals sign or quote is refused rather than written into the descriptor |

Result: 159 passed, 21 skipped, 0 failed, in 320 s. Every evidence row matches thick
mode on D2: `BOOLEAN` arrives as 1 or 0 and binds as `NUMBER` (`ORA-00920`), and a
vector arrives as a list without its format, bounded and with the warning naming the
client. The one extra skip against thin R5 is the commit-window kill, which did not
land inside the window on this run. Process death: 6 passed, the dead session cleaned
up at once (0.0 to 0.1 s).

The `VECTOR` checks left open by "BOOLEAN and VECTOR, 2026-10-14" then ran on D2 and
D3, each in thin and thick mode, in `HARNESS_APP`. They found that a vector could not
be bound at all:

| Finding | Kind | Effect | Fix |
| --- | --- | --- | --- |
| A JSON array arrives in the worker as a Python list, which python-oracledb binds as a PL/SQL array. The bind's `typeHint` was carried from the API to the worker and never read | Harness defect | Any SQL with a vector bind failed with `ORA-01484: arrays can only be bound to PL/SQL statements`; only vector text in the statement or a text bind worked | A bind with `typeHint` `vector` (`FLOAT32`), `vector:float64` or `vector:int8` is checked (a non-empty array of finite numbers, integers from -128 to 127 for `INT8`; otherwise 400) and bound as an `array.array` of that format. `FLOAT32` is the default because it is what Oracle makes of vector text, and `VECTOR_DISTANCE` refuses vectors of different formats (`ORA-51812`, measured with a `FLOAT64` bind against a `FLOAT32` column). An unhinted array is unchanged |
| An Oracle Client older than 23.4 cannot bind a vector (`DPI-1050`) | Expected client difference | As above, in thick mode with Instant Client 21.4 | The vector is sent as text, which the server converts, and the statement warns, naming the binds and the client. Text over 32,767 bytes is refused (`ORA-01461`), so a vector of more than about 3,000 dimensions needs thin mode or a newer client |
| A bound vector would read back through the out-bind values as an `array.array` | Harness defect, found while fixing the bind | The API could not have serialised the response | Out-bind values that are arrays are returned as lists |

`tests/qualification/test_23ai_types.py` binds `FLOAT32` and `INT8` vectors into a
table, searches it with `VECTOR_DISTANCE` and a bound vector, checks that an unhinted
array still fails with `ORA-01484`, and builds IVF and HNSW indexes.
`tests/integration/test_23ai_types.py` does the same through a worksheet. All passed on
D2 and D3 in both modes. HNSW was created on D3 and recorded as a gap on D2, whose Free
PDB has no vector memory pool (`ORA-51962`). In thin mode the `INT8` column reads back
as `INT8`; in thick mode with the old client the binds went as text with the warning
and the values were right.

The stand-in has no `VECTOR` type, so a vector bind there fails with
`capability_unavailable`. The console's worksheet offers the hint as a type on each bind
(`auto`, or a vector of `FLOAT32`, `FLOAT64` or `INT8`). A vector bind's value is a JSON
array of numbers, and the console refuses anything else before sending the statement. An
empty value binds null.

### Lab schemas, 2026-10-14 (T-5)

`tests/qualification/test_lab_workloads.py` registers a lab schema as a development
target through a read-only account (`HARNESS_LAB`: the diagnostics role, which carries
`CREATE SESSION`, and `SELECT` on the lab owner's objects). It then walks every DBA panel, pages the
schema browser, opens every browsable object, explains a join and opens a cached query's
plan. It writes nothing. It ran on `SH` (D1, 102 objects, `SALES` close to a million
rows over 28 partitions) and on `SALES_LAB` (D2, 19 objects).

| Finding | Kind | Effect | Fix |
| --- | --- | --- | --- |
| Paging the schema browser over a partitioned table repeated some rows and dropped others | Harness defect | On `SH`, five pages of 25 returned 102 rows but only 48 distinct ones. `list_objects.sql` did not select `SUBOBJECT_NAME`, so every partition of a table looked identical. It also ordered by type and name only, which is not a total order, so Oracle was free to order the ties differently on each page. | `list_objects.sql` selects `SUBOBJECT_NAME` (as the last column, because the web UI reads the first three by position) and orders by type, name, subobject and `OBJECT_ID`. The stand-in carries both columns. `tests/integration/test_targets_and_access.py::test_paging_objects_lists_every_partition_exactly_once` pages 12 partitions in pages of 5; it fails without the fix |
| The harness's own `EXPLAIN PLAN` statements show up in cursor search, and have no plan | Observation | A text filter on the schema name finds the explain the workbench just ran. Its detail shows an empty plan, as Oracle keeps no `V$SQL_PLAN` rows for `EXPLAIN PLAN` or for a child with a zero plan hash. | None in the harness. The test opens a query cursor that has a plan hash, and fails if that one comes back empty. Filtering the harness's own statements out of search is a possible improvement |

Every panel answered inside the default deadline. Each page stayed within its limit,
and nothing reported unavailable, because the probe found no capability missing for
this account.

| Schema | Calls | Total | Slowest |
| --- | --- | --- | --- |
| `SH` on 19.9 | 28 | 4.2 s | schemas 0.25 s, DBA overview 0.23 s, detail of `CAL_MONTH_SALES_MV` 0.20 s (0.74 s on a cold first run) |
| `SALES_LAB` on 23.26 | 13 | 2.3 s | schemas 0.32 s, DBA overview 0.24 s, detail of `LLM_SALES_MONTHLY_REPORT` 0.23 s |

### Capacity rehearsal, 2026-10-14 (T-5)

`python -m tests.capacity` ran the example workload (10 users, seed 20260913: 20%
metadata, 45% bounded reads, 28% transactions, 7% cancellations) on the stand-in and
on Oracle. The Oracle run used two targets, D1 (19.9) and D2 (23.26, `FREEPDB1`), each
with the marker table in `HARNESS_APP` only. The API ran in thin mode, with the SQLite
metadata store and declared limits of 8 workers and 30 requests in flight. The API, the
load client and the databases shared one workstation. The marker tables were dropped
after the run. The full reports are in `databases/reports/2026-10-14-capacity-*.md`.

| Steady phase, client p50 / p95 ms | Stand-in (`cap-d0b4053e1210`) | Oracle (`cap-cece4ec67213`) |
| --- | --- | --- |
| metadata | 15 / 43 | 404 / 509 |
| boundedRead | 14 / 40 | 56 / 71 (database p95 44) |
| transaction | 23 / 63 | 33 / 62 (database p95 2) |
| cancellation | 164 / 176 | 168 / 174 |
| Application overhead p95 (client minus database) | 49 | 53 |
| Saturation error rate | 3.26% | 0.00% |
| Recovery after saturation | 0 s | 0 s |
| Commits / rollbacks / cross-user probes / contamination | 420 / 94 / 128 / 0 | 464 / 87 / 137 / 0 |

On Oracle the overhead the harness adds is about the same as on the stand-in, close to
50 ms at p95. Metadata panels are the slowest requests, at about 0.5 s p95. Under
saturation (two streams per user, no think time), client p95 rose to about 1.4 s for
metadata and about 0.8 s for the rest, and nothing failed. The rehearsal is SOUND.
It is not a pilot qualification: it had two targets, not three, everything ran on one
host, and the thresholds are proposed.

| Finding | Kind | Effect | Fix |
| --- | --- | --- | --- |
| A rolled-back marker stayed visible on the stand-in (run `cap-2ba157bb8e52`) | Stand-in defect | SQLite rolls back the whole transaction when it interrupts a write. The stand-in kept the transaction flag set while later writes autocommitted, so a rollback the user asked for kept them. | `backend/fake.py` resyncs the flag from SQLite after an error, says so in the cancellation error (`transactionRolledBack`), and rolls back only what is still open. `tests/unit/test_fake_cancellation.py` pins it. The rerun found no contamination |
| HTTP 500s and "database is locked" under saturation on the stand-in | Local limit | SQLite allows one writer, so the metadata store and the stand-in's data both lock under saturation (3 to 9% errors). The Oracle run, which still uses a SQLite metadata store, had none. | None. Production uses PostgreSQL. Answering a lock with a typed, retryable error instead of a 500 is a possible improvement |
| The Oracle backend did not report database time (run `cap-ad908054428a`) | Harness defect | `database_elapsed_ms` was never set, so the overhead criterion read "not measured" on Oracle. | `backend/oracle.py` times the execute and the bounded fetch. `tests/unit/test_oracle_adapter.py::test_execution_reports_the_time_spent_in_the_database` pins it. The rerun measured 53 ms |

## Recording a run

Append; do not replace. Each entry should carry the date, the harness build, the exact
database version and configuration, which suites were run, and what failed.

Copy entries from the report under `databases/reports`, which is not committed, and
redact what identifies a database reached over a network: its host and service name
(written as "D3 (Autonomous Database, see `databases/INVENTORY.md`)" and so on), and
for a managed database also the PDB name, which Autonomous Database builds the service
name from, and the DBIDs. The unredacted report stays under `databases/reports`.

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

### Run 2026-10-14 06:02 UTC

- Harness build: `fa5eb09` plus the qualification grants. The connection-loss fix came later; that check was rerun with it at 06:07 and passed
- Target: `localhost:1522/ORCL` as `harness_app`, schema `HARNESS_APP`
- Platform: Linux-7.0.0-38-generic-x86_64-with-glibc2.43, Python 3.11.16
- driverMode: `thin`
- pythonOracledb: `4.0.2`


Run R1: D1 primary, D2 second target, thin. Suites: `tests/qualification`, `tests/integration`
and `tests/e2e`, with `HARNESS_QUAL_REQUIRE=admin,second_target,restricted_account`.
Result: 155 passed, 16 skipped, 0 failed. Skips: 12 checks marked `stand_in_only` (their Oracle equivalents are in `tests/qualification`) and 4 PostgreSQL checks (not configured for this run).

Database, as reported by the database:

- characterSet: `AL32UTF8`
- containerId: `0`
- containerName: `ORCL`
- currentSchema: `HARNESS_APP`
- currentUser: `HARNESS_APP`
- databaseName: `ORCL`
- dbUniqueName: `ORCL`
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
| Capabilities on the restricted account | connect: available<br>session_schema: available<br>all_objects: available<br>explain_plan: available<br>display_cursor: unavailable (ORA-00942: table or view does not exist<br>Help: https://docs.oracle.com/error-help/db/ora-00942/)<br>v_session: unavailable (ORA-00942: table or view does not exist<br>Help: https://docs.oracle.com/error-help/db/ora-00942/)<br>v_sql: unavailable (ORA-00942: table or view does not exist<br>Help: https://docs.oracle.com/error-help/db/ora-00942/)<br>dba_tablespaces: available<br>dba_scheduler_jobs: unavailable (ORA-00942: table or view does not exist<br>Help: https://docs.oracle.com/error-help/db/ora-00942/)<br>dbms_stats: available<br>compile_objects: available |
| Capabilities on this account | connect: available<br>session_schema: available<br>all_objects: available<br>explain_plan: available<br>display_cursor: available<br>v_session: available<br>v_sql: available<br>dba_tablespaces: available<br>dba_scheduler_jobs: available<br>dbms_stats: available<br>compile_objects: available |
| Compiler errors for the seeded invalid body | line 5 col 39: PL/SQL: ORA-00942: table or view does not exist; line 5 col 5: PL/SQL: SQL Statement ignored |
| Container identity | non-CDB: CON_ID `0`, CON_NAME `ORCL`, DB_UNIQUE_NAME `ORCL`; harness reported isCdb=`False`, containerName=`ORCL`, databaseName=`ORCL`. |
| DBA panels on the restricted account | Probe found missing: ['dba_scheduler_jobs', 'display_cursor', 'v_session', 'v_sql']<br>sessions: unavailable, `capability_unavailable`, missing ['v_session']<br>blocking: unavailable, `capability_unavailable`, missing ['v_session']<br>tablespaces: available<br>invalidObjects: available<br>schedulerJobs: unavailable, `capability_unavailable`, missing ['dba_scheduler_jobs']<br>schedulerFailures: unavailable, `capability_unavailable`, missing ['dba_scheduler_jobs'] |
| Database major version | 19c (`19.9.1.0.0`) |
| Does DBMS_OUTPUT reach the adapter? | Yes - 1 line(s): ['headcount=3'] |
| Does DDL commit pending DML? | Yes - the row inserted before the CREATE was visible to another session without an explicit commit. |
| Does ROLLBACK TO SAVEPOINT leave the transaction open? | Adapter reports open=True; the row before the savepoint survived (1 == 1) and the row after it did not (0 == 0). |
| Does a PL/SQL block leave a transaction open? | Yes - the adapter's assumption holds. |
| Does a cancellation roll back earlier work in the same transaction? | No - the earlier insert was still pending, as assumed. |
| Does the blocking panel show a real blocked session? | Session 607 waiting on 487: shown as `[607, 'HARNESS_APP', 'enq: TX - row lock contention', 0]` blocked by SID `487` |
| EMPTY_CLOB() vs NULL | EMPTY_CLOB() arrived as `{'kind': 'lob', 'preview': '', 'charLength': 0, 'truncated': False}`; a NULL CLOB as `None`; EMPTY_BLOB() as `{'kind': 'lob', 'preview': '', 'byteLength': 0, 'truncated': False}`. |
| How do TIMESTAMP columns arrive? | TIMESTAMP: `datetime.datetime(2026, 3, 1, 12, 34, 56, 789012)`<br>WITH TIME ZONE: `datetime.datetime(2026, 3, 1, 12, 34, 56, 789012)`<br>WITH LOCAL TIME ZONE: `datetime.datetime(2026, 3, 1, 12, 34, 56, 789012)`<br>Warning: `TIMESTAMP WITH TIME ZONE column(s) TS_TZ are shown without their offset: the driver returns the wall-clock time only. Select them with TO_CHAR(column, 'YYYY-MM-DD HH24:MI:SS.FF TZH:TZM') to see the offset.` |
| How does NUMBER(20,10) arrive? | As `Decimal` with value `1234567890.0123456789` |
| Is DBMS_OUTPUT bounded? | Yes - a 512-byte budget returned 8 line(s) (480 bytes) and set the truncated flag. |
| Is a lost connection during a write reported as outcome_unknown? | Raised `OutcomeUnknownError` with code `outcome_unknown`. |
| Is a session reusable after a cancellation? | Yes - the connection stayed healthy and answered a query afterwards. |
| Is an empty VARCHAR2 stored as NULL? | v_ascii read back as `None`; NVL says `'was-null'`. |
| K-4 dependency, subprogram and source lookups | rows as on the stand-in |
| K-4 trigger, link and scheduler lookups | rows as on the stand-in; no link |
| Large CLOB under a 1 KiB preview limit | reported charLength `65534`, preview is 1024 characters / 1024 bytes, truncated=`True` |
| Lost connection during COMMIT | Raised `OutcomeUnknownError` with code `outcome_unknown` |
| NCLOB of 4,000 two-byte characters: reported length | charLength `4000`, byteLength `None` |
| PL/Scope absence | EMPLOYEE_REPORT reports NOT COLLECTED; the function is read |
| Runtime error from a PL/SQL block | Oracle code `ORA-20001`, message `ORA-20001: qualification probe<br>ORA-06512: at line 1` |
| Second target identity | separate databases on separate instances. Primary: DBID `1771485732`, CON_DBID `1771485732`, CON_NAME `ORCL`, DB_UNIQUE_NAME `ORCL`, instance `ORCL` on `90520b67e617`, user `HARNESS_APP`. Second: DBID `1509902213`, CON_DBID `3457782515`, CON_NAME `FREEPDB1`, DB_UNIQUE_NAME `FREE`, instance `FREE` on `125dc0ca2795`, user `HARNESS_APP`. |
| Shape of a small CLOB | `{'kind': 'lob', 'preview': 'short clob', 'charLength': 10, 'truncated': False}` |
| Unicode read back | `'こんにちは — café — مرحبا'` |
| What does a broken statement raise? | `CancelledError_` with code `execution_cancelled`. Returned after 3.0s of a 30s statement. |
| What does a killed session raise mid-statement? | `OutcomeUnknownError` / harness code `outcome_unknown` |
| What enforces the execution deadline? | A 5s budget stopped a 30s statement after 5.0s, raising `TimeoutError_`. |
| What happens when a block fills the DBMS_OUTPUT server buffer? | Stopped with `ORA-20000`; the next block's output was `['after']`. |

Not exercised by this run:

- PDB identity: the target is a non-CDB (CON_ID 0, `ORCL`).
- Thick mode: This run is thin mode; thick needs a separate process.

### Run 2026-10-14 06:07 UTC

- Harness build: `fa5eb09` plus the two uncommitted test fixes in "What the 2026-10-14 runs found"
- Target: `localhost:1521/FREEPDB1` as `harness_app`, schema `HARNESS_APP`
- Platform: Linux-7.0.0-38-generic-x86_64-with-glibc2.43, Python 3.11.16
- driverMode: `thin`
- pythonOracledb: `4.0.2`


Run R2: D2 primary, D1 second target, thin. The same suites, with
`HARNESS_QUAL_REQUIRE=admin,second_target,restricted_account,pdb`. Result: 155 passed,
16 skipped, 0 failed. The skips as R1. The attempt before it failed twice: the two test
defects in "What the 2026-10-14 runs found".

Database, as reported by the database:

- characterSet: `AL32UTF8`
- containerId: `3`
- containerName: `FREEPDB1`
- currentSchema: `HARNESS_APP`
- currentUser: `HARNESS_APP`
- databaseName: `FREEPDB1`
- dbUniqueName: `FREE`
- driver.driverMode: `thin`
- driver.pythonOracledb: `4.0.2`
- hostName: `125dc0ca2795`
- instanceName: `FREE`
- isCdb: `True`
- nationalCharacterSet: `AL16UTF16`
- version: `23.26.3.0.0`
- versionFull: `23.26.3.0.0`

Observed Oracle behaviour:

| Question | What this database did |
| --- | --- |
| BLOB under a 256-byte preview limit | byteLength `20000`, preview 512 hex characters, truncated=`True` |
| Capabilities on the restricted account | connect: available<br>session_schema: available<br>all_objects: available<br>explain_plan: available<br>display_cursor: unavailable (ORA-00942: table or view "SYS"."V_$SQL_PLAN" does not exist<br>Help: https://docs.oracle.com/error-help/db/ora-00942/)<br>v_session: unavailable (ORA-00942: table or view "SYS"."V_$SESSION" does not exist<br>Help: https://docs.oracle.com/error-help/db/ora-00942/)<br>v_sql: unavailable (ORA-00942: table or view "SYS"."V_$SQL" does not exist<br>Help: https://docs.oracle.com/error-help/db/ora-00942/)<br>dba_tablespaces: available<br>dba_scheduler_jobs: unavailable (ORA-00942: table or view "SYS"."DBA_SCHEDULER_JOBS" does not exist<br>Help: https://docs.oracle.com/error-help/db/ora-00942/)<br>dbms_stats: available<br>compile_objects: available |
| Capabilities on this account | connect: available<br>session_schema: available<br>all_objects: available<br>explain_plan: available<br>display_cursor: available<br>v_session: available<br>v_sql: available<br>dba_tablespaces: available<br>dba_scheduler_jobs: available<br>dbms_stats: available<br>compile_objects: available |
| Compiler errors for the seeded invalid body | line 5 col 39: PL/SQL: ORA-00942: table or view does not exist; line 5 col 5: PL/SQL: SQL Statement ignored |
| Container identity | a pluggable database: CON_ID `3`, CON_NAME `FREEPDB1`, DB_UNIQUE_NAME `FREE`; harness reported isCdb=`True`, containerName=`FREEPDB1`, databaseName=`FREEPDB1`. |
| DBA panels on the restricted account | Probe found missing: ['dba_scheduler_jobs', 'display_cursor', 'v_session', 'v_sql']<br>sessions: unavailable, `capability_unavailable`, missing ['v_session']<br>blocking: unavailable, `capability_unavailable`, missing ['v_session']<br>tablespaces: available<br>invalidObjects: available<br>schedulerJobs: unavailable, `capability_unavailable`, missing ['dba_scheduler_jobs']<br>schedulerFailures: unavailable, `capability_unavailable`, missing ['dba_scheduler_jobs'] |
| Database major version | **23**, not 19. This run does not qualify Oracle 19c. Full version: `23.26.3.0.0`. |
| Does DBMS_OUTPUT reach the adapter? | Yes - 1 line(s): ['headcount=3'] |
| Does DDL commit pending DML? | Yes - the row inserted before the CREATE was visible to another session without an explicit commit. |
| Does ROLLBACK TO SAVEPOINT leave the transaction open? | Adapter reports open=True; the row before the savepoint survived (1 == 1) and the row after it did not (0 == 0). |
| Does a PL/SQL block leave a transaction open? | Yes - the adapter's assumption holds. |
| Does a cancellation roll back earlier work in the same transaction? | No - the earlier insert was still pending, as assumed. |
| Does the blocking panel show a real blocked session? | Session 209 waiting on 4: shown as `[209, 'HARNESS_APP', 'enq: TX - row lock contention', 0]` blocked by SID `4` |
| EMPTY_CLOB() vs NULL | EMPTY_CLOB() arrived as `{'kind': 'lob', 'preview': '', 'charLength': 0, 'truncated': False}`; a NULL CLOB as `None`; EMPTY_BLOB() as `{'kind': 'lob', 'preview': '', 'byteLength': 0, 'truncated': False}`. |
| How do TIMESTAMP columns arrive? | TIMESTAMP: `datetime.datetime(2026, 3, 1, 12, 34, 56, 789012)`<br>WITH TIME ZONE: `datetime.datetime(2026, 3, 1, 12, 34, 56, 789012)`<br>WITH LOCAL TIME ZONE: `datetime.datetime(2026, 3, 1, 12, 34, 56, 789012)`<br>Warning: `TIMESTAMP WITH TIME ZONE column(s) TS_TZ are shown without their offset: the driver returns the wall-clock time only. Select them with TO_CHAR(column, 'YYYY-MM-DD HH24:MI:SS.FF TZH:TZM') to see the offset.` |
| How does NUMBER(20,10) arrive? | As `Decimal` with value `1234567890.0123456789` |
| Is DBMS_OUTPUT bounded? | Yes - a 512-byte budget returned 8 line(s) (480 bytes) and set the truncated flag. |
| Is a lost connection during a write reported as outcome_unknown? | Raised `OutcomeUnknownError` with code `outcome_unknown`. |
| Is a session reusable after a cancellation? | Yes - the connection stayed healthy and answered a query afterwards. |
| Is an empty VARCHAR2 stored as NULL? | v_ascii read back as `None`; NVL says `'was-null'`. |
| K-4 dependency, subprogram and source lookups | rows as on the stand-in |
| K-4 trigger, link and scheduler lookups | rows as on the stand-in; no link |
| Large CLOB under a 1 KiB preview limit | reported charLength `65534`, preview is 1024 characters / 1024 bytes, truncated=`True` |
| Lost connection during COMMIT | Raised `OutcomeUnknownError` with code `outcome_unknown` |
| NCLOB of 4,000 two-byte characters: reported length | charLength `4000`, byteLength `None` |
| PL/Scope absence | EMPLOYEE_REPORT reports NOT COLLECTED; the function is read |
| Runtime error from a PL/SQL block | Oracle code `ORA-20001`, message `ORA-20001: qualification probe<br>ORA-06512: at line 1` |
| Second target identity | separate databases on separate instances. Primary: DBID `1509902213`, CON_DBID `3457782515`, CON_NAME `FREEPDB1`, DB_UNIQUE_NAME `FREE`, instance `FREE` on `125dc0ca2795`, user `HARNESS_APP`. Second: DBID `1771485732`, CON_DBID `1771485732`, CON_NAME `ORCL`, DB_UNIQUE_NAME `ORCL`, instance `ORCL` on `90520b67e617`, user `HARNESS_APP`. |
| Shape of a small CLOB | `{'kind': 'lob', 'preview': 'short clob', 'charLength': 10, 'truncated': False}` |
| Unicode read back | `'こんにちは — café — مرحبا'` |
| What does a broken statement raise? | `CancelledError_` with code `execution_cancelled`. Returned after 3.0s of a 30s statement. |
| What does a killed session raise mid-statement? | `OutcomeUnknownError` / harness code `outcome_unknown` |
| What enforces the execution deadline? | A 5s budget stopped a 30s statement after 5.0s, raising `TimeoutError_`. |
| What happens when a block fills the DBMS_OUTPUT server buffer? | Stopped with `ORA-20000`; the next block's output was `['after']`. |

Not exercised by this run:

- Thick mode: This run is thin mode; thick needs a separate process.

### Run 2026-10-14 06:09 UTC

- Harness build: `fa5eb09` plus the two uncommitted test fixes in "What the 2026-10-14 runs found"
- Target: `localhost:1522/ORCL` as `harness_app`, schema `HARNESS_APP`
- Platform: Linux-7.0.0-38-generic-x86_64-with-glibc2.43, Python 3.11.16
- driverMode: `thick`
- pythonOracledb: `4.0.2`
- oracleClient: `21.4.0.0.0`


Run R3: R1's configuration in thick mode, Oracle Instant Client 21.4. Result: 155 passed,
16 skipped, 0 failed. The skips as R1. Apart from session ids and the `Help:` link
missing from thick-mode `ORA-00942` text, every row matches R1.

Database, as reported by the database:

- characterSet: `AL32UTF8`
- containerId: `0`
- containerName: `ORCL`
- currentSchema: `HARNESS_APP`
- currentUser: `HARNESS_APP`
- databaseName: `ORCL`
- dbUniqueName: `ORCL`
- driver.driverMode: `thick`
- driver.oracleClient: `21.4.0.0.0`
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
| Capabilities on the restricted account | connect: available<br>session_schema: available<br>all_objects: available<br>explain_plan: available<br>display_cursor: unavailable (ORA-00942: table or view does not exist)<br>v_session: unavailable (ORA-00942: table or view does not exist)<br>v_sql: unavailable (ORA-00942: table or view does not exist)<br>dba_tablespaces: available<br>dba_scheduler_jobs: unavailable (ORA-00942: table or view does not exist)<br>dbms_stats: available<br>compile_objects: available |
| Capabilities on this account | connect: available<br>session_schema: available<br>all_objects: available<br>explain_plan: available<br>display_cursor: available<br>v_session: available<br>v_sql: available<br>dba_tablespaces: available<br>dba_scheduler_jobs: available<br>dbms_stats: available<br>compile_objects: available |
| Compiler errors for the seeded invalid body | line 5 col 39: PL/SQL: ORA-00942: table or view does not exist; line 5 col 5: PL/SQL: SQL Statement ignored |
| Container identity | non-CDB: CON_ID `0`, CON_NAME `ORCL`, DB_UNIQUE_NAME `ORCL`; harness reported isCdb=`False`, containerName=`ORCL`, databaseName=`ORCL`. |
| DBA panels on the restricted account | Probe found missing: ['dba_scheduler_jobs', 'display_cursor', 'v_session', 'v_sql']<br>sessions: unavailable, `capability_unavailable`, missing ['v_session']<br>blocking: unavailable, `capability_unavailable`, missing ['v_session']<br>tablespaces: available<br>invalidObjects: available<br>schedulerJobs: unavailable, `capability_unavailable`, missing ['dba_scheduler_jobs']<br>schedulerFailures: unavailable, `capability_unavailable`, missing ['dba_scheduler_jobs'] |
| Database major version | 19c (`19.9.1.0.0`) |
| Does DBMS_OUTPUT reach the adapter? | Yes - 1 line(s): ['headcount=3'] |
| Does DDL commit pending DML? | Yes - the row inserted before the CREATE was visible to another session without an explicit commit. |
| Does ROLLBACK TO SAVEPOINT leave the transaction open? | Adapter reports open=True; the row before the savepoint survived (1 == 1) and the row after it did not (0 == 0). |
| Does a PL/SQL block leave a transaction open? | Yes - the adapter's assumption holds. |
| Does a cancellation roll back earlier work in the same transaction? | No - the earlier insert was still pending, as assumed. |
| Does the blocking panel show a real blocked session? | Session 608 waiting on 492: shown as `[608, 'HARNESS_APP', 'enq: TX - row lock contention', 0]` blocked by SID `492` |
| EMPTY_CLOB() vs NULL | EMPTY_CLOB() arrived as `{'kind': 'lob', 'preview': '', 'charLength': 0, 'truncated': False}`; a NULL CLOB as `None`; EMPTY_BLOB() as `{'kind': 'lob', 'preview': '', 'byteLength': 0, 'truncated': False}`. |
| How do TIMESTAMP columns arrive? | TIMESTAMP: `datetime.datetime(2026, 3, 1, 12, 34, 56, 789012)`<br>WITH TIME ZONE: `datetime.datetime(2026, 3, 1, 12, 34, 56, 789012)`<br>WITH LOCAL TIME ZONE: `datetime.datetime(2026, 3, 1, 12, 34, 56, 789012)`<br>Warning: `TIMESTAMP WITH TIME ZONE column(s) TS_TZ are shown without their offset: the driver returns the wall-clock time only. Select them with TO_CHAR(column, 'YYYY-MM-DD HH24:MI:SS.FF TZH:TZM') to see the offset.` |
| How does NUMBER(20,10) arrive? | As `Decimal` with value `1234567890.0123456789` |
| Is DBMS_OUTPUT bounded? | Yes - a 512-byte budget returned 8 line(s) (480 bytes) and set the truncated flag. |
| Is a lost connection during a write reported as outcome_unknown? | Raised `OutcomeUnknownError` with code `outcome_unknown`. |
| Is a session reusable after a cancellation? | Yes - the connection stayed healthy and answered a query afterwards. |
| Is an empty VARCHAR2 stored as NULL? | v_ascii read back as `None`; NVL says `'was-null'`. |
| K-4 dependency, subprogram and source lookups | rows as on the stand-in |
| K-4 trigger, link and scheduler lookups | rows as on the stand-in; no link |
| Large CLOB under a 1 KiB preview limit | reported charLength `65534`, preview is 1024 characters / 1024 bytes, truncated=`True` |
| Lost connection during COMMIT | Raised `OutcomeUnknownError` with code `outcome_unknown` |
| NCLOB of 4,000 two-byte characters: reported length | charLength `4000`, byteLength `None` |
| PL/Scope absence | EMPLOYEE_REPORT reports NOT COLLECTED; the function is read |
| Runtime error from a PL/SQL block | Oracle code `ORA-20001`, message `ORA-20001: qualification probe<br>ORA-06512: at line 1` |
| Second target identity | separate databases on separate instances. Primary: DBID `1771485732`, CON_DBID `1771485732`, CON_NAME `ORCL`, DB_UNIQUE_NAME `ORCL`, instance `ORCL` on `90520b67e617`, user `HARNESS_APP`. Second: DBID `1509902213`, CON_DBID `3457782515`, CON_NAME `FREEPDB1`, DB_UNIQUE_NAME `FREE`, instance `FREE` on `125dc0ca2795`, user `HARNESS_APP`. |
| Shape of a small CLOB | `{'kind': 'lob', 'preview': 'short clob', 'charLength': 10, 'truncated': False}` |
| Unicode read back | `'こんにちは — café — مرحبا'` |
| What does a broken statement raise? | `CancelledError_` with code `execution_cancelled`. Returned after 3.0s of a 30s statement. |
| What does a killed session raise mid-statement? | `OutcomeUnknownError` / harness code `outcome_unknown` |
| What enforces the execution deadline? | A 5s budget stopped a 30s statement after 5.0s, raising `TimeoutError_`. |
| What happens when a block fills the DBMS_OUTPUT server buffer? | Stopped with `ORA-20000`; the next block's output was `['after']`. |

Not exercised by this run:

- PDB identity: the target is a non-CDB (CON_ID 0, `ORCL`).

### Process-death run 2026-10-14 06:11 UTC

- Harness build: `fa5eb09` plus the two uncommitted test fixes in "What the 2026-10-14 runs found"
- Target: `localhost:1522/ORCL` as `harness_app`, schema `HARNESS_APP`
- Platform: Linux-7.0.0-38-generic-x86_64-with-glibc2.43, Python 3.11.16
- driverMode: `thin`
- pythonOracledb: `4.0.2`


Run R4 on D1: `tests/integration/test_process_death.py` alone. Result: 6 passed in 24.6s.

Database, as reported by the database:

- containerName: `ORCL`
- databaseName: `ORCL`
- isCdb: `False`
- versionFull: `19.9.1.0.0`

Observed Oracle behaviour:

| Question | What this database did |
| --- | --- |
| Process death before dispatch | API process 1800602 ended with SIGKILL at `before_dispatch`. Dead session 488,30807 cleaned up after 0.0s (the session left V$SESSION and its row lock was free). Restart recorded `cancelled`; an independent session read salary 11500, unchanged. |
| Process death during COMMIT (COMMIT returned, answer not recorded) | API process 1800710 ended with SIGKILL at `commit_returned`. Dead session 488,22408 cleaned up after 0.0s (the session left V$SESSION and its row lock was free). Restart listed the session under `commitsUnknown`, with no successful commit audited. The independent session read salary 4545 (was 11500): durable. Recorded finding `applied`. |
| Process death during COMMIT (intent recorded, COMMIT not sent) | API process 1800687 ended with SIGKILL at `commit_before_send`. Dead session 488,57503 cleaned up after 0.0s (the session left V$SESSION and its row lock was free). Restart listed the session under `commitsUnknown`. The independent session read salary 11500 (was 11500): not durable. Recorded finding `not_applied`. |
| Process death during a read | API process 1800623 ended with SIGKILL at `statement_returned`. Dead session 488,23715 cleaned up after 0.0s (the session left V$SESSION and its row lock was free). Restart recorded `failed` with no verification required. |
| Process death during a write (applied, uncommitted, answer not recorded) | API process 1800644 ended with SIGKILL at `statement_returned`. Dead session 488,63862 cleaned up after 0.0s (the session left V$SESSION and its row lock was free). Restart recorded `outcome_unknown` with verification required. The independent session read salary 11500 (was 11500): Oracle rolled the dead session's transaction back. Recorded finding `not_applied`. |
| Process death while a write waits inside the database | API process 1800666 ended with SIGKILL at `statement_sent`. Dead session 488,26866 cleaned up after 0.0s (the session left V$SESSION and its row lock was free). The session was waiting on `enq: TX - row lock contention (blocked by SID 612)` when killed; immediately after the kill V$SESSION still showed it as {'status': 'ACTIVE', 'blockingSession': 612, 'event': 'enq: TX - row lock contention', 'module': 'OracleDBHarness'}. After the lock was released the independent session read salary 11500 (was 11500). Restart recorded `outcome_unknown`. |

### Process-death run 2026-10-14 06:11 UTC

- Harness build: `fa5eb09` plus the two uncommitted test fixes in "What the 2026-10-14 runs found"
- Target: `localhost:1521/FREEPDB1` as `harness_app`, schema `HARNESS_APP`
- Platform: Linux-7.0.0-38-generic-x86_64-with-glibc2.43, Python 3.11.16
- driverMode: `thin`
- pythonOracledb: `4.0.2`


Run R4 on D2: the same file. Result: 6 passed in 25.3s.

Database, as reported by the database:

- containerName: `FREEPDB1`
- databaseName: `FREEPDB1`
- isCdb: `True`
- versionFull: `23.26.3.0.0`

Observed Oracle behaviour:

| Question | What this database did |
| --- | --- |
| Process death before dispatch | API process 1800825 ended with SIGKILL at `before_dispatch`. Dead session 176,19483 cleaned up after 0.0s (the session left V$SESSION and its row lock was free). Restart recorded `cancelled`; an independent session read salary 11500, unchanged. |
| Process death during COMMIT (COMMIT returned, answer not recorded) | API process 1800947 ended with SIGKILL at `commit_returned`. Dead session 176,47285 cleaned up after 0.0s (the session left V$SESSION and its row lock was free). Restart listed the session under `commitsUnknown`, with no successful commit audited. The independent session read salary 4545 (was 11500): durable. Recorded finding `applied`. |
| Process death during COMMIT (intent recorded, COMMIT not sent) | API process 1800920 ended with SIGKILL at `commit_before_send`. Dead session 209,20275 cleaned up after 0.0s (the session left V$SESSION and its row lock was free). Restart listed the session under `commitsUnknown`. The independent session read salary 11500 (was 11500): not durable. Recorded finding `not_applied`. |
| Process death during a read | API process 1800847 ended with SIGKILL at `statement_returned`. Dead session 176,61281 cleaned up after 0.0s (the session left V$SESSION and its row lock was free). Restart recorded `failed` with no verification required. |
| Process death during a write (applied, uncommitted, answer not recorded) | API process 1800877 ended with SIGKILL at `statement_returned`. Dead session 176,54128 cleaned up after 0.0s (the session left V$SESSION and its row lock was free). Restart recorded `outcome_unknown` with verification required. The independent session read salary 11500 (was 11500): Oracle rolled the dead session's transaction back. Recorded finding `not_applied`. |
| Process death while a write waits inside the database | API process 1800899 ended with SIGKILL at `statement_sent`. Dead session 176,21542 cleaned up after 0.0s (the session left V$SESSION and its row lock was free). The session was waiting on `enq: TX - row lock contention (blocked by SID 210)` when killed; immediately after the kill V$SESSION still showed it as {'status': 'ACTIVE', 'blockingSession': 210, 'event': 'enq: TX - row lock contention', 'module': 'OracleDBHarness'}. After the lock was released the independent session read salary 11500 (was 11500). Restart recorded `outcome_unknown`. |

### Run 2026-10-14 13:44 UTC

- Harness build: `241caf7`
- Target: `D4 (remote host, see `databases/INVENTORY.md`), service `ORCL`` as `harness_app`, schema `HARNESS_APP`
- Platform: Linux-7.0.0-38-generic-x86_64-with-glibc2.43, Python 3.11.16
- driverMode: `thin`
- pythonOracledb: `4.0.2`


Run R6: D4 primary, D1 second target, thin. The whole test suite, with
`HARNESS_QUAL_REQUIRE=admin,second_target`. Result: 779 passed, 4 failed, 33 skipped in
2,747 s. The four failures were test defects, fixed and rerun (23 passed, 4 skipped);
see "The D4 run, 2026-10-14". Skips: 13 `stand_in_only`, 11 PostgreSQL and 7 Keycloak
checks (not configured), the lab workload (no lab account) and the commit window.

Database, as reported by the database:

- characterSet: `WE8MSWIN1252`
- containerId: `0`
- containerName: `ORCL`
- currentSchema: `HARNESS_APP`
- currentUser: `HARNESS_APP`
- databaseName: `ORCL`
- dbUniqueName: `ORCL`
- driver.driverMode: `thin`
- driver.pythonOracledb: `4.0.2`
- hostName: `27e1d5797223`
- instanceName: `ORCL`
- isCdb: `False`
- nationalCharacterSet: `AL16UTF16`
- version: `19.9.1.0.0`
- versionFull: `19.9.1.0.0`

Observed Oracle behaviour:

| Question | What this database did |
| --- | --- |
| BLOB under a 256-byte preview limit | byteLength `20000`, preview 512 hex characters, truncated=`True` |
| Capabilities on the restricted account | connect: available<br>session_schema: available<br>all_objects: available<br>explain_plan: available<br>display_cursor: unavailable (ORA-00942: table or view does not exist<br>Help: https://docs.oracle.com/error-help/db/ora-00942/)<br>v_session: unavailable (ORA-00942: table or view does not exist<br>Help: https://docs.oracle.com/error-help/db/ora-00942/)<br>v_sql: unavailable (ORA-00942: table or view does not exist<br>Help: https://docs.oracle.com/error-help/db/ora-00942/)<br>dba_tablespaces: unavailable (ORA-00942: table or view does not exist<br>Help: https://docs.oracle.com/error-help/db/ora-00942/)<br>dba_scheduler_jobs: unavailable (ORA-00942: table or view does not exist<br>Help: https://docs.oracle.com/error-help/db/ora-00942/)<br>dbms_stats: available<br>compile_objects: available |
| Capabilities on this account | connect: available<br>session_schema: available<br>all_objects: available<br>explain_plan: available<br>display_cursor: available<br>v_session: available<br>v_sql: available<br>dba_tablespaces: available<br>dba_scheduler_jobs: available<br>dbms_stats: available<br>compile_objects: available |
| Compiler errors for the seeded invalid body | line 5 col 39: PL/SQL: ORA-00942: table or view does not exist; line 5 col 5: PL/SQL: SQL Statement ignored |
| Container identity | non-CDB: CON_ID `0`, CON_NAME `ORCL`, DB_UNIQUE_NAME `ORCL`; harness reported isCdb=`False`, containerName=`ORCL`, databaseName=`ORCL`. |
| DBA panels on the restricted account | Probe found missing: ['dba_scheduler_jobs', 'dba_tablespaces', 'display_cursor', 'v_session', 'v_sql']<br>sessions: unavailable, `capability_unavailable`, missing ['v_session']<br>blocking: unavailable, `capability_unavailable`, missing ['v_session']<br>tablespaces: unavailable, `capability_unavailable`, missing ['dba_tablespaces']<br>invalidObjects: available<br>schedulerJobs: unavailable, `capability_unavailable`, missing ['dba_scheduler_jobs']<br>schedulerFailures: unavailable, `capability_unavailable`, missing ['dba_scheduler_jobs'] |
| Database character set | `WE8MSWIN1252`, not AL32UTF8. Treat the Unicode bind results as specific to this character set. |
| Database major version | 19c (`19.9.1.0.0`) |
| Does DBMS_OUTPUT reach the adapter? | Yes - 1 line(s): ['headcount=3'] |
| Does DDL commit pending DML? | Yes - the row inserted before the CREATE was visible to another session without an explicit commit. |
| Does ROLLBACK TO SAVEPOINT leave the transaction open? | Adapter reports open=True; the row before the savepoint survived (1 == 1) and the row after it did not (0 == 0). |
| Does a PL/SQL block leave a transaction open? | Yes - the adapter's assumption holds. |
| Does a cancellation roll back earlier work in the same transaction? | No - the earlier insert was still pending, as assumed. |
| Does the blocking panel show a real blocked session? | Session 266 waiting on 267: shown as `[266, 'HARNESS_APP', 'enq: TX - row lock contention', 0]` blocked by SID `267` |
| EMPTY_CLOB() vs NULL | EMPTY_CLOB() arrived as `{'kind': 'lob', 'preview': '', 'charLength': 0, 'truncated': False}`; a NULL CLOB as `None`; EMPTY_BLOB() as `{'kind': 'lob', 'preview': '', 'byteLength': 0, 'truncated': False}`. |
| How do TIMESTAMP columns arrive? | TIMESTAMP: `datetime.datetime(2026, 3, 1, 12, 34, 56, 789012)`<br>WITH TIME ZONE: `datetime.datetime(2026, 3, 1, 12, 34, 56, 789012)`<br>WITH LOCAL TIME ZONE: `datetime.datetime(2026, 3, 1, 12, 34, 56, 789012)`<br>Warning: `TIMESTAMP WITH TIME ZONE column(s) TS_TZ are shown without their offset: the driver returns the wall-clock time only. Select them with TO_CHAR(column, 'YYYY-MM-DD HH24:MI:SS.FF TZH:TZM') to see the offset.` |
| How does NUMBER(20,10) arrive? | As `Decimal` with value `1234567890.0123456789` |
| Is DBMS_OUTPUT bounded? | Yes - a 512-byte budget returned 8 line(s) (480 bytes) and set the truncated flag. |
| Is a lost connection during a write reported as outcome_unknown? | Raised `OutcomeUnknownError` with code `outcome_unknown`. |
| Is a session reusable after a cancellation? | Yes - the connection stayed healthy and answered a query afterwards. |
| Is an empty VARCHAR2 stored as NULL? | v_ascii read back as `None`; NVL says `'was-null'`. |
| K-4 dependency, subprogram and source lookups | rows as on the stand-in |
| K-4 trigger, link and scheduler lookups | rows as on the stand-in; no link |
| Large CLOB under a 1 KiB preview limit | reported charLength `65534`, preview is 1024 characters / 1024 bytes, truncated=`True` |
| Lost connection during COMMIT | Inconclusive - the commit completed before the session was killed. Re-run to try to hit the window. |
| NCLOB of 4,000 two-byte characters: reported length | charLength `4000`, byteLength `None` |
| PL/Scope absence | EMPLOYEE_REPORT reports NOT COLLECTED; the function is read |
| Runtime error from a PL/SQL block | Oracle code `ORA-20001`, message `ORA-20001: qualification probe<br>ORA-06512: at line 1` |
| Second target identity | separate databases on separate instances. Primary: DBID `1773258920`, CON_DBID `1773258920`, CON_NAME `ORCL`, DB_UNIQUE_NAME `ORCL`, instance `ORCL` on `27e1d5797223`, user `HARNESS_APP`. Second: DBID `1771485732`, CON_DBID `1771485732`, CON_NAME `ORCL`, DB_UNIQUE_NAME `ORCL`, instance `ORCL` on `90520b67e617`, user `HARNESS_APP`. |
| Shape of a small CLOB | `{'kind': 'lob', 'preview': 'short clob', 'charLength': 10, 'truncated': False}` |
| Unicode lookup by bind | Not checked: the database character set is `WE8MSWIN1252`, not AL32UTF8, so a VARCHAR bind cannot carry the sample characters. Read-back above passed. |
| Unicode read back | `'こんにちは — café — مرحبا'` |
| What does a broken statement raise? | `CancelledError_` with code `execution_cancelled`. Returned after 3.3s of a 30s statement. |
| What does a killed session raise mid-statement? | `OutcomeUnknownError` / harness code `outcome_unknown` |
| What enforces the execution deadline? | A 5s budget stopped a 30s statement after 5.3s, raising `TimeoutError_`. |
| What happens when a block fills the DBMS_OUTPUT server buffer? | Stopped with `ORA-20000`; the next block's output was `['after']`. |

Not exercised by this run:

- Lab workloads: HARNESS_QUAL_LAB_USER is not set. The lab workload checks need a read-only account over a schema with realistic data, named by HARNESS_QUAL_LAB_SCHEMA.
- PDB identity: the target is a non-CDB (CON_ID 0, `ORCL`).
- Thick mode: This run is thin mode; thick needs a separate process.

### Process-death run 2026-10-14 13:34 UTC

- Harness build: `241caf7`
- Target: `D4 (remote host, see `databases/INVENTORY.md`), service `ORCL`` as `harness_app`, schema `HARNESS_APP`
- Platform: Linux-7.0.0-38-generic-x86_64-with-glibc2.43, Python 3.11.16
- driverMode: `thin`
- pythonOracledb: `4.0.2`


Run R6 on D4: `tests/integration/test_process_death.py`, inside the whole-suite run.
Result: 6 passed.

Database, as reported by the database:

- containerName: `ORCL`
- databaseName: `ORCL`
- isCdb: `False`
- versionFull: `19.9.1.0.0`

Observed Oracle behaviour:

| Question | What this database did |
| --- | --- |
| Process death before dispatch | API process 1885582 ended with SIGKILL at `before_dispatch`. Dead session 272,48823 cleaned up after 0.5s (the session left V$SESSION and its row lock was free). Restart recorded `cancelled`; an independent session read salary 11500, unchanged. |
| Process death during COMMIT (COMMIT returned, answer not recorded) | API process 1885872 ended with SIGKILL at `commit_returned`. Dead session 266,45206 cleaned up after 0.4s (the session left V$SESSION and its row lock was free). Restart listed the session under `commitsUnknown`, with no successful commit audited. The independent session read salary 4545 (was 11500): durable. Recorded finding `applied`. |
| Process death during COMMIT (intent recorded, COMMIT not sent) | API process 1885788 ended with SIGKILL at `commit_before_send`. Dead session 266,54750 cleaned up after 0.4s (the session left V$SESSION and its row lock was free). Restart listed the session under `commitsUnknown`. The independent session read salary 11500 (was 11500): not durable. Recorded finding `not_applied`. |
| Process death during a read | API process 1885662 ended with SIGKILL at `statement_returned`. Dead session 272,20172 cleaned up after 0.4s (the session left V$SESSION and its row lock was free). Restart recorded `failed` with no verification required. |
| Process death during a write (applied, uncommitted, answer not recorded) | API process 1885692 ended with SIGKILL at `statement_returned`. Dead session 272,63252 cleaned up after 0.4s (the session left V$SESSION and its row lock was free). Restart recorded `outcome_unknown` with verification required. The independent session read salary 11500 (was 11500): Oracle rolled the dead session's transaction back. Recorded finding `not_applied`. |
| Process death while a write waits inside the database | API process 1885719 ended with SIGKILL at `statement_sent`. Dead session 272,53134 cleaned up after 0.4s (the session left V$SESSION and its row lock was free). The session was waiting on `enq: TX - row lock contention (blocked by SID 267)` when killed; immediately after the kill V$SESSION still showed it as {'status': 'ACTIVE', 'blockingSession': 267, 'event': 'enq: TX - row lock contention', 'module': 'OracleDBHarness'}. After the lock was released the independent session read salary 11500 (was 11500). Restart recorded `outcome_unknown`. |

### Run 2026-10-14 20:12 UTC

- Harness build: `c46044b` with the R5 transport fixes (branch `qual/r5-tcps-wallet`)
- Target: `D3 (Autonomous Database, see `databases/INVENTORY.md`), TCPS with its wallet` as `harness_app`, schema `HARNESS_APP`
- Platform: Linux-7.0.0-38-generic-x86_64-with-glibc2.43, Python 3.11.16
- driverMode: `thin`
- pythonOracledb: `4.0.2`

Run R5: D3 primary over TCPS with an encrypted wallet, D1 second target, thin, with
`HARNESS_QUAL_REQUIRE=admin,second_target`. Result: 160 passed, 20 skipped, 0 failed in
262 s. The first attempt failed 2 of 180; both were harness or test defects, fixed
before this run (see "The D3 run, 2026-10-14"). Skips: 13 `stand_in_only`, 4 PostgreSQL,
the restricted account (2) and the lab workload (1), none of them provisioned on the
shared D3.

Database, as reported by the database:

- characterSet: `AL32UTF8`
- containerId: `172`
- containerName: `<D3 PDB name, redacted>`
- currentSchema: `HARNESS_APP`
- currentUser: `HARNESS_APP`
- databaseName: `<D3 PDB name, redacted>`
- dbUniqueName: `fce7pwwd`
- driver.driverMode: `thin`
- driver.pythonOracledb: `4.0.2`
- hostName: `POD`
- instanceName: `fce7pwwd2`
- isCdb: `True`
- nationalCharacterSet: `AL16UTF16`
- version: `23.26.4.1.0`
- versionFull: `23.26.4.1.0`

Observed Oracle behaviour:

| Question | What this database did |
| --- | --- |
| BLOB under a 256-byte preview limit | byteLength `20000`, preview 512 hex characters, truncated=`True` |
| BOOLEAN bind | true selected `yes`, false `no` |
| Capabilities on this account | connect: available<br>session_schema: available<br>all_objects: available<br>explain_plan: available<br>display_cursor: available<br>v_session: available<br>v_sql: available<br>dba_tablespaces: available<br>dba_scheduler_jobs: available<br>dbms_stats: available<br>compile_objects: available |
| Compiler errors for the seeded invalid body | line 5 col 39: PL/SQL: ORA-00942: table or view does not exist; line 5 col 5: PL/SQL: SQL Statement ignored |
| Container identity | a pluggable database: CON_ID `172`, CON_NAME `<D3 PDB name, redacted>`, DB_UNIQUE_NAME `fce7pwwd`; harness reported isCdb=`True`, containerName=`<D3 PDB name, redacted>`, databaseName=`<D3 PDB name, redacted>`. |
| Database major version | **23**, not 19. This run does not qualify Oracle 19c. Full version: `23.26.4.1.0`. |
| Does DBMS_OUTPUT reach the adapter? | Yes - 1 line(s): ['headcount=3'] |
| Does DDL commit pending DML? | Yes - the row inserted before the CREATE was visible to another session without an explicit commit. |
| Does ROLLBACK TO SAVEPOINT leave the transaction open? | Adapter reports open=True; the row before the savepoint survived (1 == 1) and the row after it did not (0 == 0). |
| Does a PL/SQL block leave a transaction open? | Yes - the adapter's assumption holds. |
| Does a cancellation roll back earlier work in the same transaction? | No - the earlier insert was still pending, as assumed. |
| Does the blocking panel show a real blocked session? | Session 17 waiting on 30989: shown as `[17, 'HARNESS_APP', 'enq: TX - row lock contention', 6]` blocked by SID `30989`, 5.9s after the wait began |
| EMPTY_CLOB() vs NULL | EMPTY_CLOB() arrived as `{'kind': 'lob', 'preview': '', 'charLength': 0, 'truncated': False}`; a NULL CLOB as `None`; EMPTY_BLOB() as `{'kind': 'lob', 'preview': '', 'byteLength': 0, 'truncated': False}`. |
| How do TIMESTAMP columns arrive? | TIMESTAMP: `datetime.datetime(2026, 3, 1, 12, 34, 56, 789012)`<br>WITH TIME ZONE: `datetime.datetime(2026, 3, 1, 12, 34, 56, 789012)`<br>WITH LOCAL TIME ZONE: `datetime.datetime(2026, 3, 1, 12, 34, 56, 789012)`<br>Warning: `TIMESTAMP WITH TIME ZONE column(s) TS_TZ are shown without their offset: the driver returns the wall-clock time only. Select them with TO_CHAR(column, 'YYYY-MM-DD HH24:MI:SS.FF TZH:TZM') to see the offset.` |
| How does BOOLEAN arrive? | `[True, False, None]`, column types ['DB_TYPE_BOOLEAN', 'DB_TYPE_BOOLEAN', 'DB_TYPE_BOOLEAN'] |
| How does NUMBER(20,10) arrive? | As `Decimal` with value `1234567890.0123456789` |
| How does VECTOR arrive? | FLOAT32 `{'kind': 'vector', 'format': 'FLOAT32', 'dimensions': 3, 'values': [0.1, 1.5, -2.0], 'truncated': False}`<br>FLOAT64 `{'kind': 'vector', 'format': 'FLOAT64', 'dimensions': 2, 'values': [0.1, 2.0], 'truncated': False}`<br>INT8 `{'kind': 'vector', 'format': 'INT8', 'dimensions': 2, 'values': [1, -2], 'truncated': False}`<br>BINARY `{'kind': 'vector', 'format': 'BINARY', 'dimensions': 16, 'preview': 'ff00', 'truncated': False}`<br>column types ['DB_TYPE_VECTOR', 'DB_TYPE_VECTOR', 'DB_TYPE_VECTOR', 'DB_TYPE_VECTOR'] |
| How does a SPARSE VECTOR arrive? | `{'kind': 'vector', 'format': 'FLOAT32', 'sparse': True, 'dimensions': 4, 'storedCount': 2, 'indices': [1, 3], 'values': [1.5, 2.5], 'truncated': False}` |
| Is DBMS_OUTPUT bounded? | Yes - a 512-byte budget returned 8 line(s) (480 bytes) and set the truncated flag. |
| Is a lost connection during a write reported as outcome_unknown? | Raised `OutcomeUnknownError` with code `outcome_unknown`. |
| Is a session reusable after a cancellation? | Yes - the connection stayed healthy and answered a query afterwards. |
| Is an empty VARCHAR2 stored as NULL? | v_ascii read back as `None`; NVL says `'was-null'`. |
| K-4 dependency, subprogram and source lookups | rows as on the stand-in |
| K-4 trigger, link and scheduler lookups | rows as on the stand-in; no link |
| Large CLOB under a 1 KiB preview limit | reported charLength `65534`, preview is 1024 characters / 1024 bytes, truncated=`True` |
| Lost connection during COMMIT | Raised `OutcomeUnknownError` with code `outcome_unknown` |
| NCLOB of 4,000 two-byte characters: reported length | charLength `4000`, byteLength `None` |
| PL/Scope absence | EMPLOYEE_REPORT reports NOT COLLECTED; the function is read |
| Runtime error from a PL/SQL block | Oracle code `ORA-20001`, message `ORA-20001: qualification probe<br>ORA-06512: at line 1` |
| Second target identity | separate databases on separate instances. Primary: DBID `<D3 DBID, redacted>`, CON_DBID `<D3 CON_DBID, redacted>`, CON_NAME `<D3 PDB name, redacted>`, DB_UNIQUE_NAME `fce7pwwd`, instance `fce7pwwd2` on `POD`, user `HARNESS_APP`. Second: DBID `1771485732`, CON_DBID `1771485732`, CON_NAME `ORCL`, DB_UNIQUE_NAME `ORCL`, instance `ORCL` on `90520b67e617`, user `HARNESS_APP`. |
| Shape of a small CLOB | `{'kind': 'lob', 'preview': 'short clob', 'charLength': 10, 'truncated': False}` |
| Unicode read back | `'こんにちは — café — مرحبا'` |
| VECTOR(1000, FLOAT64) under a 256-byte preview limit | dimensions `1000`, 32 values shown, truncated=`True` |
| What does a broken statement raise? | `CancelledError_` with code `execution_cancelled`. Returned after 3.0s of a 30s statement. |
| What does a killed session raise mid-statement? | `OutcomeUnknownError` / harness code `outcome_unknown` |
| What enforces the execution deadline? | A 5s budget stopped a 30s statement after 5.0s, raising `TimeoutError_`. |
| What happens when a block fills the DBMS_OUTPUT server buffer? | Stopped with `ORA-20000`; the next block's output was `['after']`. |

Not exercised by this run:

- Lab workloads: HARNESS_QUAL_LAB_USER is not set. The lab workload checks need a read-only account over a schema with realistic data, named by HARNESS_QUAL_LAB_SCHEMA.
- Restricted account: HARNESS_QUAL_RESTRICTED_USER is not set. Degraded panels need an account that is really missing grants; the full-grant fixture account has every capability.
- Thick mode: This run is thin mode; thick needs a separate process.

### Process-death run 2026-10-14 20:14 UTC

- Harness build: `c46044b` with the R5 transport fixes (branch `qual/r5-tcps-wallet`)
- Target: `D3 (Autonomous Database, see `databases/INVENTORY.md`), TCPS with its wallet` as `harness_app`, schema `HARNESS_APP`
- Platform: Linux-7.0.0-38-generic-x86_64-with-glibc2.43, Python 3.11.16
- driverMode: `thin`
- pythonOracledb: `4.0.2`

Run R5 on D3: `tests/integration/test_process_death.py`, inside the run above.
Result: 6 passed.

Database, as reported by the database:

- containerName: `<D3 PDB name, redacted>`
- databaseName: `<D3 PDB name, redacted>`
- isCdb: `True`
- versionFull: `23.26.4.1.0`

Observed Oracle behaviour:

| Question | What this database did |
| --- | --- |
| Process death before dispatch | API process 1965913 ended with SIGKILL at `before_dispatch`. Dead session 26762,48200 cleaned up after 0.0s (the session left V$SESSION and its row lock was free). Restart recorded `cancelled`; an independent session read salary 11500, unchanged. |
| Process death during COMMIT (COMMIT returned, answer not recorded) | API process 1966065 ended with SIGKILL at `commit_returned`. Dead session 30989,57170 cleaned up after 0.0s (the session left V$SESSION and its row lock was free). Restart listed the session under `commitsUnknown`, with no successful commit audited. The independent session read salary 4545 (was 11500): durable. Recorded finding `applied`. |
| Process death during COMMIT (intent recorded, COMMIT not sent) | API process 1966050 ended with SIGKILL at `commit_before_send`. Dead session 30989,62035 cleaned up after 0.0s (the session left V$SESSION and its row lock was free). Restart listed the session under `commitsUnknown`. The independent session read salary 11500 (was 11500): not durable. Recorded finding `not_applied`. |
| Process death during a read | API process 1965989 ended with SIGKILL at `statement_returned`. Dead session 26762,7743 cleaned up after 0.0s (the session left V$SESSION and its row lock was free). Restart recorded `failed` with no verification required. |
| Process death during a write (applied, uncommitted, answer not recorded) | API process 1966003 ended with SIGKILL at `statement_returned`. Dead session 26762,39952 cleaned up after 0.0s (the session left V$SESSION and its row lock was free). Restart recorded `outcome_unknown` with verification required. The independent session read salary 11500 (was 11500): Oracle rolled the dead session's transaction back. Recorded finding `not_applied`. |
| Process death while a write waits inside the database | API process 1966017 ended with SIGKILL at `statement_sent`. Dead session 30989,665 cleaned up after 0.0s (the session left V$SESSION and its row lock was free). The session was waiting on `enq: TX - row lock contention (blocked by SID 11976)` when killed; immediately after the kill V$SESSION still showed it as {'status': 'ACTIVE', 'blockingSession': 11976, 'event': 'enq: TX - row lock contention', 'module': 'OracleDBHarness'}. After the lock was released the independent session read salary 11500 (was 11500). Restart recorded `outcome_unknown`. |

### Run 2026-10-14 20:38 UTC

- Harness build: `4d7e394` with the thick-mode wallet fix (branch `qual/r5-thick-vector`)
- Target: `D3 (Autonomous Database, see `databases/INVENTORY.md`), TCPS with its wallet` as `harness_app`, schema `HARNESS_APP`
- Platform: Linux-7.0.0-38-generic-x86_64-with-glibc2.43, Python 3.11.16
- driverMode: `thick`
- pythonOracledb: `4.0.2`
- oracleClient: `21.4.0.0.0`

Run R5 in thick mode: D3 primary over TCPS with its wallet, D1 second target, Instant
Client 21.4, with `HARNESS_QUAL_REQUIRE=admin,second_target`. Result: 159 passed, 21
skipped, 0 failed in 320 s (see "Thick mode over TCPS, and VECTOR binds, 2026-10-14").
Skips: 13 `stand_in_only`, 4 PostgreSQL, the restricted account (2), the lab workload
(1) and the commit-window kill, which did not land inside the window.

Database, as reported by the database:

- characterSet: `AL32UTF8`
- containerId: `172`
- containerName: `<D3 PDB name, redacted>`
- currentSchema: `HARNESS_APP`
- currentUser: `HARNESS_APP`
- databaseName: `<D3 PDB name, redacted>`
- dbUniqueName: `fce7pwwd`
- driver.driverMode: `thick`
- driver.oracleClient: `21.4.0.0.0`
- driver.pythonOracledb: `4.0.2`
- hostName: `POD`
- instanceName: `fce7pwwd2`
- isCdb: `True`
- nationalCharacterSet: `AL16UTF16`
- version: `23.26.4.1.0`
- versionFull: `23.26.4.1.0`

Observed Oracle behaviour:

| Question | What this database did |
| --- | --- |
| BLOB under a 256-byte preview limit | byteLength `20000`, preview 512 hex characters, truncated=`True` |
| BOOLEAN bind | Oracle Client 21.4.0.0.0 binds a bool as NUMBER: `ORA-00920: invalid relational operator` |
| Capabilities on this account | connect: available<br>session_schema: available<br>all_objects: available<br>explain_plan: available<br>display_cursor: available<br>v_session: available<br>v_sql: available<br>dba_tablespaces: available<br>dba_scheduler_jobs: available<br>dbms_stats: available<br>compile_objects: available |
| Compiler errors for the seeded invalid body | line 5 col 39: PL/SQL: ORA-00942: table or view does not exist; line 5 col 5: PL/SQL: SQL Statement ignored |
| Container identity | a pluggable database: CON_ID `172`, CON_NAME `<D3 PDB name, redacted>`, DB_UNIQUE_NAME `fce7pwwd`; harness reported isCdb=`True`, containerName=`<D3 PDB name, redacted>`, databaseName=`<D3 PDB name, redacted>`. |
| Database major version | **23**, not 19. This run does not qualify Oracle 19c. Full version: `23.26.4.1.0`. |
| Does DBMS_OUTPUT reach the adapter? | Yes - 1 line(s): ['headcount=3'] |
| Does DDL commit pending DML? | Yes - the row inserted before the CREATE was visible to another session without an explicit commit. |
| Does ROLLBACK TO SAVEPOINT leave the transaction open? | Adapter reports open=True; the row before the savepoint survived (1 == 1) and the row after it did not (0 == 0). |
| Does a PL/SQL block leave a transaction open? | Yes - the adapter's assumption holds. |
| Does a cancellation roll back earlier work in the same transaction? | No - the earlier insert was still pending, as assumed. |
| Does the blocking panel show a real blocked session? | Session 9160 waiting on 7762: shown as `[9160, 'HARNESS_APP', 'enq: TX - row lock contention', 6]` blocked by SID `7762`, 5.9s after the wait began |
| EMPTY_CLOB() vs NULL | EMPTY_CLOB() arrived as `{'kind': 'lob', 'preview': '', 'charLength': 0, 'truncated': False}`; a NULL CLOB as `None`; EMPTY_BLOB() as `{'kind': 'lob', 'preview': '', 'byteLength': 0, 'truncated': False}`. |
| How do TIMESTAMP columns arrive? | TIMESTAMP: `datetime.datetime(2026, 3, 1, 12, 34, 56, 789012)`<br>WITH TIME ZONE: `datetime.datetime(2026, 3, 1, 12, 34, 56, 789012)`<br>WITH LOCAL TIME ZONE: `datetime.datetime(2026, 3, 1, 12, 34, 56, 789012)`<br>Warning: `TIMESTAMP WITH TIME ZONE column(s) TS_TZ are shown without their offset: the driver returns the wall-clock time only. Select them with TO_CHAR(column, 'YYYY-MM-DD HH24:MI:SS.FF TZH:TZM') to see the offset.` |
| How does BOOLEAN arrive? | `[1, 0, None]`, column types ['DB_TYPE_NUMBER', 'DB_TYPE_NUMBER', 'DB_TYPE_NUMBER'] |
| How does NUMBER(20,10) arrive? | As `Decimal` with value `1234567890.0123456789` |
| How does VECTOR arrive? | FLOAT32 `{'kind': 'vector', 'format': None, 'length': 3, 'values': [0.100000001, 1.5, -2.0], 'truncated': False}`<br>FLOAT64 `{'kind': 'vector', 'format': None, 'length': 2, 'values': [0.1, 2.0], 'truncated': False}`<br>INT8 `{'kind': 'vector', 'format': None, 'length': 2, 'values': [1, -2], 'truncated': False}`<br>BINARY `{'kind': 'vector', 'format': None, 'length': 2, 'values': [255, 0], 'truncated': False}`<br>column types ['DB_TYPE_CLOB', 'DB_TYPE_CLOB', 'DB_TYPE_CLOB', 'DB_TYPE_CLOB'] |
| How does a SPARSE VECTOR arrive? | `{'kind': 'vector', 'format': None, 'length': 4, 'values': [0, 1.5, 0, 2.5], 'truncated': False}` |
| Is DBMS_OUTPUT bounded? | Yes - a 512-byte budget returned 8 line(s) (480 bytes) and set the truncated flag. |
| Is a lost connection during a write reported as outcome_unknown? | Raised `OutcomeUnknownError` with code `outcome_unknown`. |
| Is a session reusable after a cancellation? | Yes - the connection stayed healthy and answered a query afterwards. |
| Is an empty VARCHAR2 stored as NULL? | v_ascii read back as `None`; NVL says `'was-null'`. |
| K-4 dependency, subprogram and source lookups | rows as on the stand-in |
| K-4 trigger, link and scheduler lookups | rows as on the stand-in; no link |
| Large CLOB under a 1 KiB preview limit | reported charLength `65534`, preview is 1024 characters / 1024 bytes, truncated=`True` |
| Lost connection during COMMIT | Inconclusive - the commit completed before the session was killed. Re-run to try to hit the window. |
| NCLOB of 4,000 two-byte characters: reported length | charLength `4000`, byteLength `None` |
| PL/Scope absence | EMPLOYEE_REPORT reports NOT COLLECTED; the function is read |
| Runtime error from a PL/SQL block | Oracle code `ORA-20001`, message `ORA-20001: qualification probe<br>ORA-06512: at line 1` |
| Second target identity | separate databases on separate instances. Primary: DBID `<D3 DBID, redacted>`, CON_DBID `<D3 CON_DBID, redacted>`, CON_NAME `<D3 PDB name, redacted>`, DB_UNIQUE_NAME `fce7pwwd`, instance `fce7pwwd2` on `POD`, user `HARNESS_APP`. Second: DBID `1771485732`, CON_DBID `1771485732`, CON_NAME `ORCL`, DB_UNIQUE_NAME `ORCL`, instance `ORCL` on `90520b67e617`, user `HARNESS_APP`. |
| Shape of a small CLOB | `{'kind': 'lob', 'preview': 'short clob', 'charLength': 10, 'truncated': False}` |
| Unicode read back | `'こんにちは — café — مرحبا'` |
| VECTOR(1000, FLOAT64) under a 256-byte preview limit | elements `1000`, 32 values shown, truncated=`True` |
| What does a broken statement raise? | `CancelledError_` with code `execution_cancelled`. Returned after 3.0s of a 30s statement. |
| What does a killed session raise mid-statement? | `OutcomeUnknownError` / harness code `outcome_unknown` |
| What enforces the execution deadline? | A 5s budget stopped a 30s statement after 5.0s, raising `TimeoutError_`. |
| What happens when a block fills the DBMS_OUTPUT server buffer? | Stopped with `ORA-20000`; the next block's output was `['after']`. |

Not exercised by this run:

- Lab workloads: HARNESS_QUAL_LAB_USER is not set. The lab workload checks need a read-only account over a schema with realistic data, named by HARNESS_QUAL_LAB_SCHEMA.
- Restricted account: HARNESS_QUAL_RESTRICTED_USER is not set. Degraded panels need an account that is really missing grants; the full-grant fixture account has every capability.

### Process-death run 2026-10-14 20:40 UTC

- Harness build: `4d7e394` with the thick-mode wallet fix (branch `qual/r5-thick-vector`)
- Target: `D3 (Autonomous Database, see `databases/INVENTORY.md`), TCPS with its wallet` as `harness_app`, schema `HARNESS_APP`
- Platform: Linux-7.0.0-38-generic-x86_64-with-glibc2.43, Python 3.11.16
- driverMode: `thick`
- pythonOracledb: `4.0.2`
- oracleClient: `21.4.0.0.0`

Run R5 in thick mode on D3: `tests/integration/test_process_death.py`, inside the run
above. Result: 6 passed.

Database, as reported by the database:

- containerName: `<D3 PDB name, redacted>`
- databaseName: `<D3 PDB name, redacted>`
- isCdb: `True`
- versionFull: `23.26.4.1.0`

Observed Oracle behaviour:

| Question | What this database did |
| --- | --- |
| Process death before dispatch | API process 1973468 ended with SIGKILL at `before_dispatch`. Dead session 8469,18012 cleaned up after 0.0s (the session left V$SESSION and its row lock was free). Restart recorded `cancelled`; an independent session read salary 11500, unchanged. |
| Process death during COMMIT (COMMIT returned, answer not recorded) | API process 1973655 ended with SIGKILL at `commit_returned`. Dead session 10571,19316 cleaned up after 0.0s (the session left V$SESSION and its row lock was free). Restart listed the session under `commitsUnknown`, with no successful commit audited. The independent session read salary 4545 (was 11500): durable. Recorded finding `applied`. |
| Process death during COMMIT (intent recorded, COMMIT not sent) | API process 1973626 ended with SIGKILL at `commit_before_send`. Dead session 10571,39987 cleaned up after 0.0s (the session left V$SESSION and its row lock was free). Restart listed the session under `commitsUnknown`. The independent session read salary 11500 (was 11500): not durable. Recorded finding `not_applied`. |
| Process death during a read | API process 1973490 ended with SIGKILL at `statement_returned`. Dead session 10569,12826 cleaned up after 0.1s (the session left V$SESSION and its row lock was free). Restart recorded `failed` with no verification required. |
| Process death during a write (applied, uncommitted, answer not recorded) | API process 1973517 ended with SIGKILL at `statement_returned`. Dead session 10569,5526 cleaned up after 0.1s (the session left V$SESSION and its row lock was free). Restart recorded `outcome_unknown` with verification required. The independent session read salary 11500 (was 11500): Oracle rolled the dead session's transaction back. Recorded finding `not_applied`. |
| Process death while a write waits inside the database | API process 1973537 ended with SIGKILL at `statement_sent`. Dead session 10569,34126 cleaned up after 0.1s (the session left V$SESSION and its row lock was free). The session was waiting on `enq: TX - row lock contention (blocked by SID 14)` when killed; immediately after the kill V$SESSION still showed it as {'status': 'ACTIVE', 'blockingSession': 14, 'event': 'enq: TX - row lock contention', 'module': 'OracleDBHarness'}. After the lock was released the independent session read salary 11500 (was 11500). Restart recorded `outcome_unknown`. |

### Run 2026-10-14 22:40 UTC

- Harness build: `de33e1f`
- Target: `D4 (remote host, see `databases/INVENTORY.md`), service `ORCL`` as `harness_app`, schema `HARNESS_APP`
- Platform: Linux-7.0.0-38-generic-x86_64-with-glibc2.43, Python 3.11.16
- driverMode: `thin`
- pythonOracledb: `4.0.2`

Run R6 after the `SYS` grants: D4 primary, D1 second target, thin, the three Oracle
suites, with `HARNESS_QUAL_REQUIRE=admin,second_target,restricted_account`. Result: 824
passed, 45 skipped, 0 failed in 2,856 s (see "D4 with the `SYS` grants, 2026-10-14").
A first attempt was stopped at 16% when the server rebooted, and none of its results
are used. The skips each give their reason; the report lists `BOOLEAN`, `VECTOR`, the
PDB identity and the lab workload as not exercised.

Database, as reported by the database:

- characterSet: `WE8MSWIN1252`
- containerId: `0`
- containerName: `ORCL`
- currentSchema: `HARNESS_APP`
- currentUser: `HARNESS_APP`
- databaseName: `ORCL`
- dbUniqueName: `ORCL`
- driver.driverMode: `thin`
- driver.pythonOracledb: `4.0.2`
- hostName: `27e1d5797223`
- instanceName: `ORCL`
- isCdb: `False`
- nationalCharacterSet: `AL16UTF16`
- version: `19.9.1.0.0`
- versionFull: `19.9.1.0.0`

Observed Oracle behaviour:

| Question | What this database did |
| --- | --- |
| BLOB under a 256-byte preview limit | byteLength `20000`, preview 512 hex characters, truncated=`True` |
| Capabilities on the restricted account | connect: available<br>session_schema: available<br>all_objects: available<br>explain_plan: available<br>display_cursor: unavailable (ORA-00942: table or view does not exist<br>Help: https://docs.oracle.com/error-help/db/ora-00942/)<br>v_session: unavailable (ORA-00942: table or view does not exist<br>Help: https://docs.oracle.com/error-help/db/ora-00942/)<br>v_sql: unavailable (ORA-00942: table or view does not exist<br>Help: https://docs.oracle.com/error-help/db/ora-00942/)<br>dba_tablespaces: available<br>dba_scheduler_jobs: unavailable (ORA-00942: table or view does not exist<br>Help: https://docs.oracle.com/error-help/db/ora-00942/)<br>dbms_stats: available<br>compile_objects: available |
| Capabilities on this account | connect: available<br>session_schema: available<br>all_objects: available<br>explain_plan: available<br>display_cursor: available<br>v_session: available<br>v_sql: available<br>dba_tablespaces: available<br>dba_scheduler_jobs: available<br>dbms_stats: available<br>compile_objects: available |
| Compiler errors for the seeded invalid body | line 5 col 39: PL/SQL: ORA-00942: table or view does not exist; line 5 col 5: PL/SQL: SQL Statement ignored |
| Container identity | non-CDB: CON_ID `0`, CON_NAME `ORCL`, DB_UNIQUE_NAME `ORCL`; harness reported isCdb=`False`, containerName=`ORCL`, databaseName=`ORCL`. |
| DBA panels on the restricted account | Probe found missing: ['dba_scheduler_jobs', 'display_cursor', 'v_session', 'v_sql']<br>sessions: unavailable, `capability_unavailable`, missing ['v_session']<br>blocking: unavailable, `capability_unavailable`, missing ['v_session']<br>tablespaces: available<br>invalidObjects: available<br>schedulerJobs: unavailable, `capability_unavailable`, missing ['dba_scheduler_jobs']<br>schedulerFailures: unavailable, `capability_unavailable`, missing ['dba_scheduler_jobs'] |
| Database character set | `WE8MSWIN1252`, not AL32UTF8. Treat the Unicode bind results as specific to this character set. |
| Database major version | 19c (`19.9.1.0.0`) |
| Does DBMS_OUTPUT reach the adapter? | Yes - 1 line(s): ['headcount=3'] |
| Does DDL commit pending DML? | Yes - the row inserted before the CREATE was visible to another session without an explicit commit. |
| Does ROLLBACK TO SAVEPOINT leave the transaction open? | Adapter reports open=True; the row before the savepoint survived (1 == 1) and the row after it did not (0 == 0). |
| Does a PL/SQL block leave a transaction open? | Yes - the adapter's assumption holds. |
| Does a cancellation roll back earlier work in the same transaction? | No - the earlier insert was still pending, as assumed. |
| Does the blocking panel show a real blocked session? | Session 27 waiting on 50: shown as `[27, 'HARNESS_APP', 'enq: TX - row lock contention', 0]` blocked by SID `50`, 0.2s after the wait began |
| EMPTY_CLOB() vs NULL | EMPTY_CLOB() arrived as `{'kind': 'lob', 'preview': '', 'charLength': 0, 'truncated': False}`; a NULL CLOB as `None`; EMPTY_BLOB() as `{'kind': 'lob', 'preview': '', 'byteLength': 0, 'truncated': False}`. |
| How do TIMESTAMP columns arrive? | TIMESTAMP: `datetime.datetime(2026, 3, 1, 12, 34, 56, 789012)`<br>WITH TIME ZONE: `datetime.datetime(2026, 3, 1, 12, 34, 56, 789012)`<br>WITH LOCAL TIME ZONE: `datetime.datetime(2026, 3, 1, 12, 34, 56, 789012)`<br>Warning: `TIMESTAMP WITH TIME ZONE column(s) TS_TZ are shown without their offset: the driver returns the wall-clock time only. Select them with TO_CHAR(column, 'YYYY-MM-DD HH24:MI:SS.FF TZH:TZM') to see the offset.` |
| How does NUMBER(20,10) arrive? | As `Decimal` with value `1234567890.0123456789` |
| Is DBMS_OUTPUT bounded? | Yes - a 512-byte budget returned 8 line(s) (480 bytes) and set the truncated flag. |
| Is a lost connection during a write reported as outcome_unknown? | Raised `OutcomeUnknownError` with code `outcome_unknown`. |
| Is a session reusable after a cancellation? | Yes - the connection stayed healthy and answered a query afterwards. |
| Is an empty VARCHAR2 stored as NULL? | v_ascii read back as `None`; NVL says `'was-null'`. |
| K-4 dependency, subprogram and source lookups | rows as on the stand-in |
| K-4 trigger, link and scheduler lookups | rows as on the stand-in; no link |
| Large CLOB under a 1 KiB preview limit | reported charLength `65534`, preview is 1024 characters / 1024 bytes, truncated=`True` |
| Lost connection during COMMIT | Raised `OutcomeUnknownError` with code `outcome_unknown` |
| NCLOB of 4,000 two-byte characters: reported length | charLength `4000`, byteLength `None` |
| PL/Scope absence | EMPLOYEE_REPORT reports NOT COLLECTED; the function is read |
| Runtime error from a PL/SQL block | Oracle code `ORA-20001`, message `ORA-20001: qualification probe<br>ORA-06512: at line 1` |
| Second target identity | separate databases on separate instances. Primary: DBID `1773258920`, CON_DBID `1773258920`, CON_NAME `ORCL`, DB_UNIQUE_NAME `ORCL`, instance `ORCL` on `27e1d5797223`, user `HARNESS_APP`. Second: DBID `1771485732`, CON_DBID `1771485732`, CON_NAME `ORCL`, DB_UNIQUE_NAME `ORCL`, instance `ORCL` on `90520b67e617`, user `HARNESS_APP`. |
| Shape of a small CLOB | `{'kind': 'lob', 'preview': 'short clob', 'charLength': 10, 'truncated': False}` |
| Unicode lookup by bind | Not checked: the database character set is `WE8MSWIN1252`, not AL32UTF8, so a VARCHAR bind cannot carry the sample characters. Read-back above passed. |
| Unicode read back | `'こんにちは — café — مرحبا'` |
| What does a broken statement raise? | `CancelledError_` with code `execution_cancelled`. Returned after 3.3s of a 30s statement. |
| What does a killed session raise mid-statement? | `OutcomeUnknownError` / harness code `outcome_unknown` |
| What enforces the execution deadline? | A 5s budget stopped a 30s statement after 5.3s, raising `TimeoutError_`. |
| What happens when a block fills the DBMS_OUTPUT server buffer? | Stopped with `ORA-20000`; the next block's output was `['after']`. |

Not exercised by this run:

- BOOLEAN: the target is 19.9.1.0.0; the SQL BOOLEAN type arrived in 23ai.
- Lab workloads: HARNESS_QUAL_LAB_USER is not set. The lab workload checks need a read-only account over a schema with realistic data, named by HARNESS_QUAL_LAB_SCHEMA.
- PDB identity: the target is a non-CDB (CON_ID 0, `ORCL`).
- Thick mode: This run is thin mode; thick needs a separate process.
- VECTOR: the target is 19.9.1.0.0; the SQL VECTOR type arrived in 23ai.

### Process-death run 2026-10-14 22:29 UTC

- Harness build: `de33e1f`
- Target: `D4 (remote host, see `databases/INVENTORY.md`), service `ORCL`` as `harness_app`, schema `HARNESS_APP`
- Platform: Linux-7.0.0-38-generic-x86_64-with-glibc2.43, Python 3.11.16
- driverMode: `thin`
- pythonOracledb: `4.0.2`

Run R6 after the `SYS` grants: `tests/integration/test_process_death.py`, inside the run
above.

Database, as reported by the database:

- containerName: `ORCL`
- databaseName: `ORCL`
- isCdb: `False`
- versionFull: `19.9.1.0.0`

Observed Oracle behaviour:

| Question | What this database did |
| --- | --- |
| Process death before dispatch | API process 2011915 ended with SIGKILL at `before_dispatch`. Dead session 48,39286 cleaned up after 0.5s (the session left V$SESSION and its row lock was free). Restart recorded `cancelled`; an independent session read salary 11500, unchanged. |
| Process death during COMMIT (COMMIT returned, answer not recorded) | API process 2012132 ended with SIGKILL at `commit_returned`. Dead session 27,56004 cleaned up after 0.4s (the session left V$SESSION and its row lock was free). Restart listed the session under `commitsUnknown`, with no successful commit audited. The independent session read salary 4545 (was 11500): durable. Recorded finding `applied`. |
| Process death during COMMIT (intent recorded, COMMIT not sent) | API process 2012093 ended with SIGKILL at `commit_before_send`. Dead session 27,54308 cleaned up after 0.4s (the session left V$SESSION and its row lock was free). Restart listed the session under `commitsUnknown`. The independent session read salary 11500 (was 11500): not durable. Recorded finding `not_applied`. |
| Process death during a read | API process 2011944 ended with SIGKILL at `statement_returned`. Dead session 48,48591 cleaned up after 0.4s (the session left V$SESSION and its row lock was free). Restart recorded `failed` with no verification required. |
| Process death during a write (applied, uncommitted, answer not recorded) | API process 2011977 ended with SIGKILL at `statement_returned`. Dead session 48,39735 cleaned up after 0.4s (the session left V$SESSION and its row lock was free). Restart recorded `outcome_unknown` with verification required. The independent session read salary 11500 (was 11500): Oracle rolled the dead session's transaction back. Recorded finding `not_applied`. |
| Process death while a write waits inside the database | API process 2012074 ended with SIGKILL at `statement_sent`. Dead session 48,17694 cleaned up after 0.5s (the session left V$SESSION and its row lock was free). The session was waiting on `enq: TX - row lock contention (blocked by SID 26)` when killed; immediately after the kill V$SESSION still showed it as {'status': 'ACTIVE', 'blockingSession': 26, 'event': 'enq: TX - row lock contention', 'module': 'OracleDBHarness'}. After the lock was released the independent session read salary 11500 (was 11500). Restart recorded `outcome_unknown`. |
