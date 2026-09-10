-- @id: schema.list_objects
-- @title: Objects in a schema
-- @description: One page of objects, optionally filtered by type and name.
-- @capabilities: all_objects
-- @risk: read
-- @min_version: 12
-- @parameters: owner, object_type, name_filter, row_offset, row_limit
-- @privileges: SELECT on ALL_OBJECTS
SELECT owner,
       object_name,
       object_type,
       status,
       created,
       last_ddl_time
  FROM all_objects
 WHERE owner = UPPER(:owner)
   AND (:object_type IS NULL OR object_type = UPPER(:object_type))
   AND (:name_filter IS NULL OR object_name LIKE UPPER(:name_filter))
 ORDER BY object_type, object_name
 OFFSET :row_offset ROWS FETCH NEXT :row_limit ROWS ONLY
