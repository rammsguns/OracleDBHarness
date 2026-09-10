-- @id: schema.object_source
-- @title: Stored source for a program unit
-- @capabilities: all_objects
-- @risk: read
-- @min_version: 11
-- @parameters: owner, object_name, object_type
-- @privileges: SELECT on ALL_SOURCE
SELECT line,
       text
  FROM all_source
 WHERE owner = UPPER(:owner)
   AND name = UPPER(:object_name)
   AND type = UPPER(:object_type)
 ORDER BY line
