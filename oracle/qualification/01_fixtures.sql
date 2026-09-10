-- Oracle 19c qualification fixtures.
--
-- These objects mirror what harness_worker.backend.fake seeds into the local
-- stand-in, so the same acceptance criteria can be checked against a real database.
-- Run as the harness application account, in an isolated schema on a NON-PRODUCTION
-- database. 02_teardown.sql removes everything this creates.
--
-- This script is applied by tests/qualification/fixtures.py, which splits it on the
-- '--#' separators below and runs each statement in order. Keep one statement per
-- section and do not use SQL*Plus commands: the harness never runs them.
--
-- It is idempotent. Every DROP tolerates a missing object, and every CREATE is
-- either OR REPLACE or preceded by its DROP.

--# drop_order_lines
BEGIN EXECUTE IMMEDIATE 'DROP TABLE harness_order_lines PURGE'; EXCEPTION WHEN OTHERS THEN IF SQLCODE != -942 THEN RAISE; END IF; END;

--# drop_employees
BEGIN EXECUTE IMMEDIATE 'DROP TABLE harness_employees PURGE'; EXCEPTION WHEN OTHERS THEN IF SQLCODE != -942 THEN RAISE; END IF; END;

--# drop_departments
BEGIN EXECUTE IMMEDIATE 'DROP TABLE harness_departments PURGE'; EXCEPTION WHEN OTHERS THEN IF SQLCODE != -942 THEN RAISE; END IF; END;

--# drop_types
BEGIN EXECUTE IMMEDIATE 'DROP TABLE harness_types PURGE'; EXCEPTION WHEN OTHERS THEN IF SQLCODE != -942 THEN RAISE; END IF; END;

--# drop_lobs
BEGIN EXECUTE IMMEDIATE 'DROP TABLE harness_lobs PURGE'; EXCEPTION WHEN OTHERS THEN IF SQLCODE != -942 THEN RAISE; END IF; END;

--# drop_quoted
BEGIN EXECUTE IMMEDIATE 'DROP TABLE "Harness Mixed Case" PURGE'; EXCEPTION WHEN OTHERS THEN IF SQLCODE != -942 THEN RAISE; END IF; END;

-- Reference data ----------------------------------------------------------------

--# departments
CREATE TABLE harness_departments (
  department_id   NUMBER(4)     CONSTRAINT harness_dept_pk PRIMARY KEY,
  department_name VARCHAR2(30)  NOT NULL,
  location_id     NUMBER(4)
)

--# employees
CREATE TABLE harness_employees (
  employee_id   NUMBER(6)     CONSTRAINT harness_emp_pk PRIMARY KEY,
  first_name    VARCHAR2(20),
  last_name     VARCHAR2(25)  NOT NULL,
  email         VARCHAR2(25)  NOT NULL,
  hire_date     DATE          NOT NULL,
  salary        NUMBER(8,2),
  department_id NUMBER(4)     CONSTRAINT harness_emp_dept_fk
                              REFERENCES harness_departments (department_id)
)

--# employees_index
CREATE INDEX harness_emp_department_ix ON harness_employees (department_id)

--# departments_rows
INSERT ALL
  INTO harness_departments VALUES (10, 'Administration', 1700)
  INTO harness_departments VALUES (20, 'Engineering', 1400)
  INTO harness_departments VALUES (30, 'Support', 1500)
SELECT * FROM dual

--# employees_rows
INSERT ALL
  INTO harness_employees VALUES (100, 'Ada',   'Byron',   'ADA',      DATE '2019-04-01', 12000, 20)
  INTO harness_employees VALUES (101, 'Grace', 'Hopper',  'GHOPPER',  DATE '2019-06-15', 11500, 20)
  INTO harness_employees VALUES (102, 'Ken',   'Iverson', 'KIVERSON', DATE '2020-01-20',  9000, 20)
  INTO harness_employees VALUES (103, 'Jean',  'Bartik',  'JBARTIK',  DATE '2021-03-05',  8200, 30)
  INTO harness_employees VALUES (104, 'Mary',  'Keller',  'MKELLER',  DATE '2022-08-11',  7600, 30)
  INTO harness_employees VALUES (105, 'Alan',  'Perlis',  'APERLIS',  DATE '2023-02-27', 15000, 10)
SELECT * FROM dual

-- Slow-query fixture ------------------------------------------------------------
-- Deliberately larger than the stand-in's 4,000 rows and deliberately unindexed on
-- product_id, so a full scan is measurably worse than the indexed path and a
-- cancellation has something long enough to interrupt.

--# order_lines
CREATE TABLE harness_order_lines (
  line_id     NUMBER(10)    CONSTRAINT harness_order_lines_pk PRIMARY KEY,
  order_id    NUMBER(10)    NOT NULL,
  product_id  NUMBER(6)     NOT NULL,
  quantity    NUMBER(4)     NOT NULL,
  unit_price  NUMBER(10,2)  NOT NULL,
  created_at  DATE          NOT NULL
)

--# order_lines_rows
INSERT INTO harness_order_lines (line_id, order_id, product_id, quantity, unit_price, created_at)
SELECT level,
       1000 + MOD(level, 500),
       1 + MOD(level, 40),
       1 + MOD(level, 7),
       9.99 + MOD(level, 13),
       DATE '2025-01-01' + MOD(level, 365)
  FROM dual
CONNECT BY level <= 400000

--# order_lines_order_index
CREATE INDEX harness_order_lines_ord_ix ON harness_order_lines (order_id)

--# order_lines_stats
BEGIN DBMS_STATS.GATHER_TABLE_STATS(USER, 'HARNESS_ORDER_LINES', cascade => TRUE); END;

-- Type-coverage fixture ---------------------------------------------------------
-- One row per bind and precision case the release criteria name. The stand-in has
-- SQLite's type system and cannot represent any of this.

--# types
CREATE TABLE harness_types (
  id             NUMBER(4)      CONSTRAINT harness_types_pk PRIMARY KEY,
  n_integer      NUMBER(10),
  n_scaled       NUMBER(20,10),
  n_float        BINARY_DOUBLE,
  v_ascii        VARCHAR2(100),
  v_unicode      NVARCHAR2(100),
  d_date         DATE,
  ts_plain       TIMESTAMP(6),
  ts_tz          TIMESTAMP(6) WITH TIME ZONE,
  ts_ltz         TIMESTAMP(6) WITH LOCAL TIME ZONE,
  iv_day_second  INTERVAL DAY(3) TO SECOND(6),
  r_raw          RAW(64),
  nullable_all   VARCHAR2(10)
)

--# types_rows
INSERT ALL
  INTO harness_types (id, n_integer, n_scaled, n_float, v_ascii, v_unicode, d_date,
                      ts_plain, ts_tz, ts_ltz, iv_day_second, r_raw, nullable_all)
    VALUES (1, 2147483647, 1234567890.0123456789, 1.7976931348623157E308,
            'plain ascii', N'こんにちは — café — مرحبا',
            DATE '2026-02-29' - 1,
            TIMESTAMP '2026-03-01 12:34:56.789012',
            TIMESTAMP '2026-03-01 12:34:56.789012 -08:00',
            TIMESTAMP '2026-03-01 12:34:56.789012',
            INTERVAL '3 04:05:06.789' DAY TO SECOND,
            HEXTORAW('DEADBEEF'), 'present')
  INTO harness_types (id, n_integer, n_scaled, n_float, v_ascii, v_unicode, d_date,
                      ts_plain, ts_tz, ts_ltz, iv_day_second, r_raw, nullable_all)
    VALUES (2, -2147483648, -0.0000000001, -1.0,
            '', NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL)
SELECT * FROM dual

-- LOB fixture -------------------------------------------------------------------
-- Rows larger than the configured lob_preview_bytes, so the bounded-preview limit is
-- exercised rather than assumed.

--# lobs
CREATE TABLE harness_lobs (
  id       NUMBER(4)  CONSTRAINT harness_lobs_pk PRIMARY KEY,
  label    VARCHAR2(40) NOT NULL,
  c_small  CLOB,
  c_large  CLOB,
  n_large  NCLOB,
  b_large  BLOB
)

--# lobs_rows
INSERT INTO harness_lobs (id, label, c_small, c_large, n_large, b_large)
SELECT 1,
       'under and over the preview limit',
       TO_CLOB('short clob'),
       TO_CLOB(RPAD('x', 32767, 'x')) || TO_CLOB(RPAD('y', 32767, 'y')),
       TO_NCLOB(RPAD(N'é', 4000, N'é')),
       UTL_RAW.CAST_TO_RAW(RPAD('z', 20000, 'z'))
  FROM dual

--# lobs_empty_row
INSERT INTO harness_lobs (id, label, c_small, c_large, n_large, b_large)
VALUES (2, 'empty and null lobs', EMPTY_CLOB(), NULL, NULL, EMPTY_BLOB())

-- Quoted-identifier fixture -----------------------------------------------------

--# quoted_table
CREATE TABLE "Harness Mixed Case" ("Column One" NUMBER(4), "select" VARCHAR2(20))

--# quoted_rows
INSERT INTO "Harness Mixed Case" ("Column One", "select") VALUES (1, 'reserved word')

-- PL/SQL fixture ----------------------------------------------------------------
-- The specification is valid. The body references HARNESS_EMPLOYEE, which does not
-- exist, so the body compiles INVALID on purpose: the PL/SQL workspace acceptance
-- test reads the line-level error, repairs it and recompiles. Keep the misspelling.

--# package_spec
CREATE OR REPLACE PACKAGE harness_employee_report AS
  FUNCTION headcount(p_department_id IN NUMBER) RETURN NUMBER;
  PROCEDURE report_department(p_department_id IN NUMBER);
  PROCEDURE emit_lines(p_count IN NUMBER, p_width IN NUMBER DEFAULT 40);
END harness_employee_report;

--# package_body_invalid
CREATE OR REPLACE PACKAGE BODY harness_employee_report AS
  FUNCTION headcount(p_department_id IN NUMBER) RETURN NUMBER IS
    l_count NUMBER;
  BEGIN
    SELECT COUNT(*) INTO l_count FROM harness_employee WHERE department_id = p_department_id;
    RETURN l_count;
  END headcount;
  PROCEDURE report_department(p_department_id IN NUMBER) IS
  BEGIN
    DBMS_OUTPUT.PUT_LINE('headcount=' || headcount(p_department_id));
  END report_department;
  PROCEDURE emit_lines(p_count IN NUMBER, p_width IN NUMBER DEFAULT 40) IS
  BEGIN
    FOR i IN 1 .. p_count LOOP
      DBMS_OUTPUT.PUT_LINE(LPAD(TO_CHAR(i), p_width, '.'));
    END LOOP;
  END emit_lines;
END harness_employee_report;

-- A standalone procedure used by the cancellation and connection-loss checks. It
-- burns server time without allocating, so breaking it is a clean interrupt.

--# slow_procedure
CREATE OR REPLACE PROCEDURE harness_burn(p_seconds IN NUMBER) AS
  l_deadline TIMESTAMP := SYSTIMESTAMP + NUMTODSINTERVAL(p_seconds, 'SECOND');
BEGIN
  WHILE SYSTIMESTAMP < l_deadline LOOP
    NULL;
  END LOOP;
END harness_burn;

--# commit
COMMIT
