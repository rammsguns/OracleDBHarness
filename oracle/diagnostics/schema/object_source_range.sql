-- @id: schema.object_source_range
-- @title: A range of lines from stored source
-- @description: Stored source between start_line and end_line inclusive, for reading a large package a section at a time. With no start_line the range begins at line 1; with no end_line it runs to the row limit. Pair it with object_errors to read the lines around a compiler error.
-- @capabilities: all_objects
-- @risk: read
-- @kiwi: allowed
-- @min_version: 12
-- @parameters: owner, object_name, object_type, start_line, end_line, row_limit
-- @privileges: SELECT on ALL_SOURCE
SELECT line,
       text
  FROM all_source
 WHERE owner = UPPER(:owner)
   AND name = UPPER(:object_name)
   AND type = UPPER(:object_type)
   AND line >= NVL(:start_line, 1)
   AND (:end_line IS NULL OR line <= :end_line)
 ORDER BY line
 FETCH FIRST :row_limit ROWS ONLY
