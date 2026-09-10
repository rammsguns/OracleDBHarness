-- @id: tuning.cursor_statistics
-- @title: Statistics for one cached cursor
-- @description: Measured counters for a SQL ID, per child cursor. These are values
--               Oracle recorded for past executions, not estimates.
-- @capabilities: v_sql
-- @risk: read
-- @min_version: 12
-- @parameters: sql_id, child_number
-- @privileges: SELECT on V_$SQL
SELECT sql_id,
       child_number,
       executions,
       elapsed_time,
       cpu_time,
       buffer_gets,
       disk_reads,
       rows_processed,
       plan_hash_value,
       last_active_time,
       CASE WHEN executions > 0
            THEN ROUND(elapsed_time / executions / 1000, 3)
       END AS avg_elapsed_ms
  FROM v$sql
 WHERE sql_id = :sql_id
   AND (:child_number IS NULL OR child_number = :child_number)
 ORDER BY child_number
