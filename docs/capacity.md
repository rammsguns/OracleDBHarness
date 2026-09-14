# Capacity runbook

The pilot target is **ten concurrent users across three independent databases** (NP-07 in
NEXT_PHASE_PLAN.md). `python -m tests.capacity` measures that: many virtual users working
through the API the way the console does, a steady phase, a saturation phase and a
recovery phase, and a check afterwards that no user's or target's work turned up anywhere
it should not.

This page is the procedure. Nothing on it has been run against the pilot; see
docs/compatibility.md for what has.

## What a run does

Each virtual user is a real harness account with a bearer token. It holds one worksheet
session per target it works on, and repeats a mix of four kinds of step:

| Kind | Request | Timings recorded |
| --- | --- | --- |
| `metadata` | `GET /api/v1/targets/{id}/schemas` - a catalog panel on its own connection | Client time only |
| `boundedRead` | The target's `boundedRead` statement in the user's session, row-limited | Client, execution and database time |
| `transaction` | Insert a marker row, then commit (80%) or roll back (20%) | Client time for the whole transaction; database time of the insert |
| `cancellation` | Start the target's `slowRead`, cancel it after `cancelAfterMs` | Client, execution and database time. Cancelled, or finished first, both count as success |

Three phases run back to back:

| Phase | Purpose |
| --- | --- |
| `steady` | The expected pilot load. Latency, overhead and error thresholds are judged here. A bounded soak is a long steady phase - run it as its own workload with the agreed soak duration, so idle expiry, connection lifetime and slow leaks have time to show |
| `saturation` | More streams than execution slots, no think time. Queueing is the point; only the listed error codes may appear |
| `recovery` | Steady load again. The report says how many seconds it took until every 10-second window met the steady thresholds |

Every kind, weight, duration, limit and threshold comes from the workload file. There are no
defaults: `tests/capacity/workload.example.json` is a template and
`python -m tests.capacity validate` refuses a file that leaves anything out.

### Where the time went

Worksheet statements report two server-side durations, so a request's time splits three ways:

- **database time** - the driver call (`databaseElapsedMs`);
- **execution time** - database time plus waiting for one of the API's execution slots
  (`elapsedMs`);
- **client time** - execution time plus network, authentication, policy and metadata-store
  writes.

The report gives p50/p95/p99 client time, p95 execution time, p50/p95 database time and p95
**application overhead** (client minus database) per phase and kind. Catalog panels report
no execution timings, so their overhead is not separated.

### Contamination checks

Transactions write marker rows naming the run, the target the runner meant to write to, the
user and a per-user sequence number (`oracle/capacity/load_schema.sql`). Afterwards each
database is read on a fresh session and compared with what the runner knows it did:

| Found | Means |
| --- | --- |
| A marker naming another target | A request reached the wrong database |
| A rolled-back marker, or one this target never committed | Uncommitted work became visible, or a write ran twice |
| A committed marker missing | A commit reported as successful is not durable |

During the run, one transaction in four is probed from **another user's** separate session
while still uncommitted (it must be invisible), and after the steady phase each user tries
to run a statement in the next user's session and to read their execution records (both
must be refused). Worksheet sessions still open after the runner closes its own count as
leaked. Every one of these is a failure at any count; `maxContaminations` must be 0.

## Before a run

1. **A disposable load deployment.** The pilot build, PostgreSQL metadata store, the
   pilot's worker pool and resource limits. Not the pilot itself, and never a production
   target: the saturation phase deliberately overloads it and the transactions write.
2. **Three independent non-production databases**, registered as three worksheet-enabled
   targets. The report records each target's identity as the database reports it; three
   profiles on one database do not count.
3. **The marker table** from `oracle/capacity/load_schema.sql` in each, reviewed by the DBA.
   Run `oracle/capacity/load_teardown.sql` when capacity testing is finished.
4. **Ten accounts**, registered with the `developer` role and `read` and `worksheet` grants
   on all three targets. Credentials, one of:
   - `tokenFiles`: one `<subject>.token` file per account in `tokenDir`, kept fresh by
     something outside the runner (it rereads a file after a 401). This exercises the
     pilot's OIDC verification.
   - `devToken`: the load deployment runs `HARNESS_AUTH_MODE=dev` with
     `HARNESS_ALLOW_DEV_AUTH_OUTSIDE_DEVELOPMENT=true`. Simpler, but token verification then
     differs from the pilot's; record that as a limitation.
5. **The target statements.** The marker statements in the example are the reference text.
   `boundedRead` and `slowRead` should reflect pilot work; the example's `slowRead` counts a
   25-million-row join over `DUAL` and holds a CPU while it runs.
6. **The network baseline.** `python -m tests.capacity baseline workload.json` from the
   load client, and again from the API host if they differ. The run records TCP connect
   times to the API and to every database endpoint, from where it runs.
7. **Agree the thresholds, then run.** The example's numbers are proposals. Measure the
   baseline, discuss the numbers with the DBA and application owner, then set
   `thresholds.status` to `agreed` with `agreedBy`, `agreedOn` and `reference`. A run
   against proposed thresholds is reported as not a qualification, however it measures.
   Changing a threshold after seeing results means a new agreement and a new run.
8. **Host metrics.** The runner records declared resource limits, not actual CPU and memory.
   Capture API host, database host and load client metrics for the run's window.

## Running it

```bash
uv run python -m tests.capacity validate workload.json     # plan and phase timeline
uv run python -m tests.capacity baseline workload.json
uv run python -m tests.capacity run workload.json --report ./capacity/pilot.md
```

Ctrl+C stops the load; verification and cleanup still run, and the report says the run was
cut short. `cleanup: true` deletes the run's markers afterwards.

`uv run python -m tests.capacity rehearse` runs the same runner for about a minute against a
local API on the stand-in, with three stand-in databases and ten accounts it provisions
itself. It checks this tooling and its report. Its exit code follows the correctness checks
only; its latencies and error codes describe the stand-in and an in-process SQLite metadata
store, and are listed as findings.

## Reading the report

The report is Markdown appended to `--report`, with a JSON sidecar. It carries the harness
commit, the run id, the workload (seed, mix, phases, declared limits, thresholds and whether
they were agreed), the API's version, environment, backend and metadata schema, each
target's reported identity, the network baseline, per-phase tables, an example message for
each error code, recovery time, verification counts, every criterion with its threshold and
measurement, and limitations. It never contains a token.

| Verdict | Meaning |
| --- | --- |
| `PASSED` | Every criterion met, all phases complete, `oracledb` backend, three distinct databases, ten users, agreed thresholds |
| `NOT A PILOT QUALIFICATION` | Criteria met, but listed reasons stop it counting |
| `FAILED` | A criterion was not met or a prerequisite was missing |
| `REHEARSAL SOUND` / `REHEARSAL FAILED` | A rehearsal, judged on correctness only |

`run` exits 0 only when every criterion was met, 1 otherwise, and 2 when the workload is
invalid or a prerequisite was missing. Record a pilot run, whatever its verdict, in
docs/compatibility.md with its report.

## Known limits

- One load client; its CPU and network bound the load it can offer.
- Copilot requests are not in the workload: provider evaluation (NP-04) has no spend
  allowance.
- Database time is the driver call as the API measures it, which includes the API-to-database
  network round trip.
- `devToken` runs do not exercise OIDC token verification.
