-- @id: schema.object_status
-- @title: Current status of one object
-- @description: Used as verification evidence after a recompile runbook.
-- @capabilities: all_objects
-- @risk: read
-- @min_version: 11
-- @parameters: owner, object_name, object_type
-- @privileges: SELECT on ALL_OBJECTS
SELECT owner,
       object_name,
       object_type,
       status,
       last_ddl_time
  FROM all_objects
 WHERE owner = UPPER(:owner)
   AND object_name = UPPER(:object_name)
   AND (:object_type IS NULL OR object_type = UPPER(:object_type))
 ORDER BY object_type
