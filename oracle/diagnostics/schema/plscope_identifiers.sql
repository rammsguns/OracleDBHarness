-- @id: schema.plscope_identifiers
-- @title: PL/Scope identifiers in a program unit
-- @description: Declarations, definitions, references, calls and assignments recorded by PL/Scope, with line and column. PL/Scope records nothing unless the unit was compiled with PLSCOPE_SETTINGS containing IDENTIFIERS:ALL (or PUBLIC); when there is no data the lookup returns one row per unit whose PLSCOPE_STATUS says so and names the settings it was compiled with, and every identifier column is empty. Report that status as it is; do not infer identifiers from the source instead. No rows at all means no such program unit is visible.
-- @capabilities: all_objects
-- @risk: read
-- @kiwi: allowed
-- @min_version: 12
-- @parameters: owner, object_name, object_type, row_offset, row_limit
-- @privileges: SELECT on ALL_IDENTIFIERS, SELECT on ALL_PLSQL_OBJECT_SETTINGS, SELECT on ALL_OBJECTS
-- The outer join from ALL_OBJECTS is what makes absence visible: a unit compiled
-- without PL/Scope has no identifier rows, and an inner join would return nothing,
-- which reads the same as "no such object".
SELECT o.object_type,
       CASE
         WHEN i.name IS NOT NULL THEN 'COLLECTED'
         WHEN s.plscope_settings IS NULL THEN
           'UNKNOWN: no PL/SQL compiler settings are visible for this unit'
         WHEN UPPER(s.plscope_settings) LIKE '%IDENTIFIERS:NONE%'
           OR UPPER(s.plscope_settings) NOT LIKE '%IDENTIFIERS:%' THEN
           'NOT COLLECTED: compiled with PLSCOPE_SETTINGS=' || s.plscope_settings
         ELSE
           'NONE RECORDED: compiled with PLSCOPE_SETTINGS=' || s.plscope_settings
           || ' but no identifiers are stored'
       END AS plscope_status,
       i.name,
       i.type AS identifier_type,
       i.usage,
       i.line,
       i.col,
       i.usage_id,
       i.usage_context_id
  FROM all_objects o
  LEFT JOIN all_plsql_object_settings s
    ON s.owner = o.owner
   AND s.name = o.object_name
   AND s.type = o.object_type
  LEFT JOIN all_identifiers i
    ON i.owner = o.owner
   AND i.object_name = o.object_name
   AND i.object_type = o.object_type
 WHERE o.owner = UPPER(:owner)
   AND o.object_name = UPPER(:object_name)
   AND o.object_type IN ('PACKAGE', 'PACKAGE BODY', 'PROCEDURE', 'FUNCTION', 'TRIGGER',
                         'TYPE', 'TYPE BODY')
   AND (:object_type IS NULL OR o.object_type = UPPER(:object_type))
 ORDER BY o.object_type, i.line, i.col, i.usage_id
OFFSET :row_offset ROWS FETCH NEXT :row_limit ROWS ONLY
