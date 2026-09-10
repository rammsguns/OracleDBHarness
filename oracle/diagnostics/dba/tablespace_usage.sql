-- @id: dba.tablespace_usage
-- @title: Tablespace usage
-- @description: Used and allocated space per tablespace, converted to megabytes.
-- @capabilities: dba_tablespaces
-- @risk: read
-- @min_version: 12
-- @privileges: SELECT on DBA_TABLESPACE_USAGE_METRICS, DBA_TABLESPACES
SELECT m.tablespace_name,
       ROUND(m.used_space * t.block_size / 1048576, 2)      AS used_mb,
       ROUND(m.tablespace_size * t.block_size / 1048576, 2)  AS size_mb,
       ROUND(m.used_percent, 2)                              AS used_percent,
       t.status,
       t.contents
  FROM dba_tablespace_usage_metrics m
  JOIN dba_tablespaces t
    ON t.tablespace_name = m.tablespace_name
 ORDER BY m.used_percent DESC
