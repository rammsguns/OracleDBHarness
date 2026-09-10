-- @id: schema.object_errors
-- @title: Compiler errors recorded for an object
-- @capabilities: compile_objects
-- @risk: read
-- @min_version: 11
-- @parameters: owner, object_name, object_type
-- @privileges: SELECT on ALL_ERRORS
SELECT line,
       position,
       text,
       attribute,
       message_number
  FROM all_errors
 WHERE owner = UPPER(:owner)
   AND name = UPPER(:object_name)
   AND (:object_type IS NULL OR type = UPPER(:object_type))
 ORDER BY sequence
