-- @id: dba.scheduler_job_detail
-- @title: Definition of one scheduler job
-- @description: What a job runs (its action, or the program it names and that program's action), its schedule, class, state and counters. When JOB_TYPE is CHAIN, PROGRAM_NAME is the chain; read it with scheduler_chain. Only jobs the connected account can see through ALL_SCHEDULER_JOBS are found.
-- @capabilities: all_objects
-- @risk: read
-- @kiwi: allowed
-- @min_version: 12
-- @parameters: owner, job_name
-- @privileges: SELECT on ALL_SCHEDULER_JOBS, SELECT on ALL_SCHEDULER_PROGRAMS
-- Dates are converted for the reason given in scheduler_jobs.sql. Credential and
-- destination names are left out; they are not needed to explain what a job does.
SELECT j.owner,
       j.job_name,
       j.job_type,
       j.job_action,
       j.program_owner,
       j.program_name,
       p.program_type,
       p.program_action,
       p.enabled AS program_enabled,
       j.schedule_type,
       j.repeat_interval,
       j.job_class,
       j.enabled,
       j.state,
       j.run_count,
       j.failure_count,
       j.max_failures,
       SYS_EXTRACT_UTC(j.last_start_date) AS last_start_utc,
       j.last_run_duration,
       SYS_EXTRACT_UTC(j.next_run_date) AS next_run_utc,
       j.comments
  FROM all_scheduler_jobs j
  LEFT JOIN all_scheduler_programs p
    ON p.owner = j.program_owner
   AND p.program_name = j.program_name
 WHERE j.owner = UPPER(:owner)
   AND j.job_name = UPPER(:job_name)
