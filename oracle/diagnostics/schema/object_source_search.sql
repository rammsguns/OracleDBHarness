-- @id: schema.object_source_search
-- @title: Lines of stored source that match a pattern
-- @description: The lines of stored source whose text matches source_filter, with their line numbers, so a subprogram, rule or phrase can be found in a large body without reading it range by range. Leave out object_name to search every unit the owner has, and object_type to search every type. Read around a match with object_source_range.
-- @capabilities: all_objects
-- @risk: read
-- @kiwi: allowed
-- @min_version: 12
-- @parameters: owner, object_name, object_type, source_filter, row_limit
-- @privileges: SELECT on ALL_SOURCE
--
-- A missing source_filter matches nothing (LIKE NULL is never true) rather than
-- returning every line.
SELECT name,
       type,
       line,
       text
  FROM all_source
 WHERE owner = UPPER(:owner)
   AND (:object_name IS NULL OR name = UPPER(:object_name))
   AND (:object_type IS NULL OR type = UPPER(:object_type))
   AND UPPER(text) LIKE UPPER(:source_filter)
 ORDER BY name, type, line
 FETCH FIRST :row_limit ROWS ONLY
