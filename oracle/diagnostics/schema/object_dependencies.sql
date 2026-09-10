-- @id: schema.object_dependencies
-- @title: Objects a program unit depends on
-- @capabilities: all_objects
-- @risk: read
-- @min_version: 11
-- @parameters: owner, object_name
-- @privileges: SELECT on ALL_DEPENDENCIES
SELECT referenced_owner,
       referenced_name,
       referenced_type
  FROM all_dependencies
 WHERE owner = UPPER(:owner)
   AND name = UPPER(:object_name)
 ORDER BY referenced_owner, referenced_name
