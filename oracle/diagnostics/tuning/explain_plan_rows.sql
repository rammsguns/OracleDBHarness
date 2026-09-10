-- @id: tuning.explain_plan_rows
-- @title: Rows of a stored EXPLAIN PLAN
-- @description: Read back a plan produced by EXPLAIN PLAN SET STATEMENT_ID. These are
--               optimizer estimates for a statement that was never executed.
-- @capabilities: explain_plan
-- @risk: read
-- @min_version: 11
-- @parameters: statement_id
-- @privileges: SELECT, INSERT, DELETE on the session PLAN_TABLE
SELECT id,
       parent_id,
       depth,
       operation,
       options,
       object_owner,
       object_name,
       cardinality,
       bytes,
       cost,
       access_predicates,
       filter_predicates
  FROM plan_table
 WHERE statement_id = :statement_id
 ORDER BY id
