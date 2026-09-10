-- @id: dba.invalid_objects
-- @title: Invalid objects
-- @description: Objects whose status is not VALID, optionally limited to one schema.
-- @capabilities: all_objects
-- @risk: read
-- @min_version: 12
-- @parameters: owner, row_limit
-- @privileges: SELECT on ALL_OBJECTS
SELECT owner,
       object_name,
       object_type,
       status,
       last_ddl_time
  FROM all_objects
 WHERE status <> 'VALID'
   AND (:owner IS NULL OR owner = UPPER(:owner))
 ORDER BY owner, object_type, object_name
 FETCH FIRST :row_limit ROWS ONLY
