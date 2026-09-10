-- @id: schema.table_indexes
-- @title: Indexes on a table
-- @capabilities: all_objects
-- @risk: read
-- @min_version: 11
-- @parameters: owner, table_name
-- @privileges: SELECT on ALL_INDEXES, ALL_IND_COLUMNS
SELECT i.index_name,
       i.uniqueness,
       i.status,
       ic.column_name,
       ic.column_position
  FROM all_indexes i
  LEFT JOIN all_ind_columns ic
    ON ic.index_owner = i.owner
   AND ic.index_name = i.index_name
 WHERE i.table_owner = UPPER(:owner)
   AND i.table_name = UPPER(:table_name)
 ORDER BY i.index_name, ic.column_position
