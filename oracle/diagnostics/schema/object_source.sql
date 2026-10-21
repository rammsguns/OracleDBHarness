-- @id: schema.object_source
-- @title: Stored source for a program unit
-- @capabilities: all_objects
-- @risk: read
-- @kiwi: allowed
-- @min_version: 11
-- @parameters: owner, object_name, object_type
-- @required: owner, object_name, object_type
-- @privileges: SELECT on ALL_SOURCE
-- @object_types: FUNCTION, PROCEDURE, PACKAGE, PACKAGE BODY, TRIGGER, TYPE, TYPE BODY, LIBRARY, JAVA SOURCE, MLE MODULE
-- The TYPE values ALL_SOURCE can hold (C28), from the predicate of INT$DBA_SOURCE read
-- live on 19.9 and 23.26 (docs/compatibility.md, 2026-10-19). MLE MODULE exists from
-- 23ai only; on 19c it matches no row. ASSEMBLY is in the view's DECODE but excluded
-- by its predicate on both, so it is refused with this list, not run.
SELECT line,
       text
  FROM all_source
 WHERE owner = UPPER(:owner)
   AND name = UPPER(:object_name)
   AND type = UPPER(:object_type)
 ORDER BY line
