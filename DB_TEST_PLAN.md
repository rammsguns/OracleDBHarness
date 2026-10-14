# Testing against real databases

Status: drafted 2026-10-14. T-0 to T-6 done 2026-10-14 (R1 to R6 green; R5 approved and
run the same day). Owner: the harness maintainers.

The harness has been qualified against exactly one database: a 19c EE 19.9 non-CDB, in
thin mode, on 2026-09-11 (see `docs/compatibility.md`). The checks for a PDB, a
restricted account and a second target are written and have never been run. Oracle
Database Free (23ai/26ai) has never been connected to. The K-4 catalog entries are
qualified against the stand-in only.

This plan closes those gaps with the databases this workstation can reach, and turns
whatever they find into fixes. The machine inventory lives in `databases/`, which git
ignores because it holds credentials. Nothing in this file names a password, and none
should be added.

## Databases available

| Id | Database | Service | Role in this plan |
| --- | --- | --- | --- |
| **D1** | 19c EE 19.x, non-CDB, AL32UTF8 (container `oracle19c`) | `localhost:1522/ORCL` | Primary target; repeats the recorded baseline and closes the K-4 19c run |
| **D2** | 26ai Free 23.26, CDB, PDB `FREEPDB1` (container `oracle23ai_dataset`) | `localhost:1521/FREEPDB1` | Second target for the isolation check; primary target for the first PDB and 23ai run |
| **D3** | 26ai ADB Always Free, TCPS + wallet (Oracle Cloud) | `oracle26ialwaysfree_medium` | Shared with other projects. Used only after an explicit go-ahead, in a dedicated schema |
| **D4** | 19c EE 19.9, non-CDB, WE8MSWIN1252 (remote VPS) | remote, see `databases/INVENTORY.md` | First non-Unicode character set and first target over a real network. Provisioned as SYSTEM only (below) |

D4 was provisioned on 2026-10-14 without SYS, which is not available on it. SYSTEM
could create the three roles, grant the system privileges and the K-4 grants, and give
`HARNESS_APP` the roles and quota. It could not grant the seven `SYS` view grants or
`EXECUTE ON SYS.DBMS_STATS` (ORA-01031, as `harness_roles.sql` warns). `DBMS_STATS` is
already granted to `PUBLIC`, so that gap has no effect. `HARNESS_APP` already held
`SELECT_CATALOG_ROLE` before provisioning, so it reads the dictionary more widely than
the script intends. That is a deviation recorded here and has not been revoked.
`HARNESS_RESTRICTED` exists with `CREATE SESSION` only. It cannot read the tablespace
views until SYS grants them, so D4 runs do not claim `restricted_account`.

Later on 2026-10-14 the server's administrator ran the missing grants as `SYS`: the seven
view grants to `HARNESS_DIAGNOSTICS_ROLE`, and `DBA_TABLESPACES` and
`DBA_TABLESPACE_USAGE_METRICS` to `HARNESS_RESTRICTED`. Both match
`oracle/grants/harness_roles.sql` and the restricted shape in
`oracle/qualification/README.md`, and were checked as `SYSTEM` in `DBA_TAB_PRIVS`.
`SELECT_CATALOG_ROLE` was kept on `HARNESS_APP`, so that deviation stands. D4 runs now
claim `restricted_account`.

Lab schemas on D1 (`HR`, `OE`, `SH`, ...) and D2 (`SALES_LAB`, `HR`, `CO`, ...) hold
realistic data. **The fixtures never run in them.** Fixtures drop and recreate objects
such as `EMPLOYEES` and `DEPARTMENTS`, so they run only in a dedicated `HARNESS_APP`
schema. The lab schemas are read-only material for diagnostics, tuning and Kiwi (T-5).

## Ground rules

- Credentials stay in `databases/` (git-ignored). Password files go in
  `databases/secrets/` with mode 600, and are referenced by `HARNESS_QUAL_*_PASSWORD_FILE`.
- Grants follow `oracle/grants/harness_roles.sql` as written, run as SYS. A deviation
  from that script is a finding to record, not a silent workaround.
- Any run that is meant to count sets `HARNESS_QUAL_REQUIRE` for every environment the
  run claims to cover. If one of those is missing, the run fails instead of skipping.
- Every run writes `HARNESS_QUAL_REPORT` under `databases/reports/<date>-<run>.md`. The
  summary goes into `docs/compatibility.md`, as the 2026-09-11 runs were.
- python-oracledb stays pinned at 4.0.2 unless T-6 says otherwise.

## Phases

### T-0 Provision the local databases

| Step | D1 (19c) | D2 (FREEPDB1) |
| --- | --- | --- |
| Roles from `harness_roles.sql`, as SYS | yes | yes, inside the PDB |
| `HARNESS_APP`, all three roles plus quota | exists, has no grants: grant only | create |
| `HARNESS_RESTRICTED`: `CREATE SESSION` plus tablespace views only (README shape) | create | create |
| K-4 fixture grants to `HARNESS_APP` (`CREATE TRIGGER`, `CREATE JOB`, `DBMS_RULE_ADM` rule, rule-set and evaluation-context privileges; see `oracle/qualification/README.md`) | grant | grant, inside the PDB |
| Admin account for `KILL SESSION` | SYSTEM | SYSTEM (PDB-local session) |

Exit: `HARNESS_APP` can log in to both, the fixture scripts apply cleanly in both, and a
restricted logon succeeds in both.

### T-1 A one-command runner

Today a run means exporting about 15 variables by hand. Add `scripts/qual_run.sh <profile>
[pytest args]`, which sources a git-ignored profile `databases/qual/<profile>.env`,
creates the report directory, and runs `uv run pytest` with the `oracle` extra. Commit a
documented `databases.example/qual/*.env.example` so the shape is reviewable without
credentials.

Exit: each run below is a single command.

### T-2 Run matrix

| Run | Primary | Second | Mode | Require | Closes |
| --- | --- | --- | --- | --- | --- |
| **R1** | D1 | D2 | thin | `admin,second_target,restricted_account` | Baseline repeat on the current build; first two-target isolation and restricted-account run; K-4 catalog on 19c |
| **R2** | D2 | D1 | thin | `admin,second_target,restricted_account,pdb` | First 23ai/26ai connection; first PDB qualification |
| **R3** | D1 | D2 | thick (Instant Client 21.4 at `/opt/oracle/instantclient_21_4`) | as R1 | Thick mode, never run |
| **R4** | D1, then D2 | none | thin | none | `tests/integration/test_process_death.py`: NP-01 on Oracle, Linux host |
| **R5** | D3 | D1 | thin, TCPS + wallet | `second_target` | First TCPS/wallet path and first ADB. **Done 2026-10-14:** 160 passed, 20 skipped, after fixing wallet-password and per-endpoint transport support; thick mode 159 passed, 21 skipped, after naming the wallet in the descriptor; `ANALYZE ANY` and `ALTER ANY PROCEDURE` not granted on the shared D3. See `docs/compatibility.md`, "The D3 run" |
| **R6** | D4 | D1 | thin | `admin,second_target,restricted_account` | First non-AL32UTF8 character set and first database across a real network |

Each run executes `tests/qualification`, `tests/integration` and `tests/e2e` (the switch
in `tests/oracle_config.py` covers all three).

### T-3 Fix loop

For each failure in T-2:

1. Classify it as a harness defect, a fixture defect, an environment problem, or an
   expected version difference.
2. Reproduce it in a unit test against the stand-in where possible. The ORA-23308 chain
   fix (PR #20) is the pattern: the generator changes, and the unit check pins it.
3. Fix it, rerun the affected run, and add a row to "What the ... run found" in
   `docs/compatibility.md`.

Known candidates to check first:

- Version-gated catalog entries, PL/Scope and `DBA_*` view differences between 19c and
  23.26, and the `BOOLEAN` SQL type and `VECTOR` columns in 23ai. The LOB and bind
  suites should say how a `VECTOR` value arrives, or skip with a reason. Done
  2026-10-14, see below.
- Container identity on a PDB: `isCdb` and `containerName` are written but have never
  run.
- Diagnostics when `V$` views are read from inside a PDB: rows are scoped to the PDB, so
  check that a panel does not show an empty result as "healthy".

### T-4 Record

- `docs/compatibility.md`: move D1, D2 (and D3 if it runs) into the support matrix with
  their exact versions, mode and date. Replace "never run" for the PDB, restricted
  account, second target and thick mode with either the result or the reason it is still
  open.
- `KIWI_PLAN.md`: update K-4 status from the R1 result.

### T-5 Realistic read-only workloads (improvement work)

The fixtures prove behaviour on small, controlled objects. The lab schemas let us check
what users actually see:

- **Diagnostics and tuning on real volumes.** Register `SH` (D1) and `SALES_LAB` (D2) as
  development targets through read-only accounts. Exercise every DBA panel and the
  tuning workbench with the diagnostics role only. Expectations: bounded fetches respected,
  no panel slower than its deadline, `capability_unavailable` where a grant is missing.
- **Capacity rehearsal.** Run `tests/capacity` against D1 using
  `oracle/capacity/load_schema.sql` in `HARNESS_APP`, and record the latency figures next
  to the stand-in's.
- **Kiwi evaluation inputs.** Use real packages, jobs and tables from the lab schemas as
  K-7 cases (explain or diagnose prompts with known answers). This does not replace the
  paid-provider run and the DBA review that K-7 still needs.

### T-6 Driver upgrade probe (optional)

`docs/compatibility.md` records a `call_timeout` crash on a dropped session with
python-oracledb 26.0.0 on Windows. Repeat that probe on Linux against D1 using R4's
kill machinery. If Linux is clean, record it as Windows-specific and decide on the pin
separately. Do not unpin as part of this plan.

## Order and exit

T-0, then T-1, then R1, the T-3 loop until R1 is green, then R2, R3 and R4, then T-4.
T-5 and T-6 follow, then R5 once approved (approved and run 2026-10-14).

The plan is done when R1 to R4 pass with their `HARNESS_QUAL_REQUIRE` sets,
`docs/compatibility.md` records them, and every defect they found has a fix and a
test.

Progress, 2026-10-14: R1 to R4 pass with their require sets and are recorded (T-4).
They found one already-fixed fixture defect (PR #20), one provisioning gap (the K-4
grants, now in T-0) and two test defects (fixed in the tests themselves). No harness
defect.

T-5 lab workloads, 2026-10-14: `SH` (D1) and `SALES_LAB` (D2) pass through read-only
accounts. They found the first harness defect of this plan: paging the object list
repeated partitions and skipped objects (48 distinct of 102 on `SH`), because the order
did not tell a table's partitions apart. Fixed in `list_objects.sql`, pinned by an
integration test on the stand-in.

T-5 capacity, 2026-10-14: the rehearsal is SOUND on Oracle. Ten users ran on D1 and D2,
with the markers in `HARNESS_APP`, now dropped. There were no errors under saturation
and no contamination, and the steady overhead p95 was 53 ms against the stand-in's 49 ms.
It found two defects, both fixed with a unit test. The stand-in kept a stale transaction
flag after SQLite rolled back a transaction it had interrupted, and the Oracle backend
never reported database time. It also recorded one local limit: SQLite locks under
saturation, which only affects the stand-in. Recorded in `docs/compatibility.md`.

T-5 Kiwi inputs, 2026-10-14: nine explain cases in `tests/copilot/eval/lab_cases.json`,
on real objects read from the dictionary only (no lab object was executed, recompiled or
queried). `SALES_LAB` (D2): an error log written in the same transaction as the failing
insert, so the re-raise rolls it back; two triggers enforcing one threshold with an
undefined firing order; a NVL(SUM) that cannot tell an unknown region from an empty one;
an ON DEMAND MV with no logs. D1: `HR`'s job-history trigger reusing the hire date as a
primary-key start date, and its disabled, NLS-dependent office-hours check; an unguarded
lower bound in `OE.GET_PHONE_NUMBER_F`; three `CO` views invalidated by later table DDL
(no errors, no defect); and `SH` prebuilt MVs that ENFORCED rewrite integrity never uses.
The file is a separate `lab-*` case set and leaves the scored set, its version and its
denominator unchanged. K-7 still needs the paid-provider run and the DBA review.

T-6 driver probe, 2026-10-14: Linux is not clean, so the crash is not Windows-specific.
On D1, with SYSTEM killing a `HARNESS_APP` session (idle, or during a call), assigning
`call_timeout` after the driver saw `DPY-4011` ended the process with `SIGSEGV` in 13 of
13 attempts per case, on 26.0.0 and on the pinned 4.0.2 alike. Controls were clean: the
setter on a live session, the other calls on the dropped session, and the setter on a
session killed but not yet noticed, where `is_healthy()` is still true. `is_healthy()`
turns false at the first `DPY-4011`, the same moment the setter becomes fatal, so the
adapter's guard is the right check. 26.0.0 fixes nothing here, so the pin stays at 4.0.2.
Recorded in `docs/compatibility.md` and reported upstream as
[oracle/python-oracledb#619](https://github.com/oracle/python-oracledb/issues/619).
The probe ran from scratch virtualenvs; the repository's lock file is unchanged.

R6 on D4, 2026-10-14: the whole test suite ran, with 779 passed, 4 failed and 33 skipped
in 45 minutes. All four failures were test defects. Three Kiwi catalog tests in
`tests/copilot` read objects only the stand-in seeds; they are now `stand_in_only`, and
their Oracle versions passed. The two-target check told databases apart by name, but D4
and D1 are both `ORCL`; it now compares name, host and container. The rerun of both files
passed. Nothing failed because of `WE8MSWIN1252` or the network. No harness defect.
The full rerun after the fixes, with the `BOOLEAN`/`VECTOR` and thick-mode work below,
had 790 passed, 0 failed and 41 skipped in 46 minutes. The skips are 16
`stand_in_only` tests, 11 needing PostgreSQL, 7 needing Keycloak, the 6 23ai type
checks and the lab checks. Recorded in `docs/compatibility.md`.

`BOOLEAN` and `VECTOR`, 2026-10-14: on D2 (23.26), `BOOLEAN` arrives as JSON true,
false or null and binds from JSON, with nothing to fix. `VECTOR` was a harness defect:
the driver returns an `array.array` (or a `SparseVector`) that the API could not
serialise, so every query returning one failed. `_shape` now bounds a vector like a LOB,
the console renders it, and unit tests pin the shape. The checks pass on D2 and skip
with a reason on 19c. Recorded in `docs/compatibility.md`.

Thick mode against 23.26, 2026-10-14: R3's configuration pointed at D2 failed only the
six `BOOLEAN` and `VECTOR` checks. Instant Client 21.4 predates both types, so the
server sends a `BOOLEAN` as `NUMBER` and a `VECTOR` as a list with its format lost.
This is an expected client difference, but such a vector bypassed the preview limit,
and nothing warned the user. Both are fixed. The checks now pin the old client's
behaviour, and the rerun passed (162 passed, 17 skipped). Thick mode against D4 passed
too (155 passed, 24 skipped), with the same evidence as R6. Recorded in
`docs/compatibility.md`.

R5 on D3, 2026-10-14: the first TCPS path, wallet and managed database. Three harness
defects blocked it: no way to supply a wallet password, one transport for every
endpoint in a run, and a seed that never passed protocol or wallet to the API. With
those fixed, the first full attempt failed 2 of 180. One was the e2e registration
fixture, the third defect again. The other was the blocking panel: ADB fills in
`V$SESSION.BLOCKING_SESSION` only 6 to 15 s into a row-lock wait, so the check now waits
25 s. The rerun had 160 passed, 20 skipped and 0 failed (PR #23). `ANALYZE ANY` and
`ALTER ANY PROCEDURE` were not granted on the shared database, and no check needed them.

R5 in thick mode, 2026-10-14: a harness defect again. Thick mode ignores the wallet
connect parameters, so it could not reach a TCPS target (`ORA-28759`). A TCPS
descriptor now names the wallet directory in thick mode, and the Oracle Client opens
its auto-login `cwallet.sso`. Then 159 passed, 21 skipped and 0 failed, with the same
old-client `BOOLEAN` and `VECTOR` evidence as thick mode on D2.

`VECTOR` binds, columns and indexes, 2026-10-14, on D2 and D3 in both modes: a harness
defect. A JSON array binds as a PL/SQL array (`ORA-01484`), and the bind's `typeHint`
was never read, so no vector could be bound. `typeHint: "vector"` (or `vector:float64`,
`vector:int8`) now binds an `array.array`, or vector text, with a warning, for an Oracle
Client older than 23.4. Binds into `VECTOR` columns, `VECTOR_DISTANCE` and an IVF index
pass on both databases. HNSW passes on D3 and is a recorded gap on D2, whose Free PDB has
no vector memory pool. The console's worksheet offers the type hint on each bind. Recorded
in `docs/compatibility.md`.
