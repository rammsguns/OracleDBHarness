-- @id: dba.scheduler_failures
-- @title: Failed scheduler job runs
-- @capabilities: dba_scheduler_jobs
-- @risk: read
-- @min_version: 12
-- @parameters: row_limit
-- @privileges: SELECT on DBA_SCHEDULER_JOB_RUN_DETAILS
-- The column is ERROR#; ERROR_NUMBER was ORA-00904 against 19c. The start time is
-- converted for the reason given in scheduler_jobs.sql.
SELECT owner,
       job_name,
       status,
       error# AS error_number,
       SYS_EXTRACT_UTC(actual_start_date) AS actual_start_utc,
       run_duration,
       additional_info
  FROM dba_scheduler_job_run_details
 WHERE status <> 'SUCCEEDED'
 ORDER BY actual_start_date DESC
 FETCH FIRST :row_limit ROWS ONLY
