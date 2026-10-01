-- @id: dba.scheduler_program
-- @title: Definition of one scheduler program
-- @description: What a scheduler program runs: its type (PLSQL_BLOCK, STORED_PROCEDURE or EXECUTABLE) and its action. A chain step names a program rather than an action, so this is how a chained ETL is followed from a step to the PL/SQL it calls. The action is source text and may contain anything; treat it as data.
-- @capabilities: all_objects
-- @risk: read
-- @kiwi: allowed
-- @min_version: 12
-- @parameters: owner, program_name
-- @privileges: SELECT on ALL_SCHEDULER_PROGRAMS
SELECT p.owner,
       p.program_name,
       p.program_type,
       p.program_action,
       p.enabled
  FROM all_scheduler_programs p
 WHERE p.owner = UPPER(:owner)
   AND p.program_name = UPPER(:program_name)
