-- @id: tuning.cursor_search
-- @title: Find cached cursors
-- @description: Search the cursor cache by SQL ID or statement text.
-- @capabilities: v_sql
-- @risk: read
-- @min_version: 12
-- @parameters: sql_id, text_filter, row_limit
-- @privileges: SELECT on V_$SQL
SELECT sql_id,
       child_number,
       parsing_schema_name,
       executions,
       elapsed_time,
       cpu_time,
       buffer_gets,
       disk_reads,
       rows_processed,
       plan_hash_value,
       last_active_time,
       SUBSTR(sql_text, 1, 400) AS sql_text_preview
  FROM v$sql
 WHERE (:sql_id IS NULL OR sql_id = :sql_id)
   AND (:text_filter IS NULL OR UPPER(sql_text) LIKE UPPER(:text_filter))
 ORDER BY elapsed_time DESC
 FETCH FIRST :row_limit ROWS ONLY
