# Testing against real databases

Status: drafted 2026-10-14. T-0 to T-5 done 2026-10-14 (R1 to R4 green); T-6 open;
R5 awaits approval. Owner: the harness maintainers.

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
| **R5** | D3 | D1 | thin, TCPS + wallet | `second_target` | First TCPS/wallet path and first ADB. **Gated on approval** |

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
  suites should say how a `VECTOR` value arrives, or skip with a reason.
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
T-5 and T-6 follow, and R5 waits for approval.

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

