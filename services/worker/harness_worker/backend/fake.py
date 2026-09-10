"""A local stand-in for an Oracle target.

This backend exists so the execution engine, session leasing, limit enforcement,
transaction handling and every screen above them can be developed and tested before
an Oracle 19c environment is available. It is a SQLite database wearing an Oracle
costume, and it is deliberately honest about that:

* It runs real SQL, real binds, real transactions and real row counts, so commit,
  rollback, isolation and bounded-fetch behaviour are genuinely exercised.
* It seeds the dictionary views, fixture schema, invalid package, blocking scenario
  and slow-query fixture that the MVP acceptance criteria refer to.
* It does **not** implement PL/SQL, the optimizer, or Oracle semantics beyond a
  small documented translation layer. Anything it cannot honour raises an error
  that names the stand-in, so a passing test is never mistaken for Oracle coverage.

Release qualification still requires a real 19c database. See MVP_PLAN.md.
"""

from __future__ import annotations

import os
import re
import sqlite3
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from harness_worker.backend.base import (
    ConnectionSpec,
    OracleBackend,
    OracleConnection,
    StatementResult,
)
from harness_worker.errors import OracleError, OutcomeUnknownError, ValidationError
from harness_worker.types import (
    Capability,
    CapabilityReport,
    ColumnMetadata,
    CompilerError,
    ExecutionLimits,
    ResultSet,
    StatementKind,
    TargetIdentity,
)

FAKE_VERSION = "19.3.0.0.0"

# Marker a fixture or a test can put in PL/SQL source to make compilation fail in a
# predictable place, standing in for a real Oracle compiler error.
INVALID_MARKER = "--!invalid"

_DUAL = re.compile(r"\bFROM\s+DUAL\b", re.IGNORECASE)
_DYNAMIC_VIEW = re.compile(r"\b[GV]\$(\w+)", re.IGNORECASE)
_OFFSET_FETCH = re.compile(
    r"\bOFFSET\s+(:?[A-Za-z0-9_]+)\s+ROWS?\s+FETCH\s+(?:FIRST|NEXT)\s+"
    r"(:?[A-Za-z0-9_]+)\s+ROWS?\s+ONLY\b",
    re.IGNORECASE,
)
_FETCH_FIRST = re.compile(
    r"\bFETCH\s+(?:FIRST|NEXT)\s+(:?[A-Za-z0-9_]+)\s+ROWS?\s+ONLY\b", re.IGNORECASE
)
_ROWNUM = re.compile(r"\b(?:AND|WHERE)\s+ROWNUM\s*<=?\s*(\d+)", re.IGNORECASE)
_ALTER_COMPILE = re.compile(
    r"^\s*ALTER\s+(PACKAGE\s+BODY|PACKAGE|PROCEDURE|FUNCTION|TRIGGER|TYPE\s+BODY|TYPE|VIEW)\s+"
    r'(?:"?([A-Za-z0-9_$#]+)"?\s*\.\s*)?"?([A-Za-z0-9_$#]+)"?\s+COMPILE\b',
    re.IGNORECASE,
)
_GATHER_TABLE_STATS = re.compile(r"DBMS_STATS\s*\.\s*GATHER_TABLE_STATS", re.IGNORECASE)
_EXPLAIN_PLAN = re.compile(
    r"^\s*EXPLAIN\s+PLAN\s+SET\s+STATEMENT_ID\s*=\s*'([^']+)'\s+FOR\s+(.+)$",
    re.IGNORECASE | re.DOTALL,
)
_SIMPLE_FUNCTIONS = [
    (re.compile(r"\bSYSTIMESTAMP\b", re.IGNORECASE), "datetime('now')"),
    (re.compile(r"\bSYSDATE\b", re.IGNORECASE), "datetime('now')"),
    (re.compile(r"\bNVL\s*\(", re.IGNORECASE), "IFNULL("),
    (re.compile(r"\bUSER\b(?!\s*\()", re.IGNORECASE), "'HARNESS_APP'"),
]

_SEED_SCRIPT = """
CREATE TABLE IF NOT EXISTS departments (
    department_id   INTEGER PRIMARY KEY,
    department_name TEXT NOT NULL,
    location_id     INTEGER
);
CREATE TABLE IF NOT EXISTS employees (
    employee_id   INTEGER PRIMARY KEY,
    first_name    TEXT,
    last_name     TEXT NOT NULL,
    email         TEXT,
    hire_date     TEXT,
    salary        NUMERIC,
    department_id INTEGER REFERENCES departments(department_id)
);
CREATE INDEX IF NOT EXISTS emp_department_ix ON employees(department_id);
CREATE TABLE IF NOT EXISTS order_lines (
    line_id     INTEGER PRIMARY KEY,
    order_id    INTEGER NOT NULL,
    product_id  INTEGER NOT NULL,
    quantity    INTEGER NOT NULL,
    unit_price  NUMERIC NOT NULL,
    created_at  TEXT
);

-- Dictionary stand-ins. Real Oracle views are read-only; these are seeded tables
-- with the same column names so the reviewed diagnostic queries run unchanged.
CREATE TABLE IF NOT EXISTS all_objects (
    owner TEXT, object_name TEXT, object_type TEXT, status TEXT,
    created TEXT, last_ddl_time TEXT
);
CREATE TABLE IF NOT EXISTS all_tab_columns (
    owner TEXT, table_name TEXT, column_name TEXT, data_type TEXT,
    data_length INTEGER, data_precision INTEGER, data_scale INTEGER,
    nullable TEXT, column_id INTEGER
);
CREATE TABLE IF NOT EXISTS all_source (
    owner TEXT, name TEXT, type TEXT, line INTEGER, text TEXT
);
CREATE TABLE IF NOT EXISTS all_errors (
    owner TEXT, name TEXT, type TEXT, sequence INTEGER, line INTEGER,
    position INTEGER, text TEXT, attribute TEXT, message_number INTEGER
);
CREATE TABLE IF NOT EXISTS all_constraints (
    owner TEXT, constraint_name TEXT, constraint_type TEXT, table_name TEXT,
    r_owner TEXT, r_constraint_name TEXT, status TEXT
);
CREATE TABLE IF NOT EXISTS all_cons_columns (
    owner TEXT, constraint_name TEXT, table_name TEXT, column_name TEXT, position INTEGER
);
CREATE TABLE IF NOT EXISTS all_indexes (
    owner TEXT, index_name TEXT, table_owner TEXT, table_name TEXT,
    uniqueness TEXT, status TEXT
);
CREATE TABLE IF NOT EXISTS all_ind_columns (
    index_owner TEXT, index_name TEXT, table_owner TEXT, table_name TEXT,
    column_name TEXT, column_position INTEGER
);
CREATE TABLE IF NOT EXISTS all_dependencies (
    owner TEXT, name TEXT, type TEXT, referenced_owner TEXT,
    referenced_name TEXT, referenced_type TEXT
);
CREATE TABLE IF NOT EXISTS all_tables (owner TEXT, table_name TEXT, num_rows INTEGER,
    last_analyzed TEXT, tablespace_name TEXT);
CREATE TABLE IF NOT EXISTS all_views (owner TEXT, view_name TEXT, text TEXT);
CREATE TABLE IF NOT EXISTS all_sequences (sequence_owner TEXT, sequence_name TEXT,
    min_value INTEGER, max_value INTEGER, increment_by INTEGER, last_number INTEGER);
CREATE TABLE IF NOT EXISTS all_synonyms (owner TEXT, synonym_name TEXT,
    table_owner TEXT, table_name TEXT, db_link TEXT);
CREATE TABLE IF NOT EXISTS all_triggers (owner TEXT, trigger_name TEXT,
    table_owner TEXT, table_name TEXT, status TEXT, trigger_type TEXT,
    triggering_event TEXT);
CREATE TABLE IF NOT EXISTS dba_tablespace_usage_metrics (
    tablespace_name TEXT, used_space NUMERIC, tablespace_size NUMERIC,
    used_percent NUMERIC
);
CREATE TABLE IF NOT EXISTS dba_tablespaces (
    tablespace_name TEXT, block_size INTEGER, status TEXT, contents TEXT,
    extent_management TEXT
);
CREATE TABLE IF NOT EXISTS dba_scheduler_job_run_details (
    owner TEXT, job_name TEXT, status TEXT, error_number INTEGER,
    actual_start_date TEXT, run_duration TEXT, additional_info TEXT
);
CREATE TABLE IF NOT EXISTS dba_scheduler_jobs (
    owner TEXT, job_name TEXT, enabled TEXT, state TEXT, last_start_date TEXT,
    next_run_date TEXT, failure_count INTEGER
);
CREATE TABLE IF NOT EXISTS v_session (
    sid INTEGER, "SERIAL#" INTEGER, username TEXT, status TEXT, osuser TEXT,
    machine TEXT, program TEXT, sql_id TEXT, event TEXT, seconds_in_wait INTEGER,
    blocking_session INTEGER, blocking_session_status TEXT, last_call_et INTEGER,
    logon_time TEXT
);
CREATE TABLE IF NOT EXISTS v_sql (
    sql_id TEXT, child_number INTEGER, sql_text TEXT, executions INTEGER,
    elapsed_time INTEGER, cpu_time INTEGER, buffer_gets INTEGER, disk_reads INTEGER,
    rows_processed INTEGER, plan_hash_value INTEGER, last_active_time TEXT,
    parsing_schema_name TEXT
);
CREATE TABLE IF NOT EXISTS v_sql_plan (
    sql_id TEXT, child_number INTEGER, id INTEGER, parent_id INTEGER,
    operation TEXT, options TEXT, object_owner TEXT, object_name TEXT,
    cardinality INTEGER, bytes INTEGER, cost INTEGER, access_predicates TEXT,
    filter_predicates TEXT, depth INTEGER
);
CREATE TABLE IF NOT EXISTS plan_table (
    statement_id TEXT, plan_id INTEGER, id INTEGER, parent_id INTEGER, depth INTEGER,
    operation TEXT, options TEXT, object_owner TEXT, object_name TEXT,
    cardinality INTEGER, bytes INTEGER, cost INTEGER, access_predicates TEXT,
    filter_predicates TEXT
);
CREATE TABLE IF NOT EXISTS harness_fake_state (key TEXT PRIMARY KEY, value TEXT);
"""


def _seed_rows(cur: sqlite3.Cursor) -> None:
    """Load the fixture data the MVP acceptance criteria refer to."""

    cur.executemany(
        "INSERT INTO departments (department_id, department_name, location_id) VALUES (?,?,?)",
        [(10, "Administration", 1700), (20, "Engineering", 1400), (30, "Support", 1500)],
    )
    employees = [
        (100, "Ada", "Byron", "ADA", "2019-04-01", 12000, 20),
        (101, "Grace", "Hopper", "GHOPPER", "2019-06-15", 11500, 20),
        (102, "Ken", "Iverson", "KIVERSON", "2020-01-20", 9000, 20),
        (103, "Jean", "Bartik", "JBARTIK", "2021-03-05", 8200, 30),
        (104, "Mary", "Keller", "MKELLER", "2022-08-11", 7600, 30),
        (105, "Alan", "Perlis", "APERLIS", "2023-02-27", 15000, 10),
    ]
    cur.executemany(
        "INSERT INTO employees (employee_id, first_name, last_name, email, hire_date,"
        " salary, department_id) VALUES (?,?,?,?,?,?,?)",
        employees,
    )
    # Slow-query fixture: enough unindexed rows that a full scan is measurably worse
    # than the indexed access path.
    lines = [
        (i, 1000 + (i % 500), 1 + (i % 40), 1 + (i % 7), 9.99 + (i % 13), "2025-01-01")
        for i in range(1, 4001)
    ]
    cur.executemany(
        "INSERT INTO order_lines (line_id, order_id, product_id, quantity, unit_price,"
        " created_at) VALUES (?,?,?,?,?,?)",
        lines,
    )

    objects = [
        ("HARNESS_APP", "DEPARTMENTS", "TABLE", "VALID"),
        ("HARNESS_APP", "EMPLOYEES", "TABLE", "VALID"),
        ("HARNESS_APP", "ORDER_LINES", "TABLE", "VALID"),
        ("HARNESS_APP", "EMP_DEPARTMENT_IX", "INDEX", "VALID"),
        ("HARNESS_APP", "EMPLOYEE_REPORT", "PACKAGE", "VALID"),
        ("HARNESS_APP", "EMPLOYEE_REPORT", "PACKAGE BODY", "INVALID"),
        ("HARNESS_APP", "V_ACTIVE_EMPLOYEES", "VIEW", "VALID"),
        ("HARNESS_APP", "EMPLOYEE_SEQ", "SEQUENCE", "VALID"),
    ]
    cur.executemany(
        "INSERT INTO all_objects (owner, object_name, object_type, status, created,"
        " last_ddl_time) VALUES (?,?,?,?,datetime('now'),datetime('now'))",
        objects,
    )
    cur.executemany(
        "INSERT INTO all_tables (owner, table_name, num_rows, last_analyzed,"
        " tablespace_name) VALUES (?,?,?,?,?)",
        [
            ("HARNESS_APP", "DEPARTMENTS", 3, None, "USERS"),
            ("HARNESS_APP", "EMPLOYEES", 6, None, "USERS"),
            ("HARNESS_APP", "ORDER_LINES", None, None, "USERS"),
        ],
    )
    columns = [
        ("EMPLOYEES", "EMPLOYEE_ID", "NUMBER", 22, 6, 0, "N", 1),
        ("EMPLOYEES", "FIRST_NAME", "VARCHAR2", 30, None, None, "Y", 2),
        ("EMPLOYEES", "LAST_NAME", "VARCHAR2", 30, None, None, "N", 3),
        ("EMPLOYEES", "EMAIL", "VARCHAR2", 40, None, None, "Y", 4),
        ("EMPLOYEES", "HIRE_DATE", "DATE", 7, None, None, "Y", 5),
        ("EMPLOYEES", "SALARY", "NUMBER", 22, 10, 2, "Y", 6),
        ("EMPLOYEES", "DEPARTMENT_ID", "NUMBER", 22, 6, 0, "Y", 7),
        ("DEPARTMENTS", "DEPARTMENT_ID", "NUMBER", 22, 6, 0, "N", 1),
        ("DEPARTMENTS", "DEPARTMENT_NAME", "VARCHAR2", 40, None, None, "N", 2),
        ("DEPARTMENTS", "LOCATION_ID", "NUMBER", 22, 6, 0, "Y", 3),
        ("ORDER_LINES", "LINE_ID", "NUMBER", 22, 12, 0, "N", 1),
        ("ORDER_LINES", "ORDER_ID", "NUMBER", 22, 12, 0, "N", 2),
        ("ORDER_LINES", "PRODUCT_ID", "NUMBER", 22, 12, 0, "N", 3),
        ("ORDER_LINES", "QUANTITY", "NUMBER", 22, 6, 0, "N", 4),
        ("ORDER_LINES", "UNIT_PRICE", "NUMBER", 22, 10, 2, "N", 5),
        ("ORDER_LINES", "CREATED_AT", "DATE", 7, None, None, "Y", 6),
    ]
    cur.executemany(
        "INSERT INTO all_tab_columns (owner, table_name, column_name, data_type,"
        " data_length, data_precision, data_scale, nullable, column_id)"
        " VALUES ('HARNESS_APP',?,?,?,?,?,?,?,?)",
        columns,
    )
    cur.executemany(
        "INSERT INTO all_constraints (owner, constraint_name, constraint_type,"
        " table_name, r_owner, r_constraint_name, status)"
        " VALUES ('HARNESS_APP',?,?,?,?,?,'ENABLED')",
        [
            ("EMP_PK", "P", "EMPLOYEES", None, None),
            ("DEPT_PK", "P", "DEPARTMENTS", None, None),
            ("EMP_DEPT_FK", "R", "EMPLOYEES", "HARNESS_APP", "DEPT_PK"),
        ],
    )
    cur.executemany(
        "INSERT INTO all_cons_columns (owner, constraint_name, table_name, column_name,"
        " position) VALUES ('HARNESS_APP',?,?,?,?)",
        [
            ("EMP_PK", "EMPLOYEES", "EMPLOYEE_ID", 1),
            ("DEPT_PK", "DEPARTMENTS", "DEPARTMENT_ID", 1),
            ("EMP_DEPT_FK", "EMPLOYEES", "DEPARTMENT_ID", 1),
        ],
    )
    cur.execute(
        "INSERT INTO all_indexes (owner, index_name, table_owner, table_name, uniqueness,"
        " status) VALUES ('HARNESS_APP','EMP_DEPARTMENT_IX','HARNESS_APP','EMPLOYEES',"
        "'NONUNIQUE','VALID')"
    )
    cur.execute(
        "INSERT INTO all_ind_columns (index_owner, index_name, table_owner, table_name,"
        " column_name, column_position) VALUES ('HARNESS_APP','EMP_DEPARTMENT_IX',"
        "'HARNESS_APP','EMPLOYEES','DEPARTMENT_ID',1)"
    )
    cur.execute(
        "INSERT INTO all_views (owner, view_name, text) VALUES ('HARNESS_APP',"
        "'V_ACTIVE_EMPLOYEES','SELECT * FROM employees WHERE salary IS NOT NULL')"
    )
    cur.execute(
        "INSERT INTO all_sequences (sequence_owner, sequence_name, min_value, max_value,"
        " increment_by, last_number) VALUES ('HARNESS_APP','EMPLOYEE_SEQ',1,999999,1,106)"
    )
    cur.executemany(
        "INSERT INTO all_dependencies (owner, name, type, referenced_owner,"
        " referenced_name, referenced_type) VALUES ('HARNESS_APP',?,?,'HARNESS_APP',?,?)",
        [
            ("EMPLOYEE_REPORT", "PACKAGE BODY", "EMPLOYEES", "TABLE"),
            ("V_ACTIVE_EMPLOYEES", "VIEW", "EMPLOYEES", "TABLE"),
        ],
    )

    spec = [
        "PACKAGE employee_report AS",
        "  FUNCTION headcount(p_department_id IN NUMBER) RETURN NUMBER;",
        "  PROCEDURE report_department(p_department_id IN NUMBER);",
        "END employee_report;",
    ]
    body = [
        "PACKAGE BODY employee_report AS",
        "  FUNCTION headcount(p_department_id IN NUMBER) RETURN NUMBER IS",
        "    l_count NUMBER;",
        "  BEGIN",
        "    SELECT COUNT(*) INTO l_count FROM employee WHERE department_id = p_department_id;",
        "    RETURN l_count;",
        "  END headcount;",
        "  PROCEDURE report_department(p_department_id IN NUMBER) IS",
        "  BEGIN",
        "    DBMS_OUTPUT.PUT_LINE('headcount=' || headcount(p_department_id));",
        "  END report_department;",
        "END employee_report;",
    ]
    cur.executemany(
        "INSERT INTO all_source (owner, name, type, line, text) VALUES"
        " ('HARNESS_APP','EMPLOYEE_REPORT','PACKAGE',?,?)",
        list(enumerate(spec, start=1)),
    )
    cur.executemany(
        "INSERT INTO all_source (owner, name, type, line, text) VALUES"
        " ('HARNESS_APP','EMPLOYEE_REPORT','PACKAGE BODY',?,?)",
        list(enumerate(body, start=1)),
    )
    # The seeded body references EMPLOYEE instead of EMPLOYEES, so it is invalid on
    # purpose: the PL/SQL workspace acceptance test repairs it and recompiles.
    cur.executemany(
        "INSERT INTO all_errors (owner, name, type, sequence, line, position, text,"
        " attribute, message_number) VALUES ('HARNESS_APP','EMPLOYEE_REPORT',"
        "'PACKAGE BODY',?,?,?,?,'ERROR',?)",
        [
            (1, 5, 42, "PL/SQL: ORA-00942: table or view does not exist", 942),
            (2, 5, 5, "PL/SQL: SQL Statement ignored", 0),
        ],
    )

    # Sizes are in blocks, as the real DBA_TABLESPACE_USAGE_METRICS reports them, so
    # the reviewed query does the same block-size arithmetic it does against Oracle.
    block_size = 8192
    per_mb = 1048576 / block_size
    cur.executemany(
        "INSERT INTO dba_tablespace_usage_metrics (tablespace_name, used_space,"
        " tablespace_size, used_percent) VALUES (?,?,?,?)",
        [
            ("SYSTEM", 780 * per_mb, 1024 * per_mb, 76.17),
            ("SYSAUX", 640 * per_mb, 1024 * per_mb, 62.50),
            ("USERS", 1890 * per_mb, 2048 * per_mb, 92.29),
            ("TEMP", 120 * per_mb, 4096 * per_mb, 2.93),
        ],
    )
    cur.executemany(
        "INSERT INTO dba_tablespaces (tablespace_name, block_size, status, contents,"
        " extent_management) VALUES (?,?,?,?,?)",
        [
            ("SYSTEM", block_size, "ONLINE", "PERMANENT", "LOCAL"),
            ("SYSAUX", block_size, "ONLINE", "PERMANENT", "LOCAL"),
            ("USERS", block_size, "ONLINE", "PERMANENT", "LOCAL"),
            ("TEMP", block_size, "ONLINE", "TEMPORARY", "LOCAL"),
        ],
    )
    cur.executemany(
        "INSERT INTO dba_scheduler_jobs (owner, job_name, enabled, state,"
        " last_start_date, next_run_date, failure_count) VALUES (?,?,?,?,?,?,?)",
        [
            (
                "HARNESS_APP",
                "NIGHTLY_STATS",
                "TRUE",
                "SCHEDULED",
                "2025-01-01 02:00:00",
                "2025-01-02 02:00:00",
                0,
            ),
            (
                "HARNESS_APP",
                "EXPORT_ORDERS",
                "TRUE",
                "SCHEDULED",
                "2025-01-01 03:00:00",
                "2025-01-02 03:00:00",
                3,
            ),
        ],
    )
    cur.executemany(
        "INSERT INTO dba_scheduler_job_run_details (owner, job_name, status,"
        " error_number, actual_start_date, run_duration, additional_info)"
        " VALUES (?,?,?,?,?,?,?)",
        [
            (
                "HARNESS_APP",
                "EXPORT_ORDERS",
                "FAILED",
                20001,
                "2025-01-01 03:00:00",
                "+000 00:00:07",
                "ORA-20001: destination unavailable",
            ),
            (
                "HARNESS_APP",
                "NIGHTLY_STATS",
                "SUCCEEDED",
                0,
                "2025-01-01 02:00:00",
                "+000 00:04:12",
                None,
            ),
        ],
    )
    # Seeded blocking scenario: session 42 holds a lock that session 77 is waiting on.
    cur.executemany(
        'INSERT INTO v_session (sid, "SERIAL#", username, status, osuser, machine,'
        " program, sql_id, event, seconds_in_wait, blocking_session,"
        " blocking_session_status, last_call_et, logon_time) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            (
                42,
                1201,
                "HARNESS_APP",
                "INACTIVE",
                "batch",
                "app01",
                "sqlplus",
                "9xk2rj4v0d1qa",
                "SQL*Net message from client",
                320,
                None,
                None,
                320,
                "2025-01-01 08:00:00",
            ),
            (
                77,
                3310,
                "HARNESS_APP",
                "ACTIVE",
                "web",
                "app02",
                "JDBC Thin Client",
                "b7t3wq8m2z0pf",
                "enq: TX - row lock contention",
                118,
                42,
                "VALID",
                118,
                "2025-01-01 09:14:00",
            ),
            (
                91,
                4410,
                "REPORTING",
                "ACTIVE",
                "web",
                "app02",
                "JDBC Thin Client",
                "c1n5xa9k4h2rd",
                "db file sequential read",
                2,
                None,
                None,
                2,
                "2025-01-01 09:20:00",
            ),
        ],
    )
    cur.executemany(
        "INSERT INTO v_sql (sql_id, child_number, sql_text, executions, elapsed_time,"
        " cpu_time, buffer_gets, disk_reads, rows_processed, plan_hash_value,"
        " last_active_time, parsing_schema_name) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            (
                "c1n5xa9k4h2rd",
                0,
                "SELECT order_id, SUM(quantity * unit_price) FROM order_lines"
                " WHERE product_id = :product_id GROUP BY order_id",
                1420,
                88_400_000,
                61_200_000,
                918_300,
                41_200,
                1420,
                2064512836,
                "2025-01-01 09:20:00",
                "HARNESS_APP",
            ),
            (
                "b7t3wq8m2z0pf",
                0,
                "UPDATE employees SET salary = salary * 1.05 WHERE department_id = :dept",
                12,
                1_240_000,
                900_000,
                3_100,
                12,
                24,
                1188924734,
                "2025-01-01 09:14:00",
                "HARNESS_APP",
            ),
        ],
    )
    cur.executemany(
        "INSERT INTO v_sql_plan (sql_id, child_number, id, parent_id, operation, options,"
        " object_owner, object_name, cardinality, bytes, cost, access_predicates,"
        " filter_predicates, depth) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            (
                "c1n5xa9k4h2rd",
                0,
                0,
                None,
                "SELECT STATEMENT",
                None,
                None,
                None,
                500,
                21000,
                812,
                None,
                None,
                0,
            ),
            (
                "c1n5xa9k4h2rd",
                0,
                1,
                0,
                "HASH",
                "GROUP BY",
                None,
                None,
                500,
                21000,
                812,
                None,
                None,
                1,
            ),
            (
                "c1n5xa9k4h2rd",
                0,
                2,
                1,
                "TABLE ACCESS",
                "FULL",
                "HARNESS_APP",
                "ORDER_LINES",
                4000,
                168000,
                806,
                None,
                '"PRODUCT_ID"=:PRODUCT_ID',
                2,
            ),
        ],
    )
    cur.execute("INSERT INTO harness_fake_state (key, value) VALUES ('seeded', datetime('now'))")


class FakeOracleConnection(OracleConnection):
    """One SQLite session presented as an Oracle session."""

    def __init__(self, spec: ConnectionSpec, path: Path, sid: int) -> None:
        self._spec = spec
        self._path = path
        self._sid = sid
        # A leased session is driven from the execution pool and cancelled or closed
        # from a request thread, which is normal for python-oracledb. SQLite has to be
        # told to allow it; access is still serialized by the lock below.
        self._conn = sqlite3.connect(
            str(path), timeout=5.0, isolation_level=None, check_same_thread=False
        )
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA busy_timeout = 5000")
        self._transaction_open = False
        self._broken = False
        self._closed = False
        self._cancel_requested = threading.Event()
        self._dbms_output: list[str] = []
        self._lock = threading.RLock()
        self._next_commit_failure: str | None = None

    # -- identity and capabilities -------------------------------------------------

    def identity(self) -> TargetIdentity:
        return TargetIdentity(
            databaseName=self._spec.service_name.upper(),
            instanceName=self._spec.service_name.upper(),
            hostName=self._spec.host,
            version=FAKE_VERSION,
            versionFull=f"Oracle Database stand-in {FAKE_VERSION} (harness fake backend)",
            isCdb=False,
            containerName=None,
            currentSchema=(self._spec.default_schema or self._spec.username).upper(),
            currentUser=self._spec.username.upper(),
            sessionId=self._sid,
            serialNumber=1,
        )

    def probe_capability(self, capability: Capability) -> CapabilityReport:
        checks = {
            Capability.CONNECT: "SELECT 1",
            Capability.SESSION_SCHEMA: "SELECT COUNT(*) FROM all_objects",
            Capability.ALL_OBJECTS: "SELECT COUNT(*) FROM all_objects",
            Capability.EXPLAIN_PLAN: "SELECT COUNT(*) FROM plan_table",
            Capability.DISPLAY_CURSOR: "SELECT COUNT(*) FROM v_sql_plan",
            Capability.V_SESSION: "SELECT COUNT(*) FROM v_session",
            Capability.V_SQL: "SELECT COUNT(*) FROM v_sql",
            Capability.DBA_TABLESPACES: "SELECT COUNT(*) FROM dba_tablespace_usage_metrics",
            Capability.DBA_SCHEDULER_JOBS: "SELECT COUNT(*) FROM dba_scheduler_jobs",
            Capability.DBMS_STATS: "SELECT COUNT(*) FROM all_tables",
            Capability.COMPILE_OBJECTS: "SELECT COUNT(*) FROM all_source",
        }
        sql = checks.get(capability)
        if sql is None:
            return CapabilityReport(
                capability=capability,
                available=False,
                detail="No probe is defined for this capability.",
            )
        try:
            self._conn.execute(sql).fetchone()
        except sqlite3.Error as exc:  # pragma: no cover - defensive
            return CapabilityReport(capability=capability, available=False, detail=str(exc))
        return CapabilityReport(
            capability=capability,
            available=True,
            detail="Verified against the local stand-in, not against Oracle.",
        )

    # -- execution -----------------------------------------------------------------

    def execute(
        self,
        statement: str,
        binds: dict[str, Any],
        kind: StatementKind,
        limits: ExecutionLimits,
        *,
        collect_dbms_output: bool = False,
    ) -> StatementResult:
        with self._lock:
            if self._closed:
                raise OracleError("The session is closed.", oracle_code="ORA-01012")
            self._cancel_requested.clear()
            self._dbms_output = []
            started = time.perf_counter()

            if kind in (StatementKind.PLSQL_BLOCK, StatementKind.PLSQL_SOURCE):
                result = self._execute_plsql(statement, binds, kind, limits)
            elif kind == StatementKind.TRANSACTION_CONTROL:
                result = self._execute_transaction_control(statement)
            else:
                result = self._execute_sql(statement, binds, kind, limits)

            result.database_elapsed_ms = int((time.perf_counter() - started) * 1000)
            if collect_dbms_output:
                budget = limits.max_dbms_output_bytes
                collected: list[str] = []
                used = 0
                for line in self._dbms_output:
                    encoded = len(line.encode("utf-8")) + 1
                    if used + encoded > budget:
                        result.dbms_output_truncated = True
                        break
                    collected.append(line)
                    used += encoded
                result.dbms_output = collected
            return result

    def _execute_sql(
        self,
        statement: str,
        binds: dict[str, Any],
        kind: StatementKind,
        limits: ExecutionLimits,
    ) -> StatementResult:
        explain_match = _EXPLAIN_PLAN.match(statement)
        if explain_match:
            return self._explain_plan(explain_match.group(1), explain_match.group(2))

        compile_match = _ALTER_COMPILE.match(statement)
        if compile_match:
            # Recompiling is DDL against dictionary state the stand-in keeps in its own
            # tables, so it is handled here instead of being passed to SQLite.
            return self._recompile(compile_match)

        translated, forced_limit = _translate(statement)
        params = _filter_binds(translated, binds)
        if kind == StatementKind.DDL and self._transaction_open:
            # Oracle commits the open transaction before DDL. Reproduce that here so
            # the behaviour is visible in tests rather than discovered in production.
            self._hard_commit()

        cur = self._conn.cursor()
        try:
            self._begin_if_needed(kind)
            cur.execute(translated, params)
        except sqlite3.Error as exc:
            raise _as_oracle_error(exc) from exc

        if cur.description is None:
            affected = cur.rowcount if cur.rowcount is not None and cur.rowcount >= 0 else 0
            if kind in (StatementKind.DML, StatementKind.UNKNOWN) and affected:
                self._transaction_open = True
            return StatementResult(statement_kind=kind, rows_affected=affected)

        columns = [
            ColumnMetadata(name=str(d[0]).upper(), typeName="ANY", nullable=True)
            for d in cur.description
        ]
        max_rows = min(limits.max_rows, forced_limit) if forced_limit else limits.max_rows
        rows: list[list[Any]] = []
        truncated = False
        reason: str | None = None
        used_bytes = 0
        for row in cur:
            if self._cancel_requested.is_set():
                cur.close()
                from harness_worker.errors import CancelledError_

                raise CancelledError_("The statement was cancelled while fetching rows.")
            if len(rows) >= max_rows:
                truncated = True
                reason = f"Row limit of {max_rows} reached."
                break
            shaped = [_shape_value(value, limits.lob_preview_bytes) for value in row]
            used_bytes += sum(len(repr(v)) for v in shaped)
            if used_bytes > limits.max_response_bytes:
                truncated = True
                reason = "Response size limit reached."
                break
            rows.append(shaped)
        cur.close()
        return StatementResult(
            statement_kind=kind,
            result_set=ResultSet(
                columns=columns,
                rows=rows,
                rowCount=len(rows),
                truncated=truncated,
                truncationReason=reason,
            ),
        )

    def _execute_transaction_control(self, statement: str) -> StatementResult:
        head = statement.strip().split()[0].upper()
        if head == "COMMIT":
            self.commit()
        elif head == "ROLLBACK":
            self.rollback()
        else:
            raise ValidationError(
                "The stand-in backend supports COMMIT and ROLLBACK only. Use the "
                "worksheet transaction controls."
            )
        return StatementResult(statement_kind=StatementKind.TRANSACTION_CONTROL, rows_affected=0)

    def _explain_plan(self, statement_id: str, target_sql: str) -> StatementResult:
        """Produce a plausible estimated plan for the fixture schema.

        This is not an optimizer. It looks at which table the statement reads and
        whether the predicate uses an indexed column, and writes the two shapes the
        tuning workbench needs to be able to show and compare. Estimates from here
        are labelled as estimates everywhere they are displayed.
        """

        cur = self._conn.cursor()
        cur.execute("DELETE FROM plan_table WHERE statement_id = ?", (statement_id,))
        masked = re.sub(r"'[^']*'", "''", target_sql)
        table_match = re.search(r"\bFROM\s+([A-Za-z][A-Za-z0-9_$#]*)", masked, re.IGNORECASE)
        table = (table_match.group(1) if table_match else "DUAL").upper()
        owner = (self._spec.default_schema or self._spec.username).upper()

        indexed_columns = {
            row[0].upper()
            for row in cur.execute(
                "SELECT column_name FROM all_ind_columns WHERE table_name = ?", (table,)
            ).fetchall()
        }
        predicate = masked.upper()
        used_index = any(column in predicate for column in indexed_columns)
        row_estimate = cur.execute(
            "SELECT num_rows FROM all_tables WHERE table_name = ?", (table,)
        ).fetchone()
        cardinality = (row_estimate[0] if row_estimate and row_estimate[0] else 1000) or 1000

        rows: list[tuple] = [
            (
                statement_id,
                1,
                0,
                None,
                0,
                "SELECT STATEMENT",
                None,
                None,
                None,
                cardinality,
                cardinality * 42,
                3 if used_index else max(4, cardinality // 5),
                None,
                None,
            ),
        ]
        if used_index:
            rows.append(
                (
                    statement_id,
                    1,
                    1,
                    0,
                    1,
                    "TABLE ACCESS",
                    "BY INDEX ROWID BATCHED",
                    owner,
                    table,
                    cardinality,
                    cardinality * 42,
                    3,
                    None,
                    None,
                )
            )
            index_name = cur.execute(
                "SELECT index_name FROM all_ind_columns WHERE table_name = ? LIMIT 1",
                (table,),
            ).fetchone()
            rows.append(
                (
                    statement_id,
                    1,
                    2,
                    1,
                    2,
                    "INDEX",
                    "RANGE SCAN",
                    owner,
                    index_name[0] if index_name else "UNKNOWN_IX",
                    cardinality,
                    None,
                    1,
                    "indexed predicate",
                    None,
                )
            )
        else:
            rows.append(
                (
                    statement_id,
                    1,
                    1,
                    0,
                    1,
                    "TABLE ACCESS",
                    "FULL",
                    owner,
                    table,
                    cardinality,
                    cardinality * 42,
                    max(4, cardinality // 5),
                    None,
                    "unindexed predicate",
                )
            )
        cur.executemany(
            "INSERT INTO plan_table (statement_id, plan_id, id, parent_id, depth,"
            " operation, options, object_owner, object_name, cardinality, bytes, cost,"
            " access_predicates, filter_predicates) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            rows,
        )
        self._hard_commit()
        cur.close()
        return StatementResult(statement_kind=StatementKind.DDL, rows_affected=0)

    def _recompile(self, match: re.Match[str]) -> StatementResult:
        """Re-derive an object status from its stored source, as ALTER ... COMPILE does."""

        obj_type = re.sub(r"\s+", " ", match.group(1)).upper()
        owner = (match.group(2) or self._spec.username).upper()
        name = match.group(3).upper()
        cur = self._conn.cursor()
        rows = cur.execute(
            "SELECT text FROM all_source WHERE owner=? AND name=? AND type=? ORDER BY line",
            (owner, name, obj_type),
        ).fetchall()
        if not rows:
            cur.close()
            raise OracleError(
                f"ORA-04043: object {owner}.{name} does not exist",
                oracle_code="ORA-04043",
            )
        errors = _stand_in_compiler_errors([row[0] for row in rows])
        status = "INVALID" if errors else "VALID"
        cur.execute(
            "DELETE FROM all_errors WHERE owner=? AND name=? AND type=?", (owner, name, obj_type)
        )
        cur.executemany(
            "INSERT INTO all_errors (owner, name, type, sequence, line, position, text,"
            " attribute, message_number) VALUES (?,?,?,?,?,?,?,'ERROR',?)",
            [
                (owner, name, obj_type, i, e.line, e.position, e.text, e.message_number)
                for i, e in enumerate(errors, start=1)
            ],
        )
        cur.execute(
            "UPDATE all_objects SET status=?, last_ddl_time=datetime('now')"
            " WHERE owner=? AND object_name=? AND object_type=?",
            (status, owner, name, obj_type),
        )
        self._hard_commit()
        cur.close()
        return StatementResult(
            statement_kind=StatementKind.DDL,
            rows_affected=0,
            compiler_errors=errors,
            warnings=[] if not errors else [f"{owner}.{name} is still INVALID."],
        )

    def _execute_plsql(
        self,
        statement: str,
        binds: dict[str, Any],
        kind: StatementKind,
        limits: ExecutionLimits,
    ) -> StatementResult:
        if kind == StatementKind.PLSQL_SOURCE:
            return self._compile_source(statement)
        if _GATHER_TABLE_STATS.search(statement):
            return self._gather_table_stats(binds)
        return self._run_anonymous_block(statement, limits)

    def _gather_table_stats(self, binds: dict[str, Any]) -> StatementResult:
        """Stand in for DBMS_STATS.GATHER_TABLE_STATS on the fixture schema.

        It counts the rows and records the collection time, which is what the
        verification query then reads back. It does not model sampling, histograms,
        or plan invalidation.
        """

        owner = str(binds.get("owner", self._spec.username)).upper()
        table = str(binds.get("table_name", "")).upper()
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_$#]*", table):
            raise ValidationError(
                "gather_table_stats requires a simple table name.",
                detail={"tableName": table[:64]},
            )
        cur = self._conn.cursor()
        exists = cur.execute(
            "SELECT 1 FROM all_tables WHERE owner=? AND table_name=?", (owner, table)
        ).fetchone()
        if not exists:
            cur.close()
            raise OracleError(
                f"ORA-20000: table {owner}.{table} does not exist or insufficient privileges",
                oracle_code="ORA-20000",
            )
        try:
            # The table name is matched against the pattern above and confirmed to
            # exist in the seeded dictionary, so it cannot carry a payload here.
            count = cur.execute(
                f"SELECT COUNT(*) FROM {table.lower()}"  # noqa: S608
            ).fetchone()[0]
        except sqlite3.Error as exc:
            cur.close()
            raise _as_oracle_error(exc) from exc
        cur.execute(
            "UPDATE all_tables SET num_rows=?, last_analyzed=datetime('now')"
            " WHERE owner=? AND table_name=?",
            (count, owner, table),
        )
        self._hard_commit()
        cur.close()
        return StatementResult(statement_kind=StatementKind.PLSQL_BLOCK, rows_affected=0)

    def _compile_source(self, statement: str) -> StatementResult:
        match = re.match(
            r"^CREATE\s+(?:OR\s+REPLACE\s+)?(?:EDITIONABLE\s+|NONEDITIONABLE\s+)?"
            r"(PACKAGE\s+BODY|PACKAGE|PROCEDURE|FUNCTION|TRIGGER|TYPE\s+BODY|TYPE)\s+"
            r'(?:"?([A-Za-z0-9_$#]+)"?\s*\.\s*)?"?([A-Za-z0-9_$#]+)"?',
            statement.strip(),
            re.IGNORECASE | re.DOTALL,
        )
        if not match:
            raise ValidationError(
                "The stand-in backend could not identify the object being compiled."
            )
        obj_type = re.sub(r"\s+", " ", match.group(1)).upper()
        owner = (match.group(2) or self._spec.username).upper()
        name = match.group(3).upper()

        body_lines = statement.splitlines()
        errors = _stand_in_compiler_errors(body_lines)
        status = "INVALID" if errors else "VALID"

        cur = self._conn.cursor()
        cur.execute(
            "DELETE FROM all_source WHERE owner=? AND name=? AND type=?", (owner, name, obj_type)
        )
        cur.executemany(
            "INSERT INTO all_source (owner, name, type, line, text) VALUES (?,?,?,?,?)",
            [(owner, name, obj_type, i, text) for i, text in enumerate(body_lines, start=1)],
        )
        cur.execute(
            "DELETE FROM all_errors WHERE owner=? AND name=? AND type=?", (owner, name, obj_type)
        )
        cur.executemany(
            "INSERT INTO all_errors (owner, name, type, sequence, line, position, text,"
            " attribute, message_number) VALUES (?,?,?,?,?,?,?,'ERROR',?)",
            [
                (owner, name, obj_type, i, e.line, e.position, e.text, e.message_number)
                for i, e in enumerate(errors, start=1)
            ],
        )
        cur.execute(
            "DELETE FROM all_objects WHERE owner=? AND object_name=? AND object_type=?",
            (owner, name, obj_type),
        )
        cur.execute(
            "INSERT INTO all_objects (owner, object_name, object_type, status, created,"
            " last_ddl_time) VALUES (?,?,?,?,datetime('now'),datetime('now'))",
            (owner, name, obj_type, status),
        )
        self._hard_commit()
        cur.close()
        return StatementResult(
            statement_kind=StatementKind.PLSQL_SOURCE,
            compiler_errors=errors,
            rows_affected=0,
            warnings=(
                []
                if not errors
                else [f"{obj_type} {owner}.{name} compiled with {len(errors)} error(s)."]
            ),
        )

    def _run_anonymous_block(self, statement: str, limits: ExecutionLimits) -> StatementResult:
        """Support the narrow slice of PL/SQL the fixtures need.

        Only DBMS_OUTPUT.PUT_LINE with literal or concatenated literal arguments is
        interpreted. Anything else is refused by name so a green test never implies
        real PL/SQL support.
        """

        emitted = 0
        for match in re.finditer(
            r"DBMS_OUTPUT\.PUT_LINE\s*\(\s*(.+?)\s*\)\s*;", statement, re.IGNORECASE | re.DOTALL
        ):
            expression = match.group(1)
            parts = [p.strip() for p in expression.split("||")]
            rendered = []
            for part in parts:
                literal = re.fullmatch(r"'(.*)'", part, re.DOTALL)
                if literal:
                    rendered.append(literal.group(1).replace("''", "'"))
                else:
                    rendered.append(_evaluate_scalar(self._conn, part))
            self._dbms_output.append("".join(rendered))
            emitted += 1
        if emitted == 0:
            raise OracleError(
                "The local stand-in backend does not execute PL/SQL. Point the profile "
                "at a real Oracle target to run this block.",
                oracle_code="HARNESS-0001",
                detail={"backend": "fake"},
            )
        return StatementResult(statement_kind=StatementKind.PLSQL_BLOCK, rows_affected=0)

    # -- transaction and lifecycle -------------------------------------------------

    def _begin_if_needed(self, kind: StatementKind) -> None:
        if kind in (StatementKind.DML, StatementKind.UNKNOWN) and not self._transaction_open:
            self._conn.execute("BEGIN")
            self._transaction_open = True

    def _hard_commit(self) -> None:
        """Commit whatever is open, used by paths that emulate Oracle DDL.

        Oracle commits the caller transaction before DDL. The stand-in runs in
        autocommit mode, so COMMIT is only issued when a BEGIN is actually open.
        """

        if self._transaction_open or self._conn.in_transaction:
            self._conn.execute("COMMIT")
        self._transaction_open = False

    def commit(self) -> None:
        failure, self._next_commit_failure = self._next_commit_failure, None
        if failure == "lost":
            # The COMMIT reached the wire and the answer never came back. Oracle may
            # have made the transaction durable; the stand-in reports exactly what the
            # oracledb adapter reports for ORA-03113 on a commit.
            self._broken = True
            self._conn.close()
            self._closed = True
            raise OutcomeUnknownError(
                "The connection was lost while the commit was in flight. Whether Oracle "
                "committed the transaction is unknown; verify in the database before "
                "retrying.",
                detail={"oracleCode": "ORA-03113"},
            )
        if failure == "refused":
            raise OracleError(
                "ORA-02290: check constraint violated on a deferred constraint",
                oracle_code="ORA-02290",
            )
        if self._transaction_open:
            self._hard_commit()

    def rollback(self) -> None:
        if self._transaction_open:
            self._conn.execute("ROLLBACK")
            self._transaction_open = False

    def cancel(self) -> bool:
        self._cancel_requested.set()
        self._conn.interrupt()
        return True

    def close(self) -> None:
        if self._closed:
            return
        try:
            self.rollback()
        except sqlite3.Error:  # pragma: no cover - defensive
            pass
        finally:
            self._conn.close()
            self._closed = True

    def is_healthy(self) -> bool:
        if self._closed or self._broken:
            return False
        try:
            self._conn.execute("SELECT 1").fetchone()
        except sqlite3.Error:  # pragma: no cover - defensive
            self._broken = True
            return False
        return True

    def fail_next_commit(self, *, lost: bool = False) -> None:
        """Make the next commit fail, so commit handling can be tested.

        ``lost=True`` simulates the connection dying with the COMMIT in flight, which
        leaves the transaction's fate genuinely unknown. Otherwise the commit is
        refused definitely and the session stays usable with its work still pending.
        """

        self._next_commit_failure = "lost" if lost else "refused"

    def break_for_test(self) -> None:
        """Simulate a lost connection so failure handling can be tested."""

        self._broken = True
        self._conn.close()
        self._closed = True

    @property
    def transaction_open(self) -> bool:
        return self._transaction_open

    @property
    def is_broken(self) -> bool:
        return self._broken


class FakeOracleBackend(OracleBackend):
    """Creates stand-in sessions, one SQLite file per registered target."""

    name = "fake"

    def __init__(self, data_dir: str | os.PathLike[str] | None = None) -> None:
        base = Path(data_dir) if data_dir else Path(tempfile.gettempdir()) / "oracledbharness-fake"
        base.mkdir(parents=True, exist_ok=True)
        self._base = base
        self._sid = 1000
        self._lock = threading.Lock()

    def path_for(self, spec: ConnectionSpec) -> Path:
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", f"{spec.host}_{spec.port}_{spec.service_name}")
        return self._base / f"{safe}.sqlite3"

    def connect(self, spec: ConnectionSpec) -> FakeOracleConnection:
        path = self.path_for(spec)
        with self._lock:
            self._sid += 1
            sid = self._sid
            if not path.exists():
                self._create(path)
        return FakeOracleConnection(spec, path, sid)

    def reset(self, spec: ConnectionSpec) -> None:
        """Drop and reseed one target. Used by tests and the demo seed command."""

        path = self.path_for(spec)
        with self._lock:
            if path.exists():
                path.unlink()
            self._create(path)

    def _create(self, path: Path) -> None:
        conn = sqlite3.connect(str(path), isolation_level=None)
        try:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
            conn.executescript(_SEED_SCRIPT)
            cur = conn.cursor()
            already = cur.execute(
                "SELECT value FROM harness_fake_state WHERE key='seeded'"
            ).fetchone()
            if not already:
                # One transaction for the whole fixture load. Seeding row by row in
                # autocommit mode costs a disk sync per row and takes minutes.
                conn.execute("BEGIN")
                try:
                    _seed_rows(cur)
                except Exception:
                    conn.execute("ROLLBACK")
                    raise
                conn.execute("COMMIT")
        finally:
            conn.close()


# -- translation helpers -----------------------------------------------------------


def _translate(sql: str) -> tuple[str, int | None]:
    """Rewrite the Oracle spellings the seeded queries use into SQLite.

    The translation is intentionally small and syntactic. Anything it does not know
    reaches SQLite unchanged and fails there with a clear error.
    """

    text = sql
    forced_limit: int | None = None

    match = _OFFSET_FETCH.search(text)
    if match:
        offset_token, limit_token = match.group(1), match.group(2)
        if not limit_token.startswith(":"):
            forced_limit = int(limit_token)
        text = _OFFSET_FETCH.sub(f"LIMIT {limit_token} OFFSET {offset_token}", text)
    else:
        match = _FETCH_FIRST.search(text)
        if match:
            limit_token = match.group(1)
            if not limit_token.startswith(":"):
                forced_limit = int(limit_token)
            text = _FETCH_FIRST.sub(f"LIMIT {limit_token}", text)

    match = _ROWNUM.search(text)
    rownum_limit: int | None = None
    if match:
        rownum_limit = int(match.group(1))
        forced_limit = min(forced_limit, rownum_limit) if forced_limit else rownum_limit
        text = _ROWNUM.sub("", text)

    text = _DUAL.sub("", text)
    text = _DYNAMIC_VIEW.sub(lambda m: "v_" + m.group(1).lower(), text)
    for pattern, replacement in _SIMPLE_FUNCTIONS:
        text = pattern.sub(replacement, text)
    if rownum_limit is not None and " LIMIT " not in text.upper():
        text = text.rstrip().rstrip(";") + f" LIMIT {rownum_limit}"
    return text, forced_limit


def _filter_binds(sql: str, binds: dict[str, Any]) -> dict[str, Any]:
    """SQLite rejects binds a statement does not reference; Oracle behaves the same."""

    referenced = set(re.findall(r"(?<![:\w]):([A-Za-z_][A-Za-z0-9_$#]*)", sql))
    missing = referenced - set(binds)
    if missing:
        raise ValidationError(
            "Missing bind values: " + ", ".join(sorted(missing)),
            detail={"missingBinds": sorted(missing)},
        )
    return {name: value for name, value in binds.items() if name in referenced}


def _shape_value(value: Any, lob_preview_bytes: int) -> Any:
    if isinstance(value, bytes):
        preview = value[:lob_preview_bytes]
        return {
            "kind": "lob",
            "preview": preview.hex(),
            "byteLength": len(value),
            "truncated": len(value) > len(preview),
        }
    if isinstance(value, str) and lob_preview_bytes and len(value) > lob_preview_bytes:
        return {
            "kind": "lob",
            "preview": value[:lob_preview_bytes],
            "byteLength": len(value),
            "truncated": True,
        }
    return value


def _evaluate_scalar(conn: sqlite3.Connection, expression: str) -> str:
    try:
        translated, _ = _translate(f"SELECT {expression}")
        row = conn.execute(translated).fetchone()
    except sqlite3.Error as exc:
        raise OracleError(
            f"The stand-in backend cannot evaluate {expression!r} inside DBMS_OUTPUT.",
            oracle_code="HARNESS-0002",
        ) from exc
    return "" if row is None or row[0] is None else str(row[0])


def _stand_in_compiler_errors(lines: list[str]) -> list[CompilerError]:
    """Produce compiler errors for the two failures the fixtures rely on."""

    errors: list[CompilerError] = []
    known_tables = {
        "DEPARTMENTS",
        "EMPLOYEES",
        "ORDER_LINES",
        "DUAL",
    }
    for number, text in enumerate(lines, start=1):
        if INVALID_MARKER in text.lower():
            errors.append(
                CompilerError(
                    line=number,
                    position=text.lower().index(INVALID_MARKER) + 1,
                    text="PLS-00103: fixture marker requested a compilation failure",
                    messageNumber=103,
                )
            )
        for match in re.finditer(r"\bFROM\s+([A-Za-z][A-Za-z0-9_$#]*)", text, re.IGNORECASE):
            table = match.group(1).upper()
            if table not in known_tables:
                errors.append(
                    CompilerError(
                        line=number,
                        position=match.start(1) + 1,
                        text=f"PL/SQL: ORA-00942: table or view does not exist ({table})",
                        messageNumber=942,
                    )
                )
    return errors


def _as_oracle_error(exc: sqlite3.Error) -> OracleError:
    message = str(exc)
    mapping = [
        ("no such table", "ORA-00942", "table or view does not exist"),
        ("no such column", "ORA-00904", "invalid identifier"),
        ("UNIQUE constraint failed", "ORA-00001", "unique constraint violated"),
        ("FOREIGN KEY constraint failed", "ORA-02291", "integrity constraint violated"),
        ("NOT NULL constraint failed", "ORA-01400", "cannot insert NULL"),
        ("syntax error", "ORA-00900", "invalid SQL statement"),
        ("interrupted", "ORA-01013", "user requested cancel of current operation"),
    ]
    for needle, code, description in mapping:
        if needle.lower() in message.lower():
            return OracleError(f"{code}: {description} ({message})", oracle_code=code)
    return OracleError(message, oracle_code="ORA-00000")
