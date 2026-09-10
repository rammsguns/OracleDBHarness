-- @id: schema.list_schemas
-- @title: Accessible schemas
-- @description: Schemas owning at least one object the connected account can see.
-- @capabilities: all_objects
-- @risk: read
-- @min_version: 11
-- @privileges: SELECT on ALL_OBJECTS
SELECT owner       AS schema_name,
       COUNT(*)    AS object_count
  FROM all_objects
 GROUP BY owner
 ORDER BY owner
