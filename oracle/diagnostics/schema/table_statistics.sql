-- @id: schema.table_statistics
-- @title: Optimizer statistics recorded for a table
-- @description: Used as verification evidence after a gather-statistics runbook.
-- @capabilities: all_objects
-- @risk: read
-- @min_version: 11
-- @parameters: owner, table_name
-- @privileges: SELECT on ALL_TABLES
SELECT owner,
       table_name,
       num_rows,
       last_analyzed,
       tablespace_name
  FROM all_tables
 WHERE owner = UPPER(:owner)
   AND table_name = UPPER(:table_name)
