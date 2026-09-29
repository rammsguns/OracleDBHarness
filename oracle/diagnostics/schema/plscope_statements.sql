-- @id: schema.plscope_statements
-- @title: PL/Scope SQL statements in a program unit
-- @description: The static SQL inside a program unit as PL/Scope recorded it: statement type, line, SQL ID and normalised text. Needs Oracle 12.2 or later; on 12.1 ALL_STATEMENTS does not exist and the lookup fails with ORA-00942. PL/Scope records statements only when the unit was compiled with STATEMENTS:ALL in PLSCOPE_SETTINGS; when there is no data the lookup returns one row per unit whose PLSCOPE_STATUS says so, and every statement column is empty. Report that status as it is. No rows at all means no such program unit is visible.
-- @capabilities: all_objects
-- @risk: read
-- @kiwi: allowed
-- @min_version: 12
-- @parameters: owner, object_name, object_type, row_offset, row_limit
-- @privileges: SELECT on ALL_STATEMENTS, SELECT on ALL_PLSQL_OBJECT_SETTINGS, SELECT on ALL_OBJECTS
-- ALL_STATEMENTS arrived in 12.2. The version gate compares major versions only, so
-- 12.1 passes it and fails in the database; the description says so. FULL_TEXT is a
-- CLOB and is left out; TEXT, returned as SQL_TEXT, is the first 1000 characters.
SELECT o.object_type,
       CASE
         WHEN st.type IS NOT NULL THEN 'COLLECTED'
         WHEN s.plscope_settings IS NULL THEN
           'UNKNOWN: no PL/SQL compiler settings are visible for this unit'
         WHEN UPPER(s.plscope_settings) LIKE '%STATEMENTS:ALL%' THEN
           'NONE RECORDED: compiled with PLSCOPE_SETTINGS=' || s.plscope_settings
           || ' but no statements are stored'
         ELSE
           'NOT COLLECTED: compiled with PLSCOPE_SETTINGS=' || s.plscope_settings
       END AS plscope_status,
       st.type AS statement_type,
       st.line,
       st.col,
       st.sql_id,
       st.text AS sql_text,
       st.has_hint,
       st.has_for_update,
       st.usage_id,
       st.usage_context_id
  FROM all_objects o
  LEFT JOIN all_plsql_object_settings s
    ON s.owner = o.owner
   AND s.name = o.object_name
   AND s.type = o.object_type
  LEFT JOIN all_statements st
    ON st.owner = o.owner
   AND st.object_name = o.object_name
   AND st.object_type = o.object_type
 WHERE o.owner = UPPER(:owner)
   AND o.object_name = UPPER(:object_name)
   AND o.object_type IN ('PACKAGE BODY', 'PROCEDURE', 'FUNCTION', 'TRIGGER', 'TYPE BODY')
   AND (:object_type IS NULL OR o.object_type = UPPER(:object_type))
 ORDER BY o.object_type, st.line, st.col, st.usage_id
OFFSET :row_offset ROWS FETCH NEXT :row_limit ROWS ONLY
