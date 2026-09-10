-- @id: tuning.cursor_plan
-- @title: Plan of a cached cursor
-- @description: The plan Oracle actually used for a cursor still in the cache.
--               Fetching it never re-runs the statement.
-- @capabilities: display_cursor
-- @risk: read
-- @min_version: 12
-- @parameters: sql_id, child_number
-- @privileges: SELECT on V_$SQL_PLAN
SELECT id,
       parent_id,
       depth,
       operation,
       options,
       object_owner,
       object_name,
       cardinality,
       bytes,
       cost,
       access_predicates,
       filter_predicates
  FROM v$sql_plan
 WHERE sql_id = :sql_id
   AND child_number = :child_number
 ORDER BY id
