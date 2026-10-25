# Reviewed grants

`harness_roles.sql` is the reviewed privilege baseline. It is deliberately not run by
the application: a DBA runs it, reviews it, and can decline any part of it.

The baseline defines three roles:

- `harness_diagnostics_role`: required read-only diagnostics and `CREATE SESSION`.
- `harness_developer_role`: optional worksheet privileges; inherits diagnostics.
- `harness_maintenance_role`: optional reviewed runbook privileges, including
  `EXECUTE` on `SYS.DBMS_STATS`; inherits diagnostics.

Connect as the target account and run `@oracle/grants/verify_harness_grants.sql`.
It checks enabled roles (including inherited roles) and every active object grant
in the baseline, matching both the object and its `SELECT` or `EXECUTE` privilege.
Missing diagnostics are `MISSING`; absent developer/maintenance roles and the
maintenance-only `DBMS_STATS` grant are `NOT GRANTED (optional)`, which is expected
for a read-only account. The session-privilege and quota listings support DBA review;
keep reviewing the `WARNING` rows for elevated access, even on a maintenance account.

The diagnostic views are `V_$SESSION`, `V_$SQL`, `V_$SQL_PLAN`, `DBA_TABLESPACES`,
`DBA_TABLESPACE_USAGE_METRICS`, `DBA_SCHEDULER_JOBS`, and
`DBA_SCHEDULER_JOB_RUN_DETAILS`, all owned by `SYS`. Blocking diagnostics join
`V$SESSION` to itself; they do not need `V_$LOCK`. Cursor plans read `V$SQL_PLAN`
directly and do not need `DBMS_XPLAN`. Commented grants for hardened installations
and the plan table are provisioning examples, outside the active baseline check.

If a grant is missing, the affected feature reports a `capability_unavailable`
diagnostic naming the view or privilege it needed. Panels never fall back to an empty
result that looks healthy.

AWR, ASH, ADDM, SQL Tuning Advisor and Real-Time SQL Monitoring are excluded from
these roles on purpose. A technical privilege does not establish an entitlement to the
Diagnostics or Tuning Pack; see `PLAN.md`, "Performance and DBA boundaries".
