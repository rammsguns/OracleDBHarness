-- Removes everything 01_fixtures.sql creates.
--
-- Applied by tests/qualification/fixtures.py using the same '--#' separators. Every
-- statement tolerates a missing object, so it is safe to run against a schema that
-- was never set up, or twice.

--# drop_burn
BEGIN EXECUTE IMMEDIATE 'DROP PROCEDURE harness_burn'; EXCEPTION WHEN OTHERS THEN IF SQLCODE != -4043 THEN RAISE; END IF; END;

--# drop_package
BEGIN EXECUTE IMMEDIATE 'DROP PACKAGE harness_employee_report'; EXCEPTION WHEN OTHERS THEN IF SQLCODE != -4043 THEN RAISE; END IF; END;

--# drop_quoted
BEGIN EXECUTE IMMEDIATE 'DROP TABLE "Harness Mixed Case" PURGE'; EXCEPTION WHEN OTHERS THEN IF SQLCODE != -942 THEN RAISE; END IF; END;

--# drop_lobs
BEGIN EXECUTE IMMEDIATE 'DROP TABLE harness_lobs PURGE'; EXCEPTION WHEN OTHERS THEN IF SQLCODE != -942 THEN RAISE; END IF; END;

--# drop_types
BEGIN EXECUTE IMMEDIATE 'DROP TABLE harness_types PURGE'; EXCEPTION WHEN OTHERS THEN IF SQLCODE != -942 THEN RAISE; END IF; END;

--# drop_order_lines
BEGIN EXECUTE IMMEDIATE 'DROP TABLE harness_order_lines PURGE'; EXCEPTION WHEN OTHERS THEN IF SQLCODE != -942 THEN RAISE; END IF; END;

--# drop_employees
BEGIN EXECUTE IMMEDIATE 'DROP TABLE harness_employees PURGE'; EXCEPTION WHEN OTHERS THEN IF SQLCODE != -942 THEN RAISE; END IF; END;

--# drop_departments
BEGIN EXECUTE IMMEDIATE 'DROP TABLE harness_departments PURGE'; EXCEPTION WHEN OTHERS THEN IF SQLCODE != -942 THEN RAISE; END IF; END;

--# commit
COMMIT
