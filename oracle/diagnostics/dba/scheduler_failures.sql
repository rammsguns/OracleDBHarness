-- @id: dba.scheduler_failures
-- @title: Failed scheduler job runs
-- @capabilities: dba_scheduler_jobs
-- @risk: read
-- @min_version: 12
-- @parameters: row_limit
-- @privileges: SELECT on DBA_SCHEDULER_JOB_RUN_DETAILS
SELECT owner,
       job_name,
       status,
       error_number,
       actual_start_date,
       run_duration,
       additional_info
  FROM dba_scheduler_job_run_details
 WHERE status <> 'SUCCEEDED'
 ORDER BY actual_start_date DESC
 FETCH FIRST :row_limit ROWS ONLY
