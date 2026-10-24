-- oracle/discovery/fleet_discovery.sql
-- Observation-only fleet discovery script for P5 preparation.
-- Compatible with SQL*Plus, SQLcl, and the Python runner (scripts/fleet_discovery.py).
--
-- STRICTLY observation-only: SELECT from views only. No DDL/DML, no state modifications.
-- NO pack-licensed features: strictly no diagnostic or tuning pack objects.
-- Tolerates ORA-00942 and ORA-01031 per section by reporting 'not visible to this account'.
--
-- SQL*Plus usage:
--   sqlplus harness_ro/<password>@target @oracle/discovery/fleet_discovery.sql
-- Opt-in to unredacted names:
--   sqlplus harness_ro/<password>@target @oracle/discovery/fleet_discovery.sql SHOW_NAMES

SET PAGESIZE 1000
SET LINESIZE 250
SET FEEDBACK OFF
SET VERIFY OFF
SET SERVEROUTPUT ON SIZE UNLIMITED
SET TRIMOUT ON
SET TRIMSPOOL ON

-- Default argument 1 to empty string if omitted
COLUMN 1 NEW_VALUE 1
SELECT '' "1" FROM dual WHERE ROWNUM = 0;
DEFINE SHOW_NAMES_OPT = '&1'

PROMPT ==============================================================================
PROMPT Fleet Discovery (Observation-Only, P5 Preparation)
PROMPT ==============================================================================
PROMPT SECTION            | KEY                            | VALUE
PROMPT -------------------+--------------------------------+-------------------------

-- @section: version
-- @query: version.instance_version
-- SELECT version, version_full, edition FROM v$instance
-- @query: version.banner_full
-- SELECT banner_full FROM v$version WHERE banner_full IS NOT NULL AND ROWNUM <= 1
-- @query: version.sqlpatch
-- SELECT patch_id, patch_uid, action, status, action_time, description FROM (SELECT patch_id, patch_uid, action, status, TO_CHAR(action_time, 'YYYY-MM-DD HH24:MI:SS') AS action_time, description FROM dba_registry_sqlpatch ORDER BY action_time DESC) WHERE ROWNUM <= 1

-- @section: rac
-- @query: rac.instance_count_gv
-- SELECT COUNT(*) AS instance_count FROM gv$instance
-- @query: rac.instance_count_v
-- SELECT COUNT(*) AS instance_count FROM v$instance
-- @query: rac.cluster_database
-- SELECT value FROM v$parameter WHERE name = 'cluster_database'
-- @query: rac.instances
-- SELECT inst_id, instance_number, instance_name, host_name FROM gv$instance ORDER BY inst_id

-- @section: data_guard
-- @query: data_guard.database_role
-- SELECT database_role, protection_mode, open_mode, force_logging, flashback_on, dataguard_broker FROM v$database
-- @query: data_guard.remote_destinations_count
-- SELECT COUNT(*) AS remote_dest_count FROM v$archive_dest_status WHERE status != 'INACTIVE' AND type = 'REMOTE'

-- @section: cdb_pdb
-- @query: cdb_pdb.cdb
-- SELECT cdb FROM v$database
-- @query: cdb_pdb.session_con
-- SELECT SYS_CONTEXT('USERENV', 'CON_ID') AS con_id, SYS_CONTEXT('USERENV', 'CON_NAME') AS con_name FROM dual
-- @query: cdb_pdb.pdb_count
-- SELECT COUNT(*) AS pdb_count FROM v$pdbs
-- @query: cdb_pdb.pdb_open_modes
-- SELECT open_mode, COUNT(*) AS mode_count FROM v$pdbs GROUP BY open_mode
-- @query: cdb_pdb.pdbs
-- SELECT con_id, name, open_mode, restricted FROM v$pdbs ORDER BY con_id

-- @section: tcps_network
-- @query: tcps_network.session_context
-- SELECT SYS_CONTEXT('USERENV', 'NETWORK_PROTOCOL') AS network_protocol, SYS_CONTEXT('USERENV', 'AUTHENTICATION_METHOD') AS authentication_method FROM dual
-- @query: tcps_network.service_banners
-- SELECT DISTINCT network_service_banner FROM v$session_connect_info WHERE sid = SYS_CONTEXT('USERENV', 'SID') AND network_service_banner IS NOT NULL ORDER BY network_service_banner

-- @section: character_sets
-- @query: character_sets.parameters
-- SELECT parameter, value FROM nls_database_parameters WHERE parameter IN ('NLS_CHARACTERSET', 'NLS_NCHAR_CHARACTERSET') ORDER BY parameter

-- @section: licensing
-- @query: licensing.pack_access
-- SELECT value FROM v$parameter WHERE name = 'control_management_pack_access'
-- @query: licensing.edition
-- SELECT edition FROM v$instance

-- @section: readiness
-- @query: readiness.connect
-- SELECT 1 FROM dual
-- @query: readiness.session_schema
-- SELECT COUNT(*) FROM user_objects WHERE ROWNUM <= 1
-- @query: readiness.all_objects
-- SELECT COUNT(*) FROM all_objects WHERE ROWNUM <= 1
-- @query: readiness.explain_plan
-- SELECT COUNT(*) FROM plan_table WHERE ROWNUM <= 1
-- @query: readiness.display_cursor
-- SELECT COUNT(*) FROM v$sql_plan WHERE ROWNUM <= 1
-- @query: readiness.v_session
-- SELECT COUNT(*) FROM v$session WHERE ROWNUM <= 1
-- @query: readiness.v_sql
-- SELECT COUNT(*) FROM v$sql WHERE ROWNUM <= 1
-- @query: readiness.dba_tablespaces
-- SELECT COUNT(*) FROM dba_tablespace_usage_metrics WHERE ROWNUM <= 1
-- @query: readiness.dba_scheduler_jobs
-- SELECT COUNT(*) FROM dba_scheduler_jobs WHERE ROWNUM <= 1
-- @query: readiness.dbms_stats
-- SELECT COUNT(*) FROM all_objects WHERE owner = 'SYS' AND object_name = 'DBMS_STATS' AND object_type = 'PACKAGE' AND ROWNUM <= 1
-- @query: readiness.compile_objects
-- SELECT COUNT(*) FROM all_errors WHERE ROWNUM <= 1

DECLARE
  v_opt_raw    VARCHAR2(100) := TRIM(UPPER('&SHOW_NAMES_OPT'));
  v_show_names BOOLEAN       := (v_opt_raw = 'SHOW_NAMES' OR v_opt_raw = '--SHOW-NAMES' OR v_opt_raw = 'TRUE');

  PROCEDURE report(p_section IN VARCHAR2, p_key IN VARCHAR2, p_val IN VARCHAR2) IS
  BEGIN
    DBMS_OUTPUT.PUT_LINE(
      RPAD(SUBSTR(NVL(p_section, 'UNKNOWN'), 1, 18), 18) || ' | ' ||
      RPAD(SUBSTR(NVL(p_key, 'UNKNOWN'), 1, 30), 30) || ' | ' ||
      NVL(p_val, '<null>')
    );
  END;

  PROCEDURE run_scalar(p_section IN VARCHAR2, p_key IN VARCHAR2, p_sql IN VARCHAR2) IS
    v_val VARCHAR2(4000);
  BEGIN
    BEGIN
      EXECUTE IMMEDIATE p_sql INTO v_val;
      report(p_section, p_key, v_val);
    EXCEPTION
      WHEN OTHERS THEN
        IF SQLCODE IN (-942, -1031) THEN
          report(p_section, p_key, 'not visible to this account');
        ELSIF SQLCODE = -904 THEN
          report(p_section, p_key, 'not supported in this database version');
        ELSIF SQLCODE = 100 THEN
          report(p_section, p_key, '<none>');
        ELSE
          report(p_section, p_key, 'error: ' || SQLERRM);
        END IF;
    END;
  END;

  PROCEDURE check_capability(p_name IN VARCHAR2, p_sql IN VARCHAR2) IS
    v_dummy NUMBER;
  BEGIN
    BEGIN
      EXECUTE IMMEDIATE p_sql INTO v_dummy;
      report('READINESS', p_name, 'available');
    EXCEPTION
      WHEN OTHERS THEN
        IF SQLCODE IN (-942, -1031) THEN
          report('READINESS', p_name, 'unavailable (not visible to this account)');
        ELSE
          report('READINESS', p_name, 'unavailable (' || SQLERRM || ')');
        END IF;
    END;
  END;

BEGIN
  -- 1. Version / RU Level
  run_scalar('VERSION', 'version', 'SELECT version FROM v$instance');
  run_scalar('VERSION', 'version_full', 'SELECT version_full FROM v$instance');
  run_scalar('VERSION', 'banner_full', 'SELECT banner_full FROM v$version WHERE banner_full IS NOT NULL AND ROWNUM <= 1');
  run_scalar('VERSION', 'latest_sqlpatch', 'SELECT patch_id || '' ('' || action || '' : '' || status || '') '' || TO_CHAR(action_time, ''YYYY-MM-DD HH24:MI:SS'') || '' - '' || description FROM (SELECT patch_id, action, status, action_time, description FROM dba_registry_sqlpatch ORDER BY action_time DESC) WHERE ROWNUM <= 1');

  -- 2. RAC
  run_scalar('RAC', 'instance_count_gv', 'SELECT TO_CHAR(COUNT(*)) FROM gv$instance');
  run_scalar('RAC', 'instance_count_v', 'SELECT TO_CHAR(COUNT(*)) FROM v$instance');
  run_scalar('RAC', 'cluster_database', 'SELECT value FROM v$parameter WHERE name = ''cluster_database''');
  IF v_show_names THEN
    run_scalar('RAC', 'instances', 'SELECT LISTAGG(instance_name || '' (inst_id='' || inst_id || '')'', '', '') WITHIN GROUP (ORDER BY inst_id) FROM gv$instance');
  ELSE
    report('RAC', 'instances', '[REDACTED: pass SHOW_NAMES to display names]');
  END IF;

  -- 3. Data Guard
  run_scalar('DATA_GUARD', 'database_role', 'SELECT database_role FROM v$database');
  run_scalar('DATA_GUARD', 'protection_mode', 'SELECT protection_mode FROM v$database');
  run_scalar('DATA_GUARD', 'open_mode', 'SELECT open_mode FROM v$database');
  run_scalar('DATA_GUARD', 'force_logging', 'SELECT force_logging FROM v$database');
  run_scalar('DATA_GUARD', 'flashback_on', 'SELECT flashback_on FROM v$database');
  run_scalar('DATA_GUARD', 'dataguard_broker', 'SELECT dataguard_broker FROM v$database');
  run_scalar('DATA_GUARD', 'remote_destinations_count', 'SELECT TO_CHAR(COUNT(*)) FROM v$archive_dest_status WHERE status != ''INACTIVE'' AND type = ''REMOTE''');

  -- 4. CDB / PDB
  run_scalar('CDB_PDB', 'cdb', 'SELECT cdb FROM v$database');
  run_scalar('CDB_PDB', 'session_con_id', 'SELECT SYS_CONTEXT(''USERENV'', ''CON_ID'') FROM dual');
  run_scalar('CDB_PDB', 'session_con_name', 'SELECT SYS_CONTEXT(''USERENV'', ''CON_NAME'') FROM dual');
  run_scalar('CDB_PDB', 'pdb_count', 'SELECT TO_CHAR(COUNT(*)) FROM v$pdbs');
  run_scalar('CDB_PDB', 'pdb_open_modes', 'SELECT ''READ WRITE='' || COUNT(CASE WHEN open_mode = ''READ WRITE'' THEN 1 END) || '', READ ONLY='' || COUNT(CASE WHEN open_mode = ''READ ONLY'' THEN 1 END) || '', MOUNTED='' || COUNT(CASE WHEN open_mode = ''MOUNTED'' THEN 1 END) FROM v$pdbs');
  IF v_show_names THEN
    run_scalar('CDB_PDB', 'pdbs', 'SELECT LISTAGG(name || '' (con_id='' || con_id || '', '' || open_mode || '')'', ''; '') WITHIN GROUP (ORDER BY con_id) FROM v$pdbs');
  ELSE
    report('CDB_PDB', 'pdbs', '[REDACTED: pass SHOW_NAMES to display names]');
  END IF;

  -- 5. TCPS / Network
  run_scalar('TCPS_NETWORK', 'network_protocol', 'SELECT SYS_CONTEXT(''USERENV'', ''NETWORK_PROTOCOL'') FROM dual');
  run_scalar('TCPS_NETWORK', 'authentication_method', 'SELECT SYS_CONTEXT(''USERENV'', ''AUTHENTICATION_METHOD'') FROM dual');
  run_scalar('TCPS_NETWORK', 'network_service_banners', 'SELECT NVL(LISTAGG(network_service_banner, '' ; '') WITHIN GROUP (ORDER BY network_service_banner), ''<none>'') FROM (SELECT DISTINCT network_service_banner FROM v$session_connect_info WHERE sid = SYS_CONTEXT(''USERENV'', ''SID'') AND network_service_banner IS NOT NULL)');

  -- 6. Character Sets
  run_scalar('CHARACTER_SETS', 'nls_characterset', 'SELECT value FROM nls_database_parameters WHERE parameter = ''NLS_CHARACTERSET''');
  run_scalar('CHARACTER_SETS', 'nls_nchar_characterset', 'SELECT value FROM nls_database_parameters WHERE parameter = ''NLS_NCHAR_CHARACTERSET''');

  -- 7. Licensing & Edition (without packs)
  run_scalar('LICENSING', 'control_management_pack_access', 'SELECT value FROM v$parameter WHERE name = ''control_management_pack_access''');
  run_scalar('LICENSING', 'edition', 'SELECT edition FROM v$instance');

  -- 8. Harness Readiness
  check_capability('connect', 'SELECT 1 FROM dual');
  check_capability('session_schema', 'SELECT COUNT(*) FROM user_objects WHERE ROWNUM <= 1');
  check_capability('all_objects', 'SELECT COUNT(*) FROM all_objects WHERE ROWNUM <= 1');
  check_capability('explain_plan', 'SELECT COUNT(*) FROM plan_table WHERE ROWNUM <= 1');
  check_capability('display_cursor', 'SELECT COUNT(*) FROM v$sql_plan WHERE ROWNUM <= 1');
  check_capability('v_session', 'SELECT COUNT(*) FROM v$session WHERE ROWNUM <= 1');
  check_capability('v_sql', 'SELECT COUNT(*) FROM v$sql WHERE ROWNUM <= 1');
  check_capability('dba_tablespaces', 'SELECT COUNT(*) FROM dba_tablespace_usage_metrics WHERE ROWNUM <= 1');
  check_capability('dba_scheduler_jobs', 'SELECT COUNT(*) FROM dba_scheduler_jobs WHERE ROWNUM <= 1');
  check_capability('dbms_stats', 'SELECT COUNT(*) FROM all_objects WHERE owner = ''SYS'' AND object_name = ''DBMS_STATS'' AND object_type = ''PACKAGE'' AND ROWNUM <= 1');
  check_capability('compile_objects', 'SELECT COUNT(*) FROM all_errors WHERE ROWNUM <= 1');

  report('SUMMARY', 'status', 'discovery complete (observation-only)');
END;
/
