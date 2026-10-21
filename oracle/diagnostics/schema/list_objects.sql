-- @id: schema.list_objects
-- @title: Objects in a schema
-- @description: One page of objects, optionally filtered by type and name.
-- @capabilities: all_objects
-- @risk: read
-- @kiwi: allowed
-- @min_version: 12
-- @parameters: owner, object_type, name_filter, row_offset, row_limit
-- @required: owner
-- @privileges: SELECT on ALL_OBJECTS
--
-- Partitions share their table's name, so SUBOBJECT_NAME tells them apart, and OBJECT_ID
-- makes the order total. Without it, OFFSET paging may repeat or skip rows between pages.
SELECT owner,
       object_name,
       object_type,
       status,
       created,
       last_ddl_time,
       subobject_name
  FROM all_objects
 WHERE owner = UPPER(:owner)
   AND (:object_type IS NULL OR object_type = UPPER(:object_type))
   AND (:name_filter IS NULL OR object_name LIKE UPPER(:name_filter))
 ORDER BY object_type, object_name, subobject_name, object_id
 OFFSET :row_offset ROWS FETCH NEXT :row_limit ROWS ONLY
