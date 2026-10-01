-- @id: dba.scheduler_chain
-- @title: Steps and rules of a scheduler chain
-- @description: A chain's steps (what each runs) and its rules (the condition that starts each step and the action taken), one row each, distinguished by ITEM_KIND. The rules are what decide the order of an ETL chain; read them before describing the flow.
-- @capabilities: all_objects
-- @risk: read
-- @kiwi: allowed
-- @min_version: 12
-- @parameters: owner, chain_name, row_limit
-- @privileges: SELECT on ALL_SCHEDULER_CHAIN_STEPS, SELECT on ALL_SCHEDULER_CHAIN_RULES
SELECT 'STEP' AS item_kind,
       s.step_name AS item_name,
       s.step_type,
       s.program_owner,
       s.program_name,
       s.skip,
       s.pause,
       CAST(NULL AS VARCHAR2(4000)) AS rule_condition,
       CAST(NULL AS VARCHAR2(4000)) AS rule_action,
       CAST(NULL AS VARCHAR2(4000)) AS comments
  FROM all_scheduler_chain_steps s
 WHERE s.owner = UPPER(:owner)
   AND s.chain_name = UPPER(:chain_name)
UNION ALL
SELECT 'RULE' AS item_kind,
       r.rule_name AS item_name,
       CAST(NULL AS VARCHAR2(30)),
       CAST(NULL AS VARCHAR2(128)),
       CAST(NULL AS VARCHAR2(128)),
       CAST(NULL AS VARCHAR2(5)),
       CAST(NULL AS VARCHAR2(5)),
       r.condition,
       r.action,
       r.comments
  FROM all_scheduler_chain_rules r
 WHERE r.owner = UPPER(:owner)
   AND r.chain_name = UPPER(:chain_name)
 ORDER BY 1 DESC, 2
 FETCH FIRST :row_limit ROWS ONLY
