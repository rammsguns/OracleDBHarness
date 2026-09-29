-- @id: schema.triggers
-- @title: Triggers in a schema or on a table
-- @description: Timing, event, target and WHEN clause of each trigger, with whether it is enabled and whether it currently compiles. Read a trigger's body with object_source and object type TRIGGER.
-- @capabilities: all_objects
-- @risk: read
-- @kiwi: allowed
-- @min_version: 12
-- @parameters: owner, table_name, row_limit
-- @privileges: SELECT on ALL_TRIGGERS, SELECT on ALL_OBJECTS
-- TRIGGER_BODY is a LONG and is left out; the same text is in ALL_SOURCE.
SELECT t.trigger_name,
       t.trigger_type,
       t.triggering_event,
       t.base_object_type,
       t.table_owner,
       t.table_name,
       t.when_clause,
       t.status,
       o.status AS object_status
  FROM all_triggers t
  LEFT JOIN all_objects o
    ON o.owner = t.owner
   AND o.object_name = t.trigger_name
   AND o.object_type = 'TRIGGER'
 WHERE t.owner = UPPER(:owner)
   AND (:table_name IS NULL OR t.table_name = UPPER(:table_name))
 ORDER BY t.table_name, t.trigger_name
 FETCH FIRST :row_limit ROWS ONLY
