-- @id: schema.package_subprograms
-- @title: Subprograms and arguments of a package
-- @description: One row per argument of each subprogram declared in the package specification; position 0 with no argument name is a function's return value, and a subprogram without arguments has a single row with no position. Private subprograms that exist only in the body are not listed; read the body source for those.
-- @capabilities: all_objects
-- @risk: read
-- @kiwi: allowed
-- @min_version: 12
-- @parameters: owner, package_name, row_limit
-- @privileges: SELECT on ALL_PROCEDURES, SELECT on ALL_ARGUMENTS
-- DATA_LEVEL 0 keeps the declared arguments and drops the attributes of record and
-- collection types, which would otherwise repeat under every argument that uses one.
SELECT p.procedure_name AS subprogram_name,
       p.subprogram_id,
       p.overload,
       a.position,
       a.argument_name,
       a.data_type,
       a.in_out,
       a.defaulted
  FROM all_procedures p
  LEFT JOIN all_arguments a
    ON a.owner = p.owner
   AND a.package_name = p.object_name
   AND a.subprogram_id = p.subprogram_id
   AND a.data_level = 0
 WHERE p.owner = UPPER(:owner)
   AND p.object_name = UPPER(:package_name)
   AND p.object_type = 'PACKAGE'
   AND p.procedure_name IS NOT NULL
 ORDER BY p.subprogram_id, a.position
 FETCH FIRST :row_limit ROWS ONLY
