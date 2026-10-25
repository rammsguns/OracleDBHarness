-- Read-only grant check. Connect as the account used by a target, then run this file.
SET PAGESIZE 100 LINESIZE 180
PROMPT Harness roles (MISSING rows require DBA review; developer/maintenance are optional)
-- SESSION_ROLES includes enabled roles inherited through another role.
SELECT expected.role_name,
       CASE WHEN actual.role IS NOT NULL THEN 'PRESENT'
            WHEN expected.required = 'N' THEN 'NOT GRANTED (optional)'
            ELSE 'MISSING' END status
FROM (SELECT 'HARNESS_DIAGNOSTICS_ROLE' role_name, 'Y' required FROM dual UNION ALL
      SELECT 'HARNESS_DEVELOPER_ROLE', 'N' FROM dual UNION ALL
      SELECT 'HARNESS_MAINTENANCE_ROLE', 'N' FROM dual) expected
LEFT JOIN session_roles actual ON actual.role = expected.role_name
ORDER BY expected.role_name;

PROMPT Object grants visible to this account (maintenance-only grants are optional)
SELECT expected.owner, expected.object_name, expected.privilege,
       CASE WHEN p.table_name IS NOT NULL THEN 'VISIBLE'
            WHEN expected.required = 'N' THEN 'NOT GRANTED (optional)'
            ELSE 'MISSING' END status
FROM (SELECT 'SYS' owner, 'V_$SESSION' object_name, 'SELECT' privilege,
             'Y' required FROM dual UNION ALL
      SELECT 'SYS', 'V_$SQL', 'SELECT', 'Y' FROM dual UNION ALL
      SELECT 'SYS', 'V_$SQL_PLAN', 'SELECT', 'Y' FROM dual UNION ALL
      SELECT 'SYS', 'DBA_TABLESPACES', 'SELECT', 'Y' FROM dual UNION ALL
      SELECT 'SYS', 'DBA_TABLESPACE_USAGE_METRICS', 'SELECT', 'Y' FROM dual UNION ALL
      SELECT 'SYS', 'DBA_SCHEDULER_JOBS', 'SELECT', 'Y' FROM dual UNION ALL
      SELECT 'SYS', 'DBA_SCHEDULER_JOB_RUN_DETAILS', 'SELECT', 'Y' FROM dual UNION ALL
      SELECT 'SYS', 'DBMS_STATS', 'EXECUTE', 'N' FROM dual) expected
LEFT JOIN (SELECT DISTINCT table_schema, table_name, privilege FROM all_tab_privs
           WHERE privilege IN ('SELECT', 'EXECUTE')) p
  ON p.table_schema = expected.owner AND p.table_name = expected.object_name
 AND p.privilege = expected.privilege
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
