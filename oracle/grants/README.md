# Reviewed grants

`harness_roles.sql` is the reviewed privilege baseline. It is deliberately not run by
the application: a DBA runs it, reviews it, and can decline any part of it.

If a grant is missing, the affected feature reports a `capability_unavailable`
diagnostic naming the view or privilege it needed. Panels never fall back to an empty
result that looks healthy.

AWR, ASH, ADDM, SQL Tuning Advisor and Real-Time SQL Monitoring are excluded from
these roles on purpose. A technical privilege does not establish an entitlement to the
Diagnostics or Tuning Pack; see `MVP_PLAN.md`, "Performance and DBA boundaries".
