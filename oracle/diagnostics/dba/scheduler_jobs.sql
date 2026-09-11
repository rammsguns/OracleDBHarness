-- @id: dba.scheduler_jobs
-- @title: Scheduler jobs
-- @capabilities: dba_scheduler_jobs
-- @risk: read
-- @min_version: 12
-- @parameters: row_limit
-- @privileges: SELECT on DBA_SCHEDULER_JOBS
-- The scheduler stores these as TIMESTAMP WITH TIME ZONE with a named region, which
-- the driver's thin mode cannot read (DPY-3022, found against 19c). SYS_EXTRACT_UTC
-- makes them plain timestamps, in UTC and labelled so.
SELECT owner,
       job_name,
       enabled,
       state,
       SYS_EXTRACT_UTC(last_start_date) AS last_start_utc,
       SYS_EXTRACT_UTC(next_run_date)   AS next_run_utc,
       failure_count
  FROM dba_scheduler_jobs
 ORDER BY failure_count DESC, owner, job_name
 FETCH FIRST :row_limit ROWS ONLY
