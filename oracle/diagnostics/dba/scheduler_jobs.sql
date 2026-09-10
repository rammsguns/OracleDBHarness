-- @id: dba.scheduler_jobs
-- @title: Scheduler jobs
-- @capabilities: dba_scheduler_jobs
-- @risk: read
-- @min_version: 12
-- @parameters: row_limit
-- @privileges: SELECT on DBA_SCHEDULER_JOBS
SELECT owner,
       job_name,
       enabled,
       state,
       last_start_date,
       next_run_date,
       failure_count
  FROM dba_scheduler_jobs
 ORDER BY failure_count DESC, owner, job_name
 FETCH FIRST :row_limit ROWS ONLY
