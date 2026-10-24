"""Unit tests for observation-only fleet discovery (A8 / P5 preparation).

Covers:
1. Grep verification: forbidden pack-licensed features are strictly absent from SQL and code.
2. Redaction: default stable hashed identifiers vs --show-names opt-in.
3. ORA-00942 and ORA-01031 tolerance: missing grants report 'not visible to this account'.
4. Output shape: JSON and Markdown data structure validation.
5. Query loader: parsing queries from oracle/discovery/fleet_discovery.sql.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from scripts.fleet_discovery import (
    Redactor,
    discover_target,
    load_queries_from_sql,
    render_markdown,
)

FORBIDDEN_PATTERNS = [
    r"DBA_HIST_",
    r"V\$ACTIVE_SESSION_HISTORY",
    r"\bASH\b",
    r"V\$SQL_MONITOR",
    r"DBMS_SQL_MONITOR",
    r"DBMS_SQLTUNE",
    r"DBMS_WORKLOAD_",
    r"DBMS_ADDM",
    r"DBA_ADVISOR_",
]


class FakeDriverErrorInfo:
    """Simulates driver error payload."""

    def __init__(self, full_code: str, message: str) -> None:
        self.full_code = full_code
        self.message = message


class FakeDriverError(Exception):
    """Simulates driver DatabaseError."""

    def __init__(self, full_code: str, message: str) -> None:
        self.full_code = full_code
        self.message = message
        super().__init__(FakeDriverErrorInfo(full_code, message))


class FakeCursor:
    """Offline stub cursor returning predetermined query results or raising ORA errors."""

    def __init__(self, error_queries: dict[str, str] | None = None) -> None:
        self.error_queries = error_queries or {}
        self.description: list[tuple[str, ...]] | None = None
        self._rows: list[tuple[Any, ...]] = []
        self._row_idx = 0

    def execute(self, sql: str, *args: Any, **kwargs: Any) -> None:
        clean_sql = " ".join(sql.split()).upper()

        # Check for simulated errors
        for key, err_code in self.error_queries.items():
            if key.upper() in clean_sql:
                raise FakeDriverError(err_code, f"{err_code}: simulated error for {key}")

        # Dispatch mocked responses
        if "FROM V$INSTANCE" in clean_sql and "EDITION" in clean_sql:
            self.description = [("VERSION",), ("VERSION_FULL",), ("EDITION",)]
            self._rows = [("19.0.0.0.0", "19.9.0.0.0", "EE")]
        elif "FROM V$VERSION" in clean_sql:
            self.description = [("BANNER_FULL",)]
            banner = (
                "Oracle Database 19c Enterprise Edition Release 19.0.0.0.0 - Production\n"
                "Version 19.9.0.0.0"
            )
            self._rows = [(banner,)]
        elif "FROM DBA_REGISTRY_SQLPATCH" in clean_sql:
            self.description = [
                ("PATCH_ID",),
                ("PATCH_UID",),
                ("ACTION",),
                ("STATUS",),
                ("ACTION_TIME",),
                ("DESCRIPTION",),
            ]
            self._rows = [
                (
                    31281373,
                    23812739,
                    "APPLY",
                    "SUCCESS",
                    "2020-10-15 12:00:00",
                    "Database Release Update : 19.9.0.0.201020",
                )
            ]
        elif "FROM GV$INSTANCE" in clean_sql and "COUNT" in clean_sql:
            self.description = [("INSTANCE_COUNT",)]
            self._rows = [(2,)]
        elif "FROM V$INSTANCE" in clean_sql and "COUNT" in clean_sql:
            self.description = [("INSTANCE_COUNT",)]
            self._rows = [(1,)]
        elif "CLUSTER_DATABASE" in clean_sql:
            self.description = [("VALUE",)]
            self._rows = [("TRUE",)]
        elif "FROM GV$INSTANCE ORDER BY INST_ID" in clean_sql:
            self.description = [
                ("INST_ID",),
                ("INSTANCE_NUMBER",),
                ("INSTANCE_NAME",),
                ("HOST_NAME",),
            ]
            self._rows = [
                (1, 1, "PROD1", "node1.corp.example.com"),
                (2, 2, "PROD2", "node2.corp.example.com"),
            ]
        elif "FROM V$DATABASE" in clean_sql and "DATABASE_ROLE" in clean_sql:
            self.description = [
                ("DATABASE_ROLE",),
                ("PROTECTION_MODE",),
                ("OPEN_MODE",),
                ("FORCE_LOGGING",),
                ("FLASHBACK_ON",),
                ("DATAGUARD_BROKER",),
            ]
            self._rows = [("PRIMARY", "MAXIMUM PERFORMANCE", "READ WRITE", "YES", "NO", "ENABLED")]
        elif "FROM V$ARCHIVE_DEST_STATUS" in clean_sql:
            self.description = [("REMOTE_DEST_COUNT",)]
            self._rows = [(1,)]
        elif "CDB FROM V$DATABASE" in clean_sql:
            self.description = [("CDB",)]
            self._rows = [("YES",)]
        elif "SYS_CONTEXT('USERENV', 'CON_ID')" in clean_sql:
            self.description = [("CON_ID",), ("CON_NAME",)]
            self._rows = [("3", "ORCLPDB1")]
        elif "FROM V$PDBS" in clean_sql and "COUNT" in clean_sql and "GROUP BY" not in clean_sql:
            self.description = [("PDB_COUNT",)]
            self._rows = [(1,)]
        elif "FROM V$PDBS GROUP BY OPEN_MODE" in clean_sql:
            self.description = [("OPEN_MODE",), ("MODE_COUNT",)]
            self._rows = [("READ WRITE", 1)]
        elif "FROM V$PDBS ORDER BY CON_ID" in clean_sql:
            self.description = [("CON_ID",), ("NAME",), ("OPEN_MODE",), ("RESTRICTED",)]
            self._rows = [(3, "ORCLPDB1", "READ WRITE", "NO")]
        elif "NETWORK_PROTOCOL" in clean_sql:
            self.description = [("NETWORK_PROTOCOL",), ("AUTHENTICATION_METHOD",)]
            self._rows = [("tcps", "PASSWORD")]
        elif "FROM V$SESSION_CONNECT_INFO" in clean_sql:
            self.description = [("NETWORK_SERVICE_BANNER",)]
            self._rows = [
                (
                    "Oracle Advanced Security: encryption service adapter for Linux: Version 19.0.0.0.0",
                ),
                (
                    "Oracle Advanced Security: crypto-checksumming service adapter for Linux: Version 19.0.0.0.0",
                ),
            ]
        elif "FROM NLS_DATABASE_PARAMETERS" in clean_sql:
            self.description = [("PARAMETER",), ("VALUE",)]
            self._rows = [
                ("NLS_CHARACTERSET", "AL32UTF8"),
                ("NLS_NCHAR_CHARACTERSET", "AL16UTF16"),
            ]
        elif "CONTROL_MANAGEMENT_PACK_ACCESS" in clean_sql:
            self.description = [("VALUE",)]
            self._rows = [("NONE",)]
        elif "EDITION FROM V$INSTANCE" in clean_sql:
            self.description = [("EDITION",)]
            self._rows = [("EE",)]
        elif "SELECT 1 FROM DUAL" in clean_sql:
            self.description = [("1",)]
            self._rows = [(1,)]
        elif "SELECT COUNT(*) FROM" in clean_sql:
            self.description = [("COUNT(*)",)]
            self._rows = [(0,)]
        else:
            self.description = [("DUMMY",)]
            self._rows = [("OK",)]

        self._row_idx = 0

    def fetchone(self) -> tuple[Any, ...] | None:
        if self._row_idx < len(self._rows):
            row = self._rows[self._row_idx]
            self._row_idx += 1
            return row
        return None

    def fetchall(self) -> list[tuple[Any, ...]]:
        remaining = self._rows[self._row_idx :]
        self._row_idx = len(self._rows)
        return remaining


def test_forbidden_feature_grep() -> None:
    """Assert that pack-licensed features are strictly absent from SQL and discovery code."""
    repo_root = Path(__file__).resolve().parent.parent.parent
    sql_file = repo_root / "oracle" / "discovery" / "fleet_discovery.sql"
    runner_file = repo_root / "scripts" / "fleet_discovery.py"
    wrapper_file = repo_root / "scripts" / "fleet_discovery.sh"

    assert sql_file.is_file(), f"Missing SQL file: {sql_file}"
    assert runner_file.is_file(), f"Missing runner: {runner_file}"
    assert wrapper_file.is_file(), f"Missing wrapper: {wrapper_file}"

    sql_content = sql_file.read_text(encoding="utf-8")
    for pattern in FORBIDDEN_PATTERNS:
        matches = re.findall(pattern, sql_content, flags=re.IGNORECASE)
        assert not matches, f"Forbidden pattern '{pattern}' in {sql_file.name}: {matches}"

    # Verify that parsed queries do not contain forbidden patterns
    queries = load_queries_from_sql(sql_file)
    for q_name, q_sql in queries.items():
        for pattern in FORBIDDEN_PATTERNS:
            matches = re.findall(pattern, q_sql, flags=re.IGNORECASE)
            assert not matches, f"Forbidden pattern '{pattern}' in query '{q_name}': {matches}"


def test_redaction_default_stable_hashing() -> None:
    """Assert deterministic hashing of hostnames, IPs, DBs, instances, and PDBs."""
    redactor = Redactor(show_names=False)

    host1 = "rac-node1.corp.example.com"
    host2 = "rac-node2.corp.example.com"

    red_host1 = redactor.redact_host(host1)
    red_host1_again = redactor.redact_host(host1)
    red_host2 = redactor.redact_host(host2)

    assert red_host1.startswith("host_")
    assert red_host1 == red_host1_again, "Hash must be deterministic and stable"
    assert red_host1 != red_host2, "Different hosts must have different hashes"
    assert host1 not in red_host1, "Raw hostname leaked"

    ip = "192.0.2.100"
    red_ip = redactor.redact_ip(ip)
    assert red_ip.startswith("ip_")
    assert ip not in red_ip

    db = "PRODDB"
    red_db = redactor.redact_db(db)
    assert red_db.startswith("db_")
    assert db not in red_db

    inst = "PRODDB1"
    red_inst = redactor.redact_instance(inst)
    assert red_inst.startswith("inst_")
    assert inst not in red_inst

    pdb = "SALES_PDB"
    red_pdb = redactor.redact_pdb(pdb)
    assert red_pdb.startswith("pdb_")
    assert pdb not in red_pdb

    svc = "sales.corp.example.com"
    red_svc = redactor.redact_service(svc)
    assert red_svc.startswith("svc_")
    assert svc not in red_svc

    # DSN redaction
    dsn = f"{host1}:1521/{svc}"
    red_dsn = redactor.redact_dsn(dsn)
    assert "1521" in red_dsn
    assert host1 not in red_dsn
    assert svc not in red_dsn
    assert red_host1 in red_dsn
    assert red_svc in red_dsn

    # Free-text banner redaction
    banner = f"Connected to {host1} ({ip}) running instance {inst}"
    red_banner = redactor.redact_text(banner)
    assert host1 not in red_banner
    assert ip not in red_banner
    assert inst not in red_banner
    assert red_host1 in red_banner
    assert red_inst in red_banner


def test_redaction_opt_in_show_names() -> None:
    """Assert that passing show_names=True preserves real identifiers."""
    redactor = Redactor(show_names=True)

    host = "db1.example.com"
    ip = "192.0.2.5"
    db = "TESTDB"
    inst = "TESTDB1"
    pdb = "PDB1"
    svc = "svc1.example.com"
    dsn = f"{host}:1521/{svc}"

    assert redactor.redact_host(host) == host
    assert redactor.redact_ip(ip) == ip
    assert redactor.redact_db(db) == db
    assert redactor.redact_instance(inst) == inst
    assert redactor.redact_pdb(pdb) == pdb
    assert redactor.redact_service(svc) == svc
    assert redactor.redact_dsn(dsn) == dsn
    assert redactor.redact_text(f"Host {host} IP {ip}") == f"Host {host} IP {ip}"


def test_ora_00942_and_01031_tolerance() -> None:
    """Assert that missing views and privileges report 'not visible to this account'."""
    repo_root = Path(__file__).resolve().parent.parent.parent
    sql_file = repo_root / "oracle" / "discovery" / "fleet_discovery.sql"
    queries = load_queries_from_sql(sql_file)

    # Simulate restricted account missing DBA_REGISTRY_SQLPATCH and V$DATABASE
    error_queries = {
        "DBA_REGISTRY_SQLPATCH": "ORA-00942",
        "V$DATABASE": "ORA-01031",
        "V$SQL_PLAN": "ORA-00942",
    }
    fake_cur = FakeCursor(error_queries=error_queries)
    redactor = Redactor(show_names=False)

    report = discover_target(
        cursor=fake_cur,
        redactor=redactor,
        queries=queries,
        dsn="host.example.com:1521/svc",
        user="restricted_user",
        driver_mode="thin",
        target_label="restricted_target",
    )

    # Check that ORA-00942 on SQLPATCH reports not visible
    assert report["version"]["latestSqlpatch"] == "not visible to this account"

    # Check that ORA-01031 on V$DATABASE reports not visible
    assert report["dataGuard"]["databaseRole"] == "not visible to this account"
    assert report["dataGuard"]["protectionMode"] == "not visible to this account"
    assert report["cdbPdb"]["isCdb"] == "not visible to this account"

    # Check that visible views still succeeded
    assert report["version"]["version"] == "19.0.0.0.0"
    assert report["rac"]["isRac"] is True

    # Check readiness capabilities
    readiness = report["readiness"]
    assert readiness["connect"]["available"] is True
    assert readiness["display_cursor"]["available"] is False
    assert readiness["display_cursor"]["detail"] == "not visible to this account"


def test_output_shape_json_and_markdown() -> None:
    """Assert structure and completeness of discovery JSON and Markdown."""
    repo_root = Path(__file__).resolve().parent.parent.parent
    sql_file = repo_root / "oracle" / "discovery" / "fleet_discovery.sql"
    queries = load_queries_from_sql(sql_file)

    fake_cur = FakeCursor()
    redactor = Redactor(show_names=False)

    report = discover_target(
        cursor=fake_cur,
        redactor=redactor,
        queries=queries,
        dsn="db-prod.example.com:1521/service_main",
        user="harness_ro",
        driver_mode="thin",
        target_label="prod_db_01",
    )

    # 1. Top-level keys
    expected_top_keys = {
        "target",
        "discoveredAt",
        "showNames",
        "connection",
        "version",
        "rac",
        "dataGuard",
        "cdbPdb",
        "network",
        "characterSets",
        "licensing",
        "readiness",
    }
    assert expected_top_keys.issubset(set(report.keys()))

    # 2. Section specifics
    assert report["target"] == "prod_db_01"
    assert report["connection"]["driverMode"] == "thin"
    assert "db-prod.example.com" not in report["connection"]["dsn"]

    assert report["version"]["versionFull"] == "19.9.0.0.0"
    assert report["rac"]["isRac"] is True
    assert report["rac"]["instanceCountGv"] == 2
    assert len(report["rac"]["instances"]) == 2

    assert report["dataGuard"]["databaseRole"] == "PRIMARY"
    assert report["dataGuard"]["remoteDestinationsCount"] == 1

    assert report["cdbPdb"]["isCdb"] is True
    assert report["cdbPdb"]["pdbCount"] == 1
    assert "READ WRITE" in report["cdbPdb"]["openModes"]

    assert report["network"]["networkProtocol"] == "tcps"
    assert len(report["network"]["serviceBanners"]) == 2

    assert report["characterSets"]["nlsCharacterset"] == "AL32UTF8"
    assert report["characterSets"]["nlsNcharCharacterset"] == "AL16UTF16"

    assert report["licensing"]["controlManagementPackAccess"] == "NONE"

    assert len(report["readiness"]) == 11
    for _cap, status in report["readiness"].items():
        assert "available" in status
        assert "detail" in status

    # 3. Markdown rendering
    md = render_markdown(report)
    assert "# Fleet Discovery Report: `prod_db_01`" in md
    assert "Observation-Only Fleet Discovery (A8 / P5 Preparation)" in md
    assert "human DBA action" in md
    assert "## 1. Version and Release Update (RU) Level" in md
    assert "## 2. RAC (Real Application Clusters)" in md
    assert "## 3. Data Guard and Replication" in md
    assert "## 4. Container & Pluggable Database (CDB / PDB)" in md
    assert "## 5. Network & Encryption (TCPS)" in md
    assert "## 6. Database Character Sets" in md
    assert "## 7. Licensing Flags (No Pack Features Used)" in md
    assert "## 8. Harness Readiness Probes" in md


def test_load_queries_from_sql() -> None:
    """Assert query parsing from oracle/discovery/fleet_discovery.sql."""
    repo_root = Path(__file__).resolve().parent.parent.parent
    sql_file = repo_root / "oracle" / "discovery" / "fleet_discovery.sql"

    queries = load_queries_from_sql(sql_file)
    expected_queries = [
        "version.instance_version",
        "version.banner_full",
        "version.sqlpatch",
        "rac.instance_count_gv",
        "rac.instance_count_v",
        "rac.cluster_database",
        "rac.instances",
        "data_guard.database_role",
        "data_guard.remote_destinations_count",
        "cdb_pdb.cdb",
        "cdb_pdb.session_con",
        "cdb_pdb.pdb_count",
        "cdb_pdb.pdb_open_modes",
        "cdb_pdb.pdbs",
        "tcps_network.session_context",
        "tcps_network.service_banners",
        "character_sets.parameters",
        "licensing.pack_access",
        "licensing.edition",
        "readiness.connect",
        "readiness.session_schema",
        "readiness.all_objects",
        "readiness.explain_plan",
        "readiness.display_cursor",
        "readiness.v_session",
        "readiness.v_sql",
        "readiness.dba_tablespaces",
        "readiness.dba_scheduler_jobs",
        "readiness.dbms_stats",
        "readiness.compile_objects",
    ]

    for eq in expected_queries:
        assert eq in queries, f"Query '{eq}' not parsed from {sql_file.name}"
        assert queries[eq].upper().startswith("SELECT"), f"Query '{eq}' does not start with SELECT"


def test_redaction_leaves_version_numbers_alone() -> None:
    redactor = Redactor()
    banner = "Oracle Database 19c Enterprise Edition Release 19.0.0.0.0 - Version 19.9.1.0.0"
    assert redactor.redact_text(banner) == banner
    redacted = redactor.redact_text("listener at 192.0.2.10:1521, RU 19.9.1.0.210119")
    assert "192.0.2.10" not in redacted
    assert "19.9.1.0.210119" in redacted
