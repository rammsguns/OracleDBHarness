-- K-7 remediation, step 5: Oracle checks for the code rules Kiwi's prompt gives.
--
-- Run as a privileged account against a NON-PRODUCTION database. It creates two
-- disposable schemas (K7Q_OWNER, K7Q_USER) and a role (K7Q_READ), tests each rule by
-- compiling or running the pattern the prompt prescribes, and prints one PASS/FAIL line
-- per check. It does NOT clean up: run 06_k7_codegen_teardown.sql afterwards, or use
-- scripts/k7_oracle_checks.sh, which does both, counts the lines and exits nonzero on any
-- failure. Compilation is DDL; this is a qualification script, not a Kiwi feature, and
-- Kiwi never runs anything like it.
--
-- If K7Q_OWNER, K7Q_USER or K7Q_READ already exists the script stops with K7Q-COLLISION
-- before changing anything: it will not drop an account it did not create. Any SQL or
-- connect error ends it with a nonzero exit. A run that completes prints K7Q-DONE.
--
--   sqlplus -S /nolog   (then, on stdin: CONNECT system/<password>@id, then
--                        @oracle/qualification/05_k7_codegen_checks.sql <id>)
--
-- The argument is the connect identifier the script uses to reconnect as the two
-- disposable schemas (their passwords are throwaway literals, not the system's).
SET SERVEROUTPUT ON SIZE UNLIMITED
SET FEEDBACK OFF
SET VERIFY OFF
SET LINESIZE 2000 TRIMOUT ON
WHENEVER SQLERROR EXIT FAILURE
WHENEVER OSERROR EXIT FAILURE

PROMPT == setup
DECLARE
  l_taken VARCHAR2(4000);
BEGIN
  SELECT LISTAGG(name, ', ') WITHIN GROUP (ORDER BY name) INTO l_taken FROM (
    SELECT username name FROM dba_users WHERE username IN ('K7Q_OWNER','K7Q_USER')
    UNION ALL SELECT role FROM dba_roles WHERE role = 'K7Q_READ');
  IF l_taken IS NOT NULL THEN
    DBMS_OUTPUT.PUT_LINE('K7Q-COLLISION already exists, nothing was changed: ' || l_taken);
    RAISE_APPLICATION_ERROR(-20001, 'K7Q-COLLISION ' || l_taken);
  END IF;
END;
/
-- The wrapper drops only what these markers say this run created, and nothing at all
-- unless the preflight marker shows the names were free when the run began.
PROMPT K7Q-PREFLIGHT-CLEAR
CREATE USER k7q_owner IDENTIFIED BY "K7q_Owner_1" QUOTA UNLIMITED ON users;
PROMPT K7Q-CREATED K7Q_OWNER
CREATE USER k7q_user IDENTIFIED BY "K7q_User_1" QUOTA UNLIMITED ON users;
PROMPT K7Q-CREATED K7Q_USER
GRANT CREATE SESSION, CREATE TABLE, CREATE PROCEDURE, CREATE VIEW, CREATE JOB TO k7q_owner;
GRANT CREATE SESSION, CREATE TABLE, CREATE PROCEDURE, CREATE VIEW, CREATE SYNONYM TO k7q_user;
CREATE ROLE k7q_read;
PROMPT K7Q-CREATED K7Q_READ

CREATE TABLE k7q_owner.employees (
  employee_id NUMBER PRIMARY KEY, last_name VARCHAR2(30), salary NUMBER, hire_date DATE);
CREATE TABLE k7q_owner.orders (order_id NUMBER PRIMARY KEY, order_date DATE);
CREATE TABLE k7q_owner.work_items (id NUMBER PRIMARY KEY);
INSERT INTO k7q_owner.employees VALUES (1, 'A', 5000, DATE '2020-01-01');
INSERT INTO k7q_owner.employees VALUES (2, 'B', 5000, DATE '2020-01-01');
INSERT INTO k7q_owner.employees VALUES (3, 'C', 5000, DATE '2020-01-01');
INSERT INTO k7q_owner.employees VALUES (4, 'D', 4000, DATE '2020-01-01');
INSERT INTO k7q_owner.orders VALUES (1, TIMESTAMP '2026-10-16 00:00:00');
INSERT INTO k7q_owner.orders VALUES (2, TIMESTAMP '2026-10-16 12:00:00');
INSERT INTO k7q_owner.orders VALUES (3, TIMESTAMP '2026-10-16 23:59:59');
INSERT INTO k7q_owner.orders VALUES (4, TIMESTAMP '2026-10-17 00:00:00');
COMMIT;
GRANT SELECT ON k7q_owner.employees TO k7q_read;
GRANT k7q_read TO k7q_user;

-- As the privileged account: a session can set no lock timeout for DML. The only
-- session-modifiable lock timeout is DDL_LOCK_TIMEOUT (trial 12 DIAG-03 suggested "a DML
-- lock timeout on that session").
DECLARE
  l_names VARCHAR2(4000);
BEGIN
  SELECT LISTAGG(name, ',') WITHIN GROUP (ORDER BY name) INTO l_names
    FROM v$parameter WHERE name LIKE '%lock%timeout%' AND isses_modifiable = 'TRUE';
  DBMS_OUTPUT.PUT_LINE(CASE WHEN l_names = 'ddl_lock_timeout' THEN 'PASS ' ELSE 'FAIL ' END
    || 'lock: the only session-settable lock timeout is ddl_lock_timeout -- ' || l_names);
END;
/

PROMPT == checks (run as K7Q_OWNER)
CONNECT k7q_owner/"K7q_Owner_1"@&1
SET SERVEROUTPUT ON SIZE UNLIMITED

DECLARE
  l_msg VARCHAR2(4000);
  l_n   NUMBER;
  l_batches NUMBER;
  l_detail VARCHAR2(4000);

  PROCEDURE verdict(p_name VARCHAR2, p_ok BOOLEAN, p_detail VARCHAR2 := NULL) IS
  BEGIN
    DBMS_OUTPUT.PUT_LINE(CASE WHEN p_ok THEN 'PASS ' ELSE 'FAIL ' END || p_name
      || CASE WHEN p_detail IS NOT NULL THEN ' -- ' || p_detail END);
  END;

  -- Does this DDL compile cleanly? Returns the first error text, or NULL.
  FUNCTION compiles(p_ddl VARCHAR2, p_name VARCHAR2, p_type VARCHAR2) RETURN VARCHAR2 IS
    l_err VARCHAR2(4000);
  BEGIN
    BEGIN
      EXECUTE IMMEDIATE p_ddl;
    EXCEPTION WHEN OTHERS THEN
      -- ORA-24344: the unit was stored with compilation errors; USER_ERRORS has them.
      IF SQLCODE <> -24344 THEN
        RETURN 'DDL rejected: ' || SQLERRM;
      END IF;
    END;
    SELECT MAX(text) KEEP (DENSE_RANK FIRST ORDER BY sequence) INTO l_err
      FROM user_errors WHERE name = p_name AND type = p_type;
    RETURN l_err;
  END;

  -- The bulk loop the prompt prescribes, as a standalone procedure.
  PROCEDURE make_bulk IS
  BEGIN
    EXECUTE IMMEDIATE q'[
CREATE OR REPLACE PROCEDURE k7q_bulk(p_limit IN PLS_INTEGER, p_rows OUT NUMBER, p_batches OUT NUMBER) IS
  CURSOR c IS SELECT id FROM work_items ORDER BY id;
  TYPE t_ids IS TABLE OF work_items.id%TYPE;
  l_ids t_ids;
BEGIN
  p_rows := 0; p_batches := 0;
  OPEN c;
  LOOP
    FETCH c BULK COLLECT INTO l_ids LIMIT p_limit;
    EXIT WHEN l_ids.COUNT = 0;
    p_batches := p_batches + 1;
    p_rows := p_rows + l_ids.COUNT;
  END LOOP;
  CLOSE c;
END k7q_bulk;]';
  END;
BEGIN
  -- 1. Bulk collect: LIMIT on FETCH compiles; LIMIT on SELECT ... BULK COLLECT does not.
  make_bulk;
  verdict('bulk: FETCH ... BULK COLLECT ... LIMIT compiles',
    compiles('ALTER PROCEDURE k7q_bulk COMPILE', 'K7Q_BULK', 'PROCEDURE') IS NULL);
  l_msg := compiles(q'[CREATE OR REPLACE PROCEDURE k7q_bad_bulk IS
    TYPE t IS TABLE OF NUMBER; l t;
  BEGIN
    SELECT id BULK COLLECT INTO l FROM work_items LIMIT 10;
  END;]', 'K7Q_BAD_BULK', 'PROCEDURE');
  verdict('bulk: SELECT ... BULK COLLECT ... LIMIT is rejected', l_msg IS NOT NULL, SUBSTR(l_msg, 1, 80));

  FOR t IN (SELECT 0 n, 3 lim FROM dual UNION ALL SELECT 1, 3 FROM dual UNION ALL
            SELECT 3, 3 FROM dual UNION ALL SELECT 4, 3 FROM dual UNION ALL
            SELECT 9, 3 FROM dual UNION ALL SELECT 10, 3 FROM dual) LOOP
    EXECUTE IMMEDIATE 'DELETE FROM work_items';
    FOR i IN 1 .. t.n LOOP
      EXECUTE IMMEDIATE 'INSERT INTO work_items VALUES (:i)' USING i;
    END LOOP;
    EXECUTE IMMEDIATE 'BEGIN k7q_bulk(:l, :r, :b); END;' USING IN t.lim, OUT l_n, OUT l_batches;
    verdict('bulk: ' || t.n || ' rows, limit ' || t.lim || ' -> every row processed',
      l_n = t.n AND l_batches = CEIL(t.n / t.lim), l_n || ' rows in ' || l_batches || ' batch(es)');
  END LOOP;

  -- K8: a collection variable needs a previously declared collection type.
  l_msg := compiles(q'[CREATE OR REPLACE PROCEDURE k7q_collection IS
    l_orders TABLE OF k7q_owner.orders%ROWTYPE;
  BEGIN NULL; END;]', 'K7Q_COLLECTION', 'PROCEDURE');
  verdict('collection: inline TABLE OF fails with PLS-00103',
    l_msg LIKE '%PLS-00103%', SUBSTR(l_msg, 1, 80));
  verdict('collection: named TABLE OF type and variable compile',
    compiles(q'[CREATE OR REPLACE PROCEDURE k7q_collection IS
      TYPE t IS TABLE OF k7q_owner.orders%ROWTYPE; l_orders t;
    BEGIN NULL; END;]', 'K7Q_COLLECTION', 'PROCEDURE') IS NULL);

  -- 1b. FORALL takes one DML statement, not an assignment (trial 3 DRAFT-03).
  l_msg := compiles(q'[CREATE OR REPLACE PROCEDURE k7q_bad_forall IS
    TYPE t IS TABLE OF NUMBER INDEX BY PLS_INTEGER; a t; b t;
  BEGIN
    a(1) := 1;
    FORALL i IN 1 .. a.COUNT b(i) := a(i);
  END;]', 'K7Q_BAD_FORALL', 'PROCEDURE');
  verdict('bulk: FORALL around an assignment is rejected', l_msg IS NOT NULL, SUBSTR(l_msg, 1, 80));

  -- 1c. Subqueries are SQL only: not in a WHILE condition or a RETURN (trials 1 and 2).
  l_msg := compiles(q'[CREATE OR REPLACE PROCEDURE k7q_bad_while IS
    n NUMBER := 0;
  BEGIN
    WHILE EXISTS (SELECT 1 FROM work_items WHERE id = n) LOOP n := n + 1; END LOOP;
  END;]', 'K7Q_BAD_WHILE', 'PROCEDURE');
  verdict('plsql: WHILE EXISTS (subquery) is rejected', l_msg IS NOT NULL, SUBSTR(l_msg, 1, 80));
  l_msg := compiles(q'[CREATE OR REPLACE FUNCTION k7q_bad_return RETURN NUMBER IS
  BEGIN
    RETURN (SELECT COUNT(*) FROM work_items);
  END;]', 'K7Q_BAD_RETURN', 'FUNCTION');
  verdict('plsql: RETURN (SELECT ...) is rejected', l_msg IS NOT NULL, SUBSTR(l_msg, 1, 80));

  -- 2. A bare FUNCTION is not a statement; CREATE OR REPLACE FUNCTION is.
  BEGIN
    EXECUTE IMMEDIATE 'FUNCTION k7q_f RETURN NUMBER IS BEGIN RETURN 1; END k7q_f;';
    verdict('unit: a bare FUNCTION is rejected', FALSE);
  EXCEPTION WHEN OTHERS THEN
    verdict('unit: a bare FUNCTION is rejected', TRUE, SQLERRM);
  END;
  verdict('unit: CREATE OR REPLACE FUNCTION compiles',
    compiles('CREATE OR REPLACE FUNCTION k7q_f RETURN NUMBER IS BEGIN RETURN 1; END k7q_f;',
             'K7Q_F', 'FUNCTION') IS NULL);

  -- 3. AUTHID goes before IS.
  l_msg := compiles('CREATE OR REPLACE PROCEDURE k7q_a IS AUTHID CURRENT_USER BEGIN NULL; END;',
                    'K7Q_A', 'PROCEDURE');
  verdict('authid: after IS is rejected', l_msg IS NOT NULL, SUBSTR(l_msg, 1, 80));
  verdict('authid: before IS compiles',
    compiles('CREATE OR REPLACE PROCEDURE k7q_a AUTHID CURRENT_USER IS BEGIN NULL; END;',
             'K7Q_A', 'PROCEDURE') IS NULL);

  -- 4. Declaration order in an anonymous block (compiled through DBMS_SQL-free dynamic PL/SQL).
  BEGIN
    EXECUTE IMMEDIATE 'DECLARE PROCEDURE p IS BEGIN NULL; END; v NUMBER; BEGIN p; END;';
    verdict('block: a variable after a local procedure is rejected', FALSE);
  EXCEPTION WHEN OTHERS THEN
    verdict('block: a variable after a local procedure is rejected', TRUE, SUBSTR(SQLERRM, 1, 80));
  END;
  BEGIN
    EXECUTE IMMEDIATE 'DECLARE v NUMBER; PROCEDURE p IS BEGIN NULL; END; BEGIN p; END;';
    verdict('block: variables before local procedures compile and run', TRUE);
  EXCEPTION WHEN OTHERS THEN
    verdict('block: variables before local procedures compile and run', FALSE, SQLERRM);
  END;

  -- 5. DATE predicates. The original, the equivalent range, and the naive range.
  DECLARE
    l_d DATE;
    l_orig NUMBER;
    l_eq NUMBER;
    l_naive NUMBER;
  BEGIN
    FOR b IN (SELECT 'midnight' lbl, DATE '2026-10-16' d FROM dual UNION ALL
              SELECT 'noon', TO_DATE('2026-10-16 12:00:00','YYYY-MM-DD HH24:MI:SS') FROM dual UNION ALL
              SELECT 'null', CAST(NULL AS DATE) FROM dual) LOOP
      l_d := b.d;
      SELECT COUNT(*) INTO l_orig FROM orders WHERE TRUNC(order_date) = l_d;
      SELECT COUNT(*) INTO l_eq FROM orders
       WHERE order_date >= l_d AND order_date < l_d + 1 AND l_d = TRUNC(l_d);
      SELECT COUNT(*) INTO l_naive FROM orders
       WHERE order_date >= TRUNC(l_d) AND order_date < TRUNC(l_d) + 1;
      verdict('date[' || b.lbl || ']: the equivalent rewrite matches the original',
        l_eq = l_orig, 'original ' || l_orig || ', rewrite ' || l_eq);
      IF b.lbl = 'noon' THEN
        verdict('date[noon]: the naive TRUNC(:d) range differs from the original (why the rule exists)',
          l_naive <> l_orig, 'original ' || l_orig || ', naive ' || l_naive);
      END IF;
    END LOOP;
  END;

  -- 6. Transactions: rollback to a savepoint keeps pending work; a bare ROLLBACK does not.
  EXECUTE IMMEDIATE 'INSERT INTO work_items VALUES (900)';
  EXECUTE IMMEDIATE 'SAVEPOINT k7q_sp';
  EXECUTE IMMEDIATE 'INSERT INTO work_items VALUES (901)';
  EXECUTE IMMEDIATE 'ROLLBACK TO SAVEPOINT k7q_sp';
  SELECT COUNT(*) INTO l_n FROM work_items WHERE id IN (900, 901);
  verdict('txn: ROLLBACK TO SAVEPOINT keeps the caller''s earlier uncommitted row', l_n = 1, l_n || ' row(s) left of 900/901');
  EXECUTE IMMEDIATE 'ROLLBACK';
  SELECT COUNT(*) INTO l_n FROM work_items WHERE id = 900;
  verdict('txn: a full ROLLBACK discards the caller''s pending row (why the rule exists)', l_n = 0);

  -- 6b. A test block shaped as the prompt prescribes, with one failing check: it fails
  -- (raises), its own row is gone, and the caller's earlier pending row is kept.
  EXECUTE IMMEDIATE 'INSERT INTO work_items VALUES (910)';
  BEGIN
    EXECUTE IMMEDIATE q'[
DECLARE
  l_failures PLS_INTEGER := 0;
BEGIN
  SAVEPOINT k7q_test;
  INSERT INTO work_items VALUES (911);
  IF 1 <> 2 THEN l_failures := l_failures + 1; END IF;
  ROLLBACK TO k7q_test;
  IF l_failures > 0 THEN
    RAISE_APPLICATION_ERROR(-20001, l_failures || ' check(s) failed');
  END IF;
EXCEPTION
  WHEN OTHERS THEN
    ROLLBACK TO k7q_test;
    RAISE;
END;]';
    l_msg := NULL;
  EXCEPTION WHEN OTHERS THEN
    l_msg := SQLERRM;
  END;
  SELECT COUNT(*) INTO l_n FROM work_items WHERE id IN (910, 911);
  SELECT l_n * 10 + COUNT(*) INTO l_n FROM work_items WHERE id = 910;
  verdict('test: a failed check raises, drops its own row and keeps the caller''s',
    l_msg LIKE '%ORA-20001%1 check(s) failed%' AND l_n = 11,
    NVL(SUBSTR(l_msg, 1, 60), 'no error raised') || '; rows ' || l_n);
  EXECUTE IMMEDIATE 'ROLLBACK';

  -- 6c. An aggregate SELECT ... INTO always returns one row: NULL when nothing matches,
  -- never NO_DATA_FOUND (trial 4 TEST-01 guarded a check on that exception).
  BEGIN
    SELECT MAX(id) INTO l_n FROM work_items WHERE id < 0;
    verdict('plsql: SELECT MAX(...) INTO with no matching row gives NULL, not NO_DATA_FOUND',
      l_n IS NULL);
  EXCEPTION WHEN NO_DATA_FOUND THEN
    verdict('plsql: SELECT MAX(...) INTO with no matching row gives NULL, not NO_DATA_FOUND',
      FALSE, 'NO_DATA_FOUND raised');
  END;

  -- 6d. The prescribed handler for an unexpected error in a test block: ROLLBACK TO the
  -- savepoint and RAISE. The error reaches the caller, the block's row is gone, and the
  -- caller's earlier pending row is kept.
  EXECUTE IMMEDIATE 'INSERT INTO work_items VALUES (920)';
  BEGIN
    EXECUTE IMMEDIATE q'[
DECLARE
  l_x NUMBER := 0;
BEGIN
  SAVEPOINT k7q_test;
  INSERT INTO work_items VALUES (921);
  l_x := 1 / l_x;
EXCEPTION
  WHEN OTHERS THEN
    DBMS_OUTPUT.PUT_LINE('unexpected error: ' || SQLERRM);
    ROLLBACK TO k7q_test;
    RAISE;
END;]';
    l_msg := NULL;
  EXCEPTION WHEN OTHERS THEN
    l_msg := SQLERRM;
  END;
  SELECT COUNT(*) INTO l_n FROM work_items WHERE id IN (920, 921);
  SELECT l_n * 10 + COUNT(*) INTO l_n FROM work_items WHERE id = 920;
  verdict('test: an unexpected error is re-raised, drops the block''s row and keeps the caller''s',
    l_msg LIKE '%ORA-01476%' AND l_n = 11,
    NVL(SUBSTR(l_msg, 1, 60), 'no error raised') || '; rows ' || l_n);
  EXECUTE IMMEDIATE 'ROLLBACK';

  -- 6e. Read consistency: a cursor's rows are fixed when it opens, so rows the loop
  -- deletes are still fetched (trial 4 KINJ-01 said such a loop can skip rows).
  EXECUTE IMMEDIATE 'DELETE FROM work_items';
  FOR i IN 1 .. 5 LOOP
    EXECUTE IMMEDIATE 'INSERT INTO work_items VALUES (:i)' USING i;
  END LOOP;
  l_n := 0;
  FOR r IN (SELECT id FROM work_items ORDER BY id) LOOP
    l_n := l_n + 1;
    DELETE FROM work_items WHERE id > r.id;
  END LOOP;
  verdict('cursor: rows deleted inside the loop are still fetched (read consistency)',
    l_n = 5, l_n || ' of 5 rows fetched');
  EXECUTE IMMEDIATE 'ROLLBACK';

  -- 7. Arithmetic at the boundary, and ranking ties.
  EXECUTE IMMEDIATE 'SELECT (1000 + 1) * 1000 FROM dual' INTO l_n;
  verdict('arith: (1000+N)*1000 at N=1', l_n = 1001000, TO_CHAR(l_n));
  SELECT COUNT(*) INTO l_n FROM (
    SELECT ROW_NUMBER() OVER (ORDER BY salary DESC, employee_id) rn FROM employees) WHERE rn <= 3;
  verdict('ranking: ROW_NUMBER with an employee_id tie-break returns exactly three', l_n = 3);
  SELECT COUNT(*) INTO l_n FROM (
    SELECT RANK() OVER (ORDER BY salary DESC) rk FROM employees) WHERE rk <= 1;
  verdict('ranking: RANK returns every row tied for first (3), where ROW_NUMBER returns 1', l_n = 3);

  -- 8. Package spec and body must agree; a complete pair compiles.
  verdict('package: spec compiles',
    compiles('CREATE OR REPLACE PACKAGE k7q_pkg AS FUNCTION f RETURN NUMBER; END k7q_pkg;', 'K7Q_PKG', 'PACKAGE') IS NULL);
  l_msg := compiles('CREATE OR REPLACE PACKAGE BODY k7q_pkg AS FUNCTION g RETURN NUMBER IS BEGIN RETURN 1; END g; END k7q_pkg;',
                    'K7Q_PKG', 'PACKAGE BODY');
  verdict('package: a body missing the spec''s function is rejected', l_msg IS NOT NULL, SUBSTR(l_msg, 1, 80));
  verdict('package: a complete body compiles',
    compiles('CREATE OR REPLACE PACKAGE BODY k7q_pkg AS FUNCTION f RETURN NUMBER IS BEGIN RETURN 1; END f; END k7q_pkg;',
             'K7Q_PKG', 'PACKAGE BODY') IS NULL);

  -- 9. Scheduler: DBMS_SCHEDULER.DISABLE works; ALTER JOB ... DISABLE is not SQL; DBMS_ROWID exists.
  DBMS_SCHEDULER.CREATE_JOB(job_name => 'K7Q_JOB', job_type => 'PLSQL_BLOCK',
    job_action => 'BEGIN NULL; END;', enabled => TRUE);
  DBMS_SCHEDULER.DISABLE('K7Q_OWNER.K7Q_JOB');
  SELECT COUNT(*) INTO l_n FROM user_scheduler_jobs WHERE job_name = 'K7Q_JOB' AND enabled = 'FALSE';
  verdict('scheduler: DBMS_SCHEDULER.DISABLE(''OWNER.JOB'') disables the job', l_n = 1);
  BEGIN
    EXECUTE IMMEDIATE 'ALTER JOB k7q_job DISABLE';
    verdict('scheduler: ALTER JOB ... DISABLE is not valid syntax', FALSE);
  EXCEPTION WHEN OTHERS THEN
    verdict('scheduler: ALTER JOB ... DISABLE is not valid syntax', TRUE, SQLERRM);
  END;
  SELECT COUNT(*) INTO l_n FROM all_objects WHERE owner = 'SYS' AND object_name = 'DBMS_ROWID' AND object_type = 'PACKAGE';
  verdict('rowid: DBMS_ROWID exists', l_n = 1);
  SELECT COUNT(*) INTO l_n FROM all_objects WHERE owner = 'SYS' AND object_name = 'DBMS_ROW_ID';
  verdict('rowid: DBMS_ROW_ID does not exist', l_n = 0);
  DBMS_SCHEDULER.DROP_JOB('K7Q_JOB', force => TRUE);

  -- 9b. MAX_FAILURES NULL: a failed run leaves the job enabled; nothing disables it, so
  -- nothing has to re-enable it (trial 4 DIAG-02).
  DBMS_SCHEDULER.CREATE_JOB(job_name => 'K7Q_FAILJOB', job_type => 'PLSQL_BLOCK',
    job_action => q'[BEGIN RAISE_APPLICATION_ERROR(-20001, 'destination unavailable'); END;]',
    start_date => SYSTIMESTAMP + INTERVAL '1' DAY, repeat_interval => 'FREQ=DAILY',
    enabled => TRUE);
  BEGIN
    DBMS_SCHEDULER.RUN_JOB('K7Q_FAILJOB', use_current_session => TRUE);
    l_msg := 'the run did not fail';
  EXCEPTION WHEN OTHERS THEN
    l_msg := SQLERRM;
  END;
  SELECT COUNT(*) INTO l_n FROM user_scheduler_jobs
   WHERE job_name = 'K7Q_FAILJOB' AND enabled = 'TRUE' AND max_failures IS NULL;
  verdict('scheduler: with MAX_FAILURES NULL a failed run leaves the job enabled',
    l_n = 1 AND l_msg LIKE '%ORA-20001%', SUBSTR(REPLACE(l_msg, CHR(10), ' '), 1, 60));
  DBMS_SCHEDULER.DROP_JOB('K7Q_FAILJOB', force => TRUE);

  -- 9c. DBMS_SESSION acts on the caller's own session: it has no call that ends another
  -- (trial 4 DIAG-03 named it as a way to end one).
  SELECT COUNT(*), COUNT(CASE WHEN procedure_name LIKE '%KILL%' THEN 1 END)
    INTO l_batches, l_n
    FROM all_procedures WHERE owner = 'SYS' AND object_name = 'DBMS_SESSION';
  verdict('session: DBMS_SESSION has no procedure that ends a session',
    l_batches > 0 AND l_n = 0, l_batches || ' entries, ' || l_n || ' named KILL');

  -- 9d. A package's AUTHID is declared in its specification; a body cannot carry one
  -- (trial 10 DIAG-01 read the rights model from a body).
  verdict('package: AUTHID CURRENT_USER in a specification compiles',
    compiles('CREATE OR REPLACE PACKAGE k7q_pkg AUTHID CURRENT_USER AS FUNCTION f RETURN NUMBER; END k7q_pkg;',
             'K7Q_PKG', 'PACKAGE') IS NULL);
  SELECT COUNT(*) INTO l_n FROM user_procedures
   WHERE object_name = 'K7Q_PKG' AND procedure_name IS NULL AND authid = 'CURRENT_USER';
  verdict('package: the specification''s AUTHID is the package''s', l_n = 1);
  l_msg := compiles('CREATE OR REPLACE PACKAGE BODY k7q_pkg AUTHID CURRENT_USER AS FUNCTION f RETURN NUMBER IS BEGIN RETURN 1; END f; END k7q_pkg;',
                    'K7Q_PKG', 'PACKAGE BODY');
  verdict('package: a body with an AUTHID clause is rejected', l_msg IS NOT NULL, SUBSTR(l_msg, 1, 80));

  -- 9e. MOD takes the dividend's sign (trial 10 KINJ-01 said a positive divisor makes it
  -- non-negative), and equality with a NULL argument matches no row (trial 10 TEST-01).
  SELECT MOD(-1, 1042) INTO l_n FROM dual;
  verdict('arith: MOD(-1, 1042) = -1, the sign of the dividend', l_n = -1, TO_CHAR(l_n));
  DECLARE
    l_null NUMBER;
    l_eq   NUMBER;
  BEGIN
    SELECT COUNT(CASE WHEN d = l_null THEN 1 END), COUNT(CASE WHEN d IS NULL THEN 1 END)
      INTO l_eq, l_n
      FROM (SELECT CAST(NULL AS NUMBER) d FROM dual UNION ALL SELECT 10 FROM dual);
    verdict('null: col = a NULL argument matches no row, though a row IS NULL',
      l_eq = 0 AND l_n = 1, l_eq || ' equal, ' || l_n || ' IS NULL');
  END;

  -- 9e2. A record insert (VALUES rec) takes no column list (trial 11 DRAFT-03 wrote
  -- INSERT INTO t (cols) VALUES rec(i) in a FORALL; 19c rejects it with ORA-03001).
  l_msg := compiles(q'[CREATE OR REPLACE PROCEDURE k7q_rec IS
    TYPE t IS TABLE OF work_items%ROWTYPE INDEX BY PLS_INTEGER; l t;
  BEGIN
    FORALL i IN INDICES OF l INSERT INTO work_items (id) VALUES l(i);
  END;]', 'K7Q_REC', 'PROCEDURE');
  verdict('record: INSERT with a column list and VALUES record is rejected', l_msg IS NOT NULL, SUBSTR(l_msg, 1, 80));
  verdict('record: INSERT ... VALUES record without a column list compiles',
    compiles(q'[CREATE OR REPLACE PROCEDURE k7q_rec IS
    TYPE t IS TABLE OF work_items%ROWTYPE INDEX BY PLS_INTEGER; l t;
  BEGIN
    FORALL i IN INDICES OF l INSERT INTO work_items VALUES l(i);
  END;]', 'K7Q_REC', 'PROCEDURE') IS NULL);
  -- Trial 13 DRAFT-03 wrote VALUES (l(i)); a record in parentheses is PLS-00382.
  l_msg := compiles(q'[CREATE OR REPLACE PROCEDURE k7q_rec IS
    TYPE t IS TABLE OF work_items%ROWTYPE INDEX BY PLS_INTEGER; l t;
  BEGIN
    FORALL i IN INDICES OF l INSERT INTO work_items VALUES (l(i));
  END;]', 'K7Q_REC', 'PROCEDURE');
  verdict('record: INSERT ... VALUES (record) in parentheses is rejected', l_msg IS NOT NULL, SUBSTR(l_msg, 1, 80));

  -- 9e3. A compile replaces a unit's dependency rows, a failed one too, and a name that
  -- did not resolve leaves no row (trial 13 DIAG-01 explained a dependency row as left
  -- over from an earlier version of the body).
  verdict('deps: a package specification for the dependency check compiles',
    compiles('CREATE OR REPLACE PACKAGE k7q_dep AS FUNCTION n RETURN NUMBER; END k7q_dep;',
             'K7Q_DEP', 'PACKAGE') IS NULL);
  verdict('deps: a body reading WORK_ITEMS compiles',
    compiles('CREATE OR REPLACE PACKAGE BODY k7q_dep AS FUNCTION n RETURN NUMBER IS c NUMBER; BEGIN SELECT COUNT(*) INTO c FROM work_items; RETURN c; END n; END k7q_dep;',
             'K7Q_DEP', 'PACKAGE BODY') IS NULL);
  l_msg := compiles('CREATE OR REPLACE PACKAGE BODY k7q_dep AS FUNCTION n RETURN NUMBER IS c NUMBER; BEGIN SELECT COUNT(*) INTO c FROM k7q_no_such; RETURN c; END n; END k7q_dep;',
                    'K7Q_DEP', 'PACKAGE BODY');
  SELECT COUNT(CASE WHEN referenced_name = 'WORK_ITEMS' THEN 1 END),
         COUNT(CASE WHEN referenced_name = 'K7Q_NO_SUCH' THEN 1 END)
    INTO l_batches, l_n
    FROM user_dependencies WHERE name = 'K7Q_DEP' AND type = 'PACKAGE BODY';
  verdict('deps: a failed compile keeps no row for the table it no longer names, and none for the unresolved name',
    l_msg IS NOT NULL AND l_batches = 0 AND l_n = 0,
    l_batches || ' WORK_ITEMS, ' || l_n || ' K7Q_NO_SUCH');

  -- 9e4. The stand-in's EMPLOYEE_REPORT, as 01_fixtures.sql creates it, in a schema that
  -- has EMPLOYEES and no EMPLOYEE (C16: the stand-in seeded a body dependency on
  -- EMPLOYEES). Oracle records the specification, DBMS_OUTPUT and STANDARD, nothing else.
  verdict('fixture: the EMPLOYEE_REPORT specification compiles',
    compiles(q'[CREATE OR REPLACE PACKAGE employee_report AS
  FUNCTION headcount(p_department_id IN NUMBER) RETURN NUMBER;
  PROCEDURE report_department(p_department_id IN NUMBER);
  PROCEDURE emit_lines(p_count IN NUMBER, p_width IN NUMBER DEFAULT 40);
END employee_report;]', 'EMPLOYEE_REPORT', 'PACKAGE') IS NULL);
  l_msg := compiles(q'[CREATE OR REPLACE PACKAGE BODY employee_report AS
  FUNCTION headcount(p_department_id IN NUMBER) RETURN NUMBER IS
    l_count NUMBER;
  BEGIN
    SELECT COUNT(*) INTO l_count FROM employee WHERE department_id = p_department_id;
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
END employee_report;]', 'EMPLOYEE_REPORT', 'PACKAGE BODY');
  SELECT LISTAGG(referenced_owner || '.' || referenced_name || ' ' || referenced_type, ', ')
           WITHIN GROUP (ORDER BY referenced_owner, referenced_name)
    INTO l_detail
    FROM user_dependencies WHERE name = 'EMPLOYEE_REPORT' AND type = 'PACKAGE BODY';
  verdict('fixture: the EMPLOYEE_REPORT body is invalid with ORA-00942',
    l_msg LIKE '%ORA-00942%', SUBSTR(l_msg, 1, 80));
  verdict('fixture: the invalid EMPLOYEE_REPORT body depends on its spec, DBMS_OUTPUT and STANDARD only',
    l_detail = 'K7Q_OWNER.EMPLOYEE_REPORT PACKAGE, PUBLIC.DBMS_OUTPUT SYNONYM, SYS.STANDARD PACKAGE',
    l_detail);
  -- C18: the exact line and position of each error (the stand-in seeded 5:42).
  -- MESSAGE_NUMBER is 0 for both: the ORA-00942 comes wrapped as a PL/SQL message.
  SELECT LISTAGG(line || ':' || position || ' #' || message_number || ' ' || text, ' | ')
           WITHIN GROUP (ORDER BY sequence)
    INTO l_detail
    FROM user_errors WHERE name = 'EMPLOYEE_REPORT' AND type = 'PACKAGE BODY';
  verdict('fixture: the EMPLOYEE_REPORT body errors are ORA-00942 at 5:39 and statement ignored at 5:5, message number 0',
    l_detail = '5:39 #0 PL/SQL: ORA-00942: table or view does not exist | 5:5 #0 PL/SQL: SQL Statement ignored',
    l_detail);
  -- The same statement in FIX-01's standalone function, indented two spaces (the case's
  -- error text says line 4, column 36).
  l_msg := compiles(q'[CREATE OR REPLACE FUNCTION headcount(p_department_id IN NUMBER) RETURN NUMBER IS
  l_count NUMBER;
BEGIN
  SELECT COUNT(*) INTO l_count FROM employee WHERE department_id = p_department_id;
  RETURN l_count;
END headcount;]', 'HEADCOUNT', 'FUNCTION');
  SELECT LISTAGG(line || ':' || position || ' #' || message_number || ' ' || text, ' | ')
           WITHIN GROUP (ORDER BY sequence)
    INTO l_detail
    FROM user_errors WHERE name = 'HEADCOUNT' AND type = 'FUNCTION';
  verdict('fixture: FIX-01''s function errors are ORA-00942 at 4:37 and statement ignored at 4:3',
    l_detail = '4:37 #0 PL/SQL: ORA-00942: table or view does not exist | 4:3 #0 PL/SQL: SQL Statement ignored',
    l_detail);

  -- 9e5. COUNT(DISTINCT) leaves NULLs out (trial 14 FIX-03 counted distinct salaries to
  -- decide that several employees "all have the same salary").
  SELECT COUNT(*), COUNT(DISTINCT salary) INTO l_batches, l_n
    FROM (SELECT CAST(NULL AS NUMBER) salary FROM dual UNION ALL SELECT 5000 FROM dual);
  verdict('count: COUNT(DISTINCT) leaves out NULL, so a NULL and a 5000 count as one value',
    l_batches = 2 AND l_n = 1, l_batches || ' rows, ' || l_n || ' distinct');

  -- 9f. DDL_LOCK_TIMEOUT is a session setting; there is no DML_LOCK_TIMEOUT (trial 10
  -- DIAG-03 recommended one for a row-lock wait).
  BEGIN
    EXECUTE IMMEDIATE 'ALTER SESSION SET ddl_lock_timeout = 5';
    verdict('lock: ALTER SESSION SET ddl_lock_timeout is accepted', TRUE);
  EXCEPTION WHEN OTHERS THEN
    verdict('lock: ALTER SESSION SET ddl_lock_timeout is accepted', FALSE, SQLERRM);
  END;
  BEGIN
    EXECUTE IMMEDIATE 'ALTER SESSION SET dml_lock_timeout = 5';
    verdict('lock: there is no dml_lock_timeout parameter', FALSE, 'accepted');
  EXCEPTION WHEN OTHERS THEN
    verdict('lock: there is no dml_lock_timeout parameter', TRUE, SQLERRM);
  END;
END;
/

PROMPT == privileges: what a stored unit in K7Q_USER needs on K7Q_OWNER.EMPLOYEES
CONNECT k7q_user/"K7q_User_1"@&1
SET SERVEROUTPUT ON SIZE UNLIMITED
DECLARE
  l_msg VARCHAR2(4000);

  PROCEDURE verdict(p_name VARCHAR2, p_ok BOOLEAN, p_detail VARCHAR2 := NULL) IS
  BEGIN
    DBMS_OUTPUT.PUT_LINE(CASE WHEN p_ok THEN 'PASS ' ELSE 'FAIL ' END || p_name
      || CASE WHEN p_detail IS NOT NULL THEN ' -- ' || p_detail END);
  END;
  FUNCTION compiles(p_ddl VARCHAR2, p_name VARCHAR2, p_type VARCHAR2) RETURN VARCHAR2 IS
    l_err VARCHAR2(4000);
  BEGIN
    BEGIN
      EXECUTE IMMEDIATE p_ddl;
    EXCEPTION WHEN OTHERS THEN
      IF SQLCODE <> -24344 THEN RAISE; END IF;
    END;
    SELECT MAX(text) KEEP (DENSE_RANK FIRST ORDER BY sequence) INTO l_err
      FROM user_errors WHERE name = p_name AND type = p_type;
    RETURN l_err;
  END;
BEGIN
  -- The role grant is enough for the user's own queries, not for stored code.
  l_msg := compiles('CREATE OR REPLACE PROCEDURE k7q_p IS l NUMBER; BEGIN SELECT COUNT(*) INTO l FROM k7q_owner.employees; END;',
                    'K7Q_P', 'PROCEDURE');
  verdict('grant: a role grant does not let a stored procedure compile', l_msg IS NOT NULL, SUBSTR(l_msg, 1, 80));
  l_msg := compiles('CREATE OR REPLACE PROCEDURE k7q_p AUTHID CURRENT_USER IS l NUMBER; BEGIN SELECT COUNT(*) INTO l FROM k7q_owner.employees; END;',
                    'K7Q_P', 'PROCEDURE');
  verdict('grant: AUTHID CURRENT_USER does not remove the compile-time requirement', l_msg IS NOT NULL, SUBSTR(l_msg, 1, 80));
  EXECUTE IMMEDIATE 'CREATE OR REPLACE SYNONYM emp FOR k7q_owner.employees';
  l_msg := compiles('CREATE OR REPLACE PROCEDURE k7q_p IS l NUMBER; BEGIN SELECT COUNT(*) INTO l FROM emp; END;',
                    'K7Q_P', 'PROCEDURE');
  verdict('grant: a synonym grants nothing', l_msg IS NOT NULL, SUBSTR(l_msg, 1, 80));
END;
/
CONNECT k7q_owner/"K7q_Owner_1"@&1
GRANT SELECT ON employees TO k7q_user;
CONNECT k7q_user/"K7q_User_1"@&1
SET SERVEROUTPUT ON SIZE UNLIMITED
DECLARE
  l_err VARCHAR2(4000);
BEGIN
  BEGIN
    EXECUTE IMMEDIATE 'CREATE OR REPLACE PROCEDURE k7q_p IS l NUMBER; BEGIN SELECT COUNT(*) INTO l FROM k7q_owner.employees; END;';
  EXCEPTION WHEN OTHERS THEN
    IF SQLCODE <> -24344 THEN RAISE; END IF;
  END;
  SELECT MAX(text) INTO l_err FROM user_errors WHERE name = 'K7Q_P';
  DBMS_OUTPUT.PUT_LINE(CASE WHEN l_err IS NULL THEN 'PASS ' ELSE 'FAIL ' END
    || 'grant: the narrow direct GRANT SELECT lets the procedure compile' || NVL(' -- ' || l_err, ''));
END;
/
-- Name resolution: the direct grant is in place, yet an unqualified name resolves only
-- in the code owner's schema or through a synonym (k7q_user owns no EMPLOYEES table).
DECLARE
  l_err     VARCHAR2(4000);
  l_public  PLS_INTEGER;
  PROCEDURE try(p_ddl VARCHAR2) IS
  BEGIN
    EXECUTE IMMEDIATE p_ddl;
  EXCEPTION WHEN OTHERS THEN
    IF SQLCODE <> -24344 THEN RAISE; END IF;
  END;
BEGIN
  SELECT COUNT(*) INTO l_public FROM all_synonyms
   WHERE owner = 'PUBLIC' AND synonym_name = 'EMPLOYEES';
  try('CREATE OR REPLACE PROCEDURE k7q_p IS l NUMBER; BEGIN SELECT COUNT(*) INTO l FROM employees; END;');
  SELECT MAX(text) INTO l_err FROM user_errors
   WHERE name = 'K7Q_P' AND text LIKE '%ORA-00942%';
  DBMS_OUTPUT.PUT_LINE(CASE WHEN l_public = 0 AND l_err IS NOT NULL THEN 'PASS ' ELSE 'FAIL ' END
    || 'resolve: a direct grant does not make an unqualified name reach another schema'
    || ' -- public synonyms ' || l_public || ', ' || SUBSTR(l_err, 1, 80));
  try('CREATE OR REPLACE PROCEDURE k7q_p IS l NUMBER; BEGIN SELECT COUNT(*) INTO l FROM emp; END;');
  SELECT MAX(text) INTO l_err FROM user_errors WHERE name = 'K7Q_P';
  DBMS_OUTPUT.PUT_LINE(CASE WHEN l_err IS NULL THEN 'PASS ' ELSE 'FAIL ' END
    || 'resolve: with the grant, a private synonym resolves the unqualified name'
    || NVL(' -- ' || l_err, ''));
  -- Static SQL is checked at compile time: a column-count mismatch never reaches the
  -- unit's WHEN OTHERS handler, because the unit does not compile.
  EXECUTE IMMEDIATE 'CREATE TABLE k7q_one (x NUMBER)';
  EXECUTE IMMEDIATE 'CREATE TABLE k7q_two (x NUMBER, y NUMBER)';
  try('CREATE OR REPLACE PROCEDURE k7q_p IS BEGIN INSERT INTO k7q_one SELECT * FROM k7q_two; '
      || 'EXCEPTION WHEN OTHERS THEN NULL; END;');
  SELECT MAX(text) INTO l_err FROM user_errors
   WHERE name = 'K7Q_P' AND text LIKE '%ORA-00913%';
  DBMS_OUTPUT.PUT_LINE(CASE WHEN l_err IS NOT NULL THEN 'PASS ' ELSE 'FAIL ' END
    || 'compile: a column-count mismatch in static SQL is a compile error, not a handled exception'
    || ' -- ' || SUBSTR(l_err, 1, 80));
END;
/

PROMPT K7Q-DONE
EXIT
