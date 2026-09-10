-- @id: schema.table_constraints
-- @title: Table constraints and their columns
-- @capabilities: all_objects
-- @risk: read
-- @min_version: 11
-- @parameters: owner, table_name
-- @privileges: SELECT on ALL_CONSTRAINTS, ALL_CONS_COLUMNS
SELECT c.constraint_name,
       c.constraint_type,
       c.status,
       c.r_owner,
       c.r_constraint_name,
       cc.column_name,
       cc.position
  FROM all_constraints c
  LEFT JOIN all_cons_columns cc
    ON cc.owner = c.owner
   AND cc.constraint_name = c.constraint_name
 WHERE c.owner = UPPER(:owner)
   AND c.table_name = UPPER(:table_name)
 ORDER BY c.constraint_type, c.constraint_name, cc.position
