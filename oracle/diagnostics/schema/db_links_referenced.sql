-- @id: schema.db_links_referenced
-- @title: Database links a schema's code references
-- @description: Remote objects referenced through a database link, by the local object that references them, with the owner of the matching link (a private link in the same schema, or PUBLIC) or NOT VISIBLE. Names only: the link's username and host are deliberately not returned, and Oracle never exposes its password here.
-- @capabilities: all_objects
-- @risk: read
-- @kiwi: allowed
-- @min_version: 12
-- @parameters: owner, object_name, row_limit
-- @privileges: SELECT on ALL_DEPENDENCIES, SELECT on ALL_DB_LINKS
-- USERNAME and HOST are left out on purpose: a host can be a full connect
-- descriptor, and neither helps explain what the code depends on. The LIKE matches a
-- link name recorded without the global name domain that ALL_DB_LINKS carries.
SELECT d.name,
       d.type,
       d.referenced_link_name AS db_link,
       d.referenced_owner AS remote_owner,
       d.referenced_name AS remote_name,
       d.referenced_type AS remote_type,
       CASE WHEN l.db_link IS NULL THEN 'NOT VISIBLE' ELSE l.owner END AS link_owner
  FROM all_dependencies d
  LEFT JOIN all_db_links l
    ON l.owner IN (d.owner, 'PUBLIC')
   AND (l.db_link = d.referenced_link_name
        OR l.db_link LIKE d.referenced_link_name || '.%')
 WHERE d.owner = UPPER(:owner)
   AND d.referenced_link_name IS NOT NULL
   AND (:object_name IS NULL OR d.name = UPPER(:object_name))
 ORDER BY d.name, d.type, d.referenced_link_name, d.referenced_name
 FETCH FIRST :row_limit ROWS ONLY
