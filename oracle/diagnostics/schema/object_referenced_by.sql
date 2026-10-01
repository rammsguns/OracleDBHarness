-- @id: schema.object_referenced_by
-- @title: Objects that depend on an object
-- @description: The reverse of object_dependencies: which views, program units and triggers reference this object, so the effect of changing it can be judged. Only dependents the connected account can see are listed.
-- @capabilities: all_objects
-- @risk: read
-- @kiwi: allowed
-- @min_version: 12
-- @parameters: owner, object_name, row_limit
-- @privileges: SELECT on ALL_DEPENDENCIES
SELECT owner,
       name,
       type,
       referenced_type
  FROM all_dependencies
 WHERE referenced_owner = UPPER(:owner)
   AND referenced_name = UPPER(:object_name)
 ORDER BY owner, name, type
 FETCH FIRST :row_limit ROWS ONLY
