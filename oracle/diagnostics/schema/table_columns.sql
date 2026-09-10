-- @id: schema.table_columns
-- @title: Table or view columns
-- @capabilities: all_objects
-- @risk: read
-- @min_version: 11
-- @parameters: owner, table_name
-- @privileges: SELECT on ALL_TAB_COLUMNS
SELECT column_id,
       column_name,
       data_type,
       data_length,
       data_precision,
       data_scale,
       nullable
  FROM all_tab_columns
 WHERE owner = UPPER(:owner)
   AND table_name = UPPER(:table_name)
 ORDER BY column_id
