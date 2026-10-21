-- Read-only grant check. Connect as the account used by a target, then run this file.
SET PAGESIZE 100 LINESIZE 180
PROMPT Harness roles (MISSING rows require DBA review)
SELECT expected.role_name,
       CASE WHEN actual.granted_role IS NULL THEN 'MISSING' ELSE 'PRESENT' END status
FROM (SELECT 'HARNESS_METADATA_ROLE' role_name FROM dual UNION ALL
      SELECT 'HARNESS_DIAGNOSTICS_ROLE' FROM dual UNION ALL
      SELECT 'HARNESS_RUNBOOK_ROLE' FROM dual) expected
LEFT JOIN user_role_privs actual ON actual.granted_role = expected.role_name
ORDER BY expected.role_name;

PROMPT Diagnostic object grants visible to this account
SELECT expected.owner, expected.object_name,
       CASE WHEN p.table_name IS NULL THEN 'MISSING' ELSE 'VISIBLE' END status
FROM (SELECT 'SYS' owner, 'V_$SESSION' object_name FROM dual UNION ALL
      SELECT 'SYS', 'V_$LOCK' FROM dual UNION ALL
      SELECT 'SYS', 'V_$SQL' FROM dual UNION ALL
      SELECT 'SYS', 'V_$SQL_PLAN' FROM dual UNION ALL
      SELECT 'SYS', 'DBA_TABLESPACES' FROM dual UNION ALL
      SELECT 'SYS', 'DBA_TABLESPACE_USAGE_METRICS' FROM dual UNION ALL
      SELECT 'SYS', 'DBMS_XPLAN' FROM dual) expected
LEFT JOIN (SELECT DISTINCT table_schema, table_name FROM all_tab_privs
           WHERE privilege IN ('SELECT', 'EXECUTE')) p
  ON p.table_schema = expected.owner AND p.table_name = expected.object_name
ORDER BY expected.object_name;

PROMPT Session privileges and quotas
SELECT privilege FROM session_privs ORDER BY privilege;
SELECT tablespace_name, bytes, max_bytes FROM user_ts_quotas ORDER BY tablespace_name;

PROMPT WARNING rows mean elevated privileges; review and revoke if unintended
SELECT 'WARNING: role ' || granted_role warning FROM user_role_privs
 WHERE granted_role IN ('DBA', 'SELECT_CATALOG_ROLE')
UNION ALL
SELECT 'WARNING: privilege ' || privilege FROM session_privs
 WHERE privilege IN ('ANALYZE ANY', 'ALTER ANY PROCEDURE');
