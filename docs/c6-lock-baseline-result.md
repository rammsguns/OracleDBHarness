# C6 exclusive live-database locks and baseline rerun

agent-qualified on a production-shaped stand-in on z4g5 (AI agent, not a human)

Decision: **PROMOTE** for `baseline: no change (C6 binding check, re-run after lock fix)`.
This is AI-agent qualification evidence for the existing stand-in baseline; no new
Oracle RU or driver version was installed or qualified by this run.

The rerun began at 2026-10-25T02:41:15.819816+00:00 (20:41 CST on 2026-10-24), after BF1's live
qualification process exited. Both `pgrep -af qual_run.sh` and actual live
pytest/canary command lines were checked before starting, since the old launcher
replaced itself with pytest and disappeared from a qual_run-only process search.

| Check | Result |
| --- | --- |
| D1 thin (`r1 --d1-only`) | 160 passed, 40 skipped; exit 0 |
| D1 thick (`r3 --d1-only`) | 159 passed, 41 skipped; exit 0 |
| P1 read-only pass | success; exit 0; no budget failures |
| D1 and P1 Oracle before/after | `19.9.1.0.0`, unchanged and equal |
| python-oracledb before/after | `26.0.1`, unchanged |
| Ruff and format checks | passed |
| Mypy, CI scope | passed, 99 source files |
| Unit and canary suites | 888 passed, 8 skipped |

P1 cost: 1 session, 2.920902 seconds SQL elapsed,
102755 buffer gets and 29 executions.
P1 ran at 2026-10-25T02:48:58.096487+00:00, outside 08:50–09:30 CST.
The official C4 window directory and systemd units were not changed.

The first unit/canary run had 887 passed, 8 skipped and one failure in the existing
`test_a_statement_that_timed_out_in_the_queue_is_never_sent_to_the_database`:
it read `backend.connections[0]` immediately after starting a worker thread.
The isolated queue-test module passed all four tests on retry, and the full suite
rerun passed. No unrelated implementation or test fix was made.

Commit `f67ee97` adds shared `fcntl.flock` locks at
`$REPO/databases/locks/<DSN-identity-sha256>.lock`, acquired in sorted unique order
for primary, admin and second-target DSNs after profile overrides. Local Easy
Connect aliases normalize to the same identity. Holder description and PID are
written while locked; a contender prints them while waiting. The total wait
budget defaults to 7200 seconds and is configurable through
`HARNESS_DB_LOCK_TIMEOUT_SECONDS`. G5 version probes and P1 pass/drill commands use
the same helper. Unit tests cover naming, aliases, deduplication, ordering,
cross-process contention, release and negative timeout rejection. An independent
no-database claimant during live r1 was refused with the actual holder/PID.
Other worktrees and runners must adopt this commit to participate in the locks;
advisory locks cannot constrain an older launcher or direct database client.

Evidence remains outside Git in
`databases/reports/2026-10-25-c6-g5-baseline/`: `g5.json`, `g5.md`,
`r1.log`, `r3.log`, `p1.log`, and `passes/20261025T024858.096420Z.json`.
The earlier BLOCK was retained as `g5-interfered-20261025T0121.json` and `.md`,
annotated as invalidated for concurrent BF1/BF2 fixture interference. Its original
logs and supporting reports are preserved in
`g5-interfered-20261025T0121-evidence/`. No databases files were staged.
