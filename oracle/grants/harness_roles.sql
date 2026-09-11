-- Reviewed role and grant setup for OracleDBHarness.
--
-- Run this as SYS on each registered target. SYSTEM is not enough: granting SELECT on
-- the SYS views below needs SYS or the grant option, and SYSTEM holds neither by
-- default, so those lines fail with ORA-01031 (found against 19c). If SYS is not
-- available, granting SELECT_CATALOG_ROLE instead of the seven view grants works, but
-- reads every dictionary view rather than the seven the diagnostics use.
--
-- Then create one database account per
-- (role, target) combination the harness needs. Do not give every application user a
-- shared DBA connection: the harness associates a credential reference with a
-- specific actor/target/capability combination.
--
-- These grants are the minimum the shipped diagnostics need. Review them against
-- your own security policy before running them; nothing here is granted implicitly
-- by the application.

-- 1. Read-only diagnostics used by the Viewer role and the DBA overview screens.
CREATE ROLE harness_diagnostics_role;
GRANT CREATE SESSION TO harness_diagnostics_role;
GRANT SELECT ON sys.v_$session                    TO harness_diagnostics_role;
GRANT SELECT ON sys.v_$sql                        TO harness_diagnostics_role;
GRANT SELECT ON sys.v_$sql_plan                   TO harness_diagnostics_role;
GRANT SELECT ON sys.dba_tablespaces               TO harness_diagnostics_role;
GRANT SELECT ON sys.dba_tablespace_usage_metrics  TO harness_diagnostics_role;
GRANT SELECT ON sys.dba_scheduler_jobs            TO harness_diagnostics_role;
GRANT SELECT ON sys.dba_scheduler_job_run_details TO harness_diagnostics_role;

-- ALL_* views are readable by PUBLIC on a default installation, but list them so a
-- hardened database makes the requirement explicit rather than failing at runtime.
-- GRANT SELECT ON sys.all_objects      TO harness_diagnostics_role;
-- GRANT SELECT ON sys.all_tab_columns  TO harness_diagnostics_role;
-- GRANT SELECT ON sys.all_source       TO harness_diagnostics_role;
-- GRANT SELECT ON sys.all_errors       TO harness_diagnostics_role;
-- GRANT SELECT ON sys.all_constraints  TO harness_diagnostics_role;
-- GRANT SELECT ON sys.all_indexes      TO harness_diagnostics_role;
-- GRANT SELECT ON sys.all_dependencies TO harness_diagnostics_role;

-- 2. Development and test worksheets. Grant this only on databases where the pilot
--    has agreed that free-form SQL is acceptable. A keyword filter is not a
--    read-only boundary, so the account itself must be constrained.
CREATE ROLE harness_developer_role;
GRANT harness_diagnostics_role TO harness_developer_role;
GRANT CREATE TABLE, CREATE VIEW, CREATE PROCEDURE, CREATE SEQUENCE
   TO harness_developer_role;
-- The plan table backs the tuning workbench. 19c auto-creates PLAN_TABLE$ in SYS and
-- exposes the PLAN_TABLE synonym; grant it explicitly if your site removed it.
-- GRANT SELECT, INSERT, DELETE ON sys.plan_table$ TO harness_developer_role;

-- 3. Reviewed maintenance runbooks. Deliberately narrow: recompiling one object and
--    gathering statistics for one table. It does not include ALTER SYSTEM, session
--    termination, or user provisioning, which are out of scope for the MVP.
CREATE ROLE harness_maintenance_role;
GRANT harness_diagnostics_role TO harness_maintenance_role;
GRANT ANALYZE ANY TO harness_maintenance_role;
GRANT ALTER ANY PROCEDURE TO harness_maintenance_role;
GRANT EXECUTE ON sys.dbms_stats TO harness_maintenance_role;

-- 4. Example accounts. Replace the identified-by clauses with your own provisioning;
--    the harness never creates database accounts and never stores their passwords.
-- CREATE USER harness_ro IDENTIFIED BY "managed elsewhere";
-- GRANT harness_diagnostics_role TO harness_ro;
-- ALTER USER harness_ro DEFAULT TABLESPACE users QUOTA 0 ON users;
