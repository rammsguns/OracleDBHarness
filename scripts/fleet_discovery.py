#!/usr/bin/env python3
"""Observation-only fleet discovery runner for Oracle databases (A8 / P5 preparation).

Discovers fleet topology, version/RU level, RAC, Data Guard, CDB/PDB structure,
network encryption/TCPS, character sets, licensing parameters, and harness readiness
without using pack-licensed features.

Tolerates ORA-00942 and ORA-01031 per query by reporting 'not visible to this account'.
By default, all hostnames, IPs, database names, instance names, PDB names, and service
names are redacted using stable, deterministic hashes (pass --show-names to opt in).

Exits non-zero ONLY on connection failure.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, overload

try:
    import oracledb
except ImportError:
    oracledb = None  # type: ignore[assignment]


_IPV4_PATTERN = re.compile(r"\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b")


class Redactor:
    """Stable, deterministic identifier redactor.

    Replaces hostnames, IPs, database names, instance names, PDB names, and service
    names with hashed placeholders (e.g. host_a1b2c3d4) unless show_names is enabled.
    """

    def __init__(self, show_names: bool = False, salt: str = "") -> None:
        self.show_names = show_names
        self.salt = salt
        self._cache: dict[str, str] = {}
        self._known_names: dict[str, str] = {}

    @overload
    def redact(self, value: None, kind: str) -> None: ...
    @overload
    def redact(self, value: str, kind: str) -> str: ...
    def redact(self, value: str | None, kind: str) -> str | None:
        if value is None or self.show_names:
            return value
        trimmed = value.strip()
        if not trimmed:
            return value
        # Skip standard constant tokens
        if trimmed.upper() in {
            "YES",
            "NO",
            "TRUE",
            "FALSE",
            "NONE",
            "NOT VISIBLE TO THIS ACCOUNT",
            "NOT SUPPORTED IN THIS DATABASE VERSION",
            "<NONE>",
            "<NULL>",
            "UNKNOWN",
        }:
            return value

        cache_key = f"{kind}:{trimmed.upper()}"
        if cache_key not in self._cache:
            digest = hashlib.sha256((trimmed.upper() + self.salt).encode("utf-8")).hexdigest()[:8]
            redacted = f"{kind}_{digest}"
            self._cache[cache_key] = redacted
            self._known_names[trimmed] = redacted
            self._known_names[trimmed.upper()] = redacted

        return self._cache[cache_key]

    @overload
    def redact_host(self, host: None) -> None: ...
    @overload
    def redact_host(self, host: str) -> str: ...
    def redact_host(self, host: str | None) -> str | None:
        if host is None:
            return None
        return self.redact(host, "host")

    @overload
    def redact_ip(self, ip: None) -> None: ...
    @overload
    def redact_ip(self, ip: str) -> str: ...
    def redact_ip(self, ip: str | None) -> str | None:
        if ip is None:
            return None
        return self.redact(ip, "ip")

    @overload
    def redact_db(self, db_name: None) -> None: ...
    @overload
    def redact_db(self, db_name: str) -> str: ...
    def redact_db(self, db_name: str | None) -> str | None:
        if db_name is None:
            return None
        return self.redact(db_name, "db")

    @overload
    def redact_instance(self, inst_name: None) -> None: ...
    @overload
    def redact_instance(self, inst_name: str) -> str: ...
    def redact_instance(self, inst_name: str | None) -> str | None:
        if inst_name is None:
            return None
        return self.redact(inst_name, "inst")

    @overload
    def redact_pdb(self, pdb_name: None) -> None: ...
    @overload
    def redact_pdb(self, pdb_name: str) -> str: ...
    def redact_pdb(self, pdb_name: str | None) -> str | None:
        if pdb_name is None:
            return None
        return self.redact(pdb_name, "pdb")

    @overload
    def redact_service(self, svc_name: None) -> None: ...
    @overload
    def redact_service(self, svc_name: str) -> str: ...
    def redact_service(self, svc_name: str | None) -> str | None:
        if svc_name is None:
            return None
        return self.redact(svc_name, "svc")

    @overload
    def redact_dsn(self, dsn: None) -> None: ...
    @overload
    def redact_dsn(self, dsn: str) -> str: ...
    def redact_dsn(self, dsn: str | None) -> str | None:
        if dsn is None or self.show_names:
            return dsn
        # Check for host:port/service
        simple_match = re.match(r"^([^:/]+):(\d+)(?:/([^?]+))?(?:\?(.*))?$", dsn)
        if simple_match:
            host, port, svc, query = simple_match.groups()
            redacted_host = self.redact_host(host)
            redacted_svc = f"/{self.redact_service(svc)}" if svc else ""
            redacted_query = f"?{query}" if query else ""
            return f"{redacted_host}:{port}{redacted_svc}{redacted_query}"

        # Otherwise redact known names and IPs in DSN
        return self.redact_text(dsn)

    def redact_text(self, text: str) -> str:
        if not text or self.show_names:
            return text
        result = text
        # Redact known registered names
        for raw, red in sorted(self._known_names.items(), key=lambda x: -len(x[0])):
            if len(raw) >= 3:
                result = re.sub(re.escape(raw), red, result, flags=re.IGNORECASE)

        # Redact any remaining IPv4 addresses
        def _ip_sub(match: re.Match[str]) -> str:
            ip_str = match.group(0)
            return self.redact_ip(ip_str)

        result = _IPV4_PATTERN.sub(_ip_sub, result)
        return result


def extract_error_code(exc: Exception) -> str:
    """Extract Oracle or driver error code from exception."""
    if hasattr(exc, "args") and exc.args:
        first_arg = exc.args[0]
        full_code = getattr(first_arg, "full_code", None)
        if full_code:
            return str(full_code).upper()
        code = getattr(first_arg, "code", None)
        if code:
            return f"ORA-{int(code):05d}"
    msg = str(exc).upper()
    for code_pattern in ("ORA-00942", "ORA-01031", "ORA-00904", "ORA-01013"):
        if code_pattern in msg:
            return code_pattern
    return ""


def load_queries_from_sql(path: Path) -> dict[str, str]:
    """Parse queries tagged with '-- @query: <name>' from fleet_discovery.sql."""
    if not path.is_file():
        raise FileNotFoundError(f"SQL file not found at {path}")
    content = path.read_text(encoding="utf-8")
    queries: dict[str, str] = {}
    current_key: str | None = None
    query_lines: list[str] = []

    for raw_line in content.splitlines():
        line = raw_line.strip()
        if line.startswith("-- @query:"):
            if current_key and query_lines:
                queries[current_key] = " ".join(query_lines).strip()
                query_lines = []
            current_key = line.split(":", 1)[1].strip()
        elif current_key and line.startswith("--") and not line.startswith("-- @"):
            query_content = line[2:].strip()
            if query_content:
                query_lines.append(query_content)
        elif current_key and not line.startswith("--"):
            if query_lines:
                queries[current_key] = " ".join(query_lines).strip()
                query_lines = []
            current_key = None

    if current_key and query_lines:
        queries[current_key] = " ".join(query_lines).strip()

    return queries


def execute_query(cursor: Any, sql: str) -> tuple[list[str], list[tuple[Any, ...]]] | str:
    """Execute a query, returning (column_names, rows) or error classification string."""
    try:
        cursor.execute(sql)
        cols = [col[0].lower() for col in cursor.description] if cursor.description else []
        rows = cursor.fetchall()
        return cols, rows
    except Exception as exc:  # noqa: BLE001
        code = extract_error_code(exc)
        if code in ("ORA-00942", "ORA-01031"):
            return "not visible to this account"
        if code == "ORA-00904":
            return "not supported in this database version"
        return f"error: {exc}"


def check_capability(cursor: Any, sql: str) -> dict[str, Any]:
    """Check a readiness capability probe."""
    try:
        cursor.execute(sql)
        cursor.fetchone()
        return {"available": True, "detail": ""}
    except Exception as exc:  # noqa: BLE001
        code = extract_error_code(exc)
        if code in ("ORA-00942", "ORA-01031"):
            return {"available": False, "detail": "not visible to this account"}
        return {"available": False, "detail": str(exc)}


def _safe_int(val: Any, default: int = 0) -> int:
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


def discover_target(
    cursor: Any,
    redactor: Redactor,
    queries: dict[str, str],
    dsn: str,
    user: str,
    driver_mode: str = "thin",
    target_label: str = "oracle_target",
) -> dict[str, Any]:
    """Run all discovery queries and return structured discovery report."""
    now_iso = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    # 1. Version / RU Level
    version_data: dict[str, Any] = {
        "version": None,
        "versionFull": None,
        "edition": None,
        "bannerFull": None,
        "latestSqlpatch": None,
    }
    q_inst = queries.get(
        "version.instance_version",
        "SELECT version, version_full, edition FROM v$instance",
    )
    res_inst = execute_query(cursor, q_inst)
    if isinstance(res_inst, tuple):
        cols, rows = res_inst
        if rows:
            row = dict(zip(cols, rows[0], strict=False))
            version_data["version"] = row.get("version")
            version_data["versionFull"] = row.get("version_full") or row.get("version")
            version_data["edition"] = row.get("edition")
    else:
        version_data["version"] = res_inst
        version_data["versionFull"] = res_inst

    q_banner = queries.get(
        "version.banner_full",
        "SELECT banner_full FROM v$version WHERE banner_full IS NOT NULL AND ROWNUM <= 1",
    )
    res_banner = execute_query(cursor, q_banner)
    if isinstance(res_banner, tuple):
        _, rows = res_banner
        if rows:
            version_data["bannerFull"] = redactor.redact_text(str(rows[0][0]))
    else:
        version_data["bannerFull"] = res_banner

    q_patch = queries.get(
        "version.sqlpatch",
        "SELECT patch_id, patch_uid, action, status, action_time, description "
        "FROM dba_registry_sqlpatch ORDER BY action_time DESC",
    )
    res_patch = execute_query(cursor, q_patch)
    if isinstance(res_patch, tuple):
        cols, rows = res_patch
        if rows:
            row = dict(zip(cols, rows[0], strict=False))
            version_data["latestSqlpatch"] = {
                "patchId": row.get("patch_id"),
                "patchUid": row.get("patch_uid"),
                "action": row.get("action"),
                "status": row.get("status"),
                "actionTime": str(row.get("action_time")),
                "description": row.get("description"),
            }
        else:
            version_data["latestSqlpatch"] = "<none applied>"
    else:
        version_data["latestSqlpatch"] = res_patch

    # 2. RAC
    rac_data: dict[str, Any] = {
        "isRac": False,
        "clusterDatabase": None,
        "instanceCountGv": None,
        "instanceCountV": None,
        "instances": [],
    }
    q_gv = queries.get("rac.instance_count_gv", "SELECT COUNT(*) FROM gv$instance")
    res_gv = execute_query(cursor, q_gv)
    if isinstance(res_gv, tuple):
        _, rows = res_gv
        rac_data["instanceCountGv"] = _safe_int(rows[0][0]) if rows else 0
    else:
        rac_data["instanceCountGv"] = res_gv

    q_v = queries.get("rac.instance_count_v", "SELECT COUNT(*) FROM v$instance")
    res_v = execute_query(cursor, q_v)
    if isinstance(res_v, tuple):
        _, rows = res_v
        rac_data["instanceCountV"] = _safe_int(rows[0][0]) if rows else 0
    else:
        rac_data["instanceCountV"] = res_v

    q_param = queries.get(
        "rac.cluster_database",
        "SELECT value FROM v$parameter WHERE name = 'cluster_database'",
    )
    res_param = execute_query(cursor, q_param)
    if isinstance(res_param, tuple):
        _, rows = res_param
        val = str(rows[0][0]).upper() if rows else "FALSE"
        rac_data["clusterDatabase"] = val == "TRUE"
        gv_cnt = rac_data["instanceCountGv"]
        if val == "TRUE" or (isinstance(gv_cnt, int) and gv_cnt > 1):
            rac_data["isRac"] = True
    else:
        rac_data["clusterDatabase"] = res_param

    q_insts = queries.get(
        "rac.instances",
        "SELECT inst_id, instance_number, instance_name, host_name "
        "FROM gv$instance ORDER BY inst_id",
    )
    res_insts = execute_query(cursor, q_insts)
    if isinstance(res_insts, tuple):
        cols, rows = res_insts
        for r in rows:
            row = dict(zip(cols, r, strict=False))
            inst_name = str(row.get("instance_name") or "")
            host_name = str(row.get("host_name") or "")
            rac_data["instances"].append(
                {
                    "instId": row.get("inst_id"),
                    "instanceNumber": row.get("instance_number"),
                    "instanceName": redactor.redact_instance(inst_name),
                    "hostName": redactor.redact_host(host_name),
                }
            )
    else:
        rac_data["instances"] = res_insts

    # 3. Data Guard
    dg_data: dict[str, Any] = {
        "databaseRole": None,
        "protectionMode": None,
        "openMode": None,
        "forceLogging": None,
        "flashbackOn": None,
        "dataguardBroker": None,
        "remoteDestinationsCount": None,
    }
    q_db = queries.get(
        "data_guard.database_role",
        "SELECT database_role, protection_mode, open_mode, "
        "force_logging, flashback_on, dataguard_broker FROM v$database",
    )
    res_db = execute_query(cursor, q_db)
    if isinstance(res_db, tuple):
        cols, rows = res_db
        if rows:
            row = dict(zip(cols, rows[0], strict=False))
            dg_data["databaseRole"] = row.get("database_role")
            dg_data["protectionMode"] = row.get("protection_mode")
            dg_data["openMode"] = row.get("open_mode")
            dg_data["forceLogging"] = row.get("force_logging")
            dg_data["flashbackOn"] = row.get("flashback_on")
            dg_data["dataguardBroker"] = row.get("dataguard_broker")
    else:
        for k in (
            "databaseRole",
            "protectionMode",
            "openMode",
            "forceLogging",
            "flashbackOn",
            "dataguardBroker",
        ):
            dg_data[k] = res_db

    q_dest = queries.get(
        "data_guard.remote_destinations_count",
        "SELECT COUNT(*) FROM v$archive_dest_status WHERE status != 'INACTIVE' AND type = 'REMOTE'",
    )
    res_dest = execute_query(cursor, q_dest)
    if isinstance(res_dest, tuple):
        _, rows = res_dest
        dg_data["remoteDestinationsCount"] = _safe_int(rows[0][0]) if rows else 0
    else:
        dg_data["remoteDestinationsCount"] = res_dest

    # 4. CDB / PDB
    cdb_data: dict[str, Any] = {
        "isCdb": None,
        "sessionConId": None,
        "sessionConName": None,
        "pdbCount": None,
        "openModes": {},
        "pdbs": [],
    }
    q_cdb = queries.get("cdb_pdb.cdb", "SELECT cdb FROM v$database")
    res_cdb = execute_query(cursor, q_cdb)
    if isinstance(res_cdb, tuple):
        _, rows = res_cdb
        cdb_data["isCdb"] = (str(rows[0][0]).upper() == "YES") if rows else False
    else:
        cdb_data["isCdb"] = res_cdb

    q_scon = queries.get(
        "cdb_pdb.session_con",
        "SELECT SYS_CONTEXT('USERENV', 'CON_ID') AS con_id, "
        "SYS_CONTEXT('USERENV', 'CON_NAME') AS con_name FROM dual",
    )
    res_scon = execute_query(cursor, q_scon)
    if isinstance(res_scon, tuple):
        cols, rows = res_scon
        if rows:
            row = dict(zip(cols, rows[0], strict=False))
            cid = row.get("con_id")
            cname = str(row.get("con_name") or "")
            cdb_data["sessionConId"] = int(cid) if cid is not None and str(cid).isdigit() else cid
            cdb_data["sessionConName"] = redactor.redact_pdb(cname)
    else:
        cdb_data["sessionConId"] = res_scon
        cdb_data["sessionConName"] = res_scon

    q_pdbc = queries.get("cdb_pdb.pdb_count", "SELECT COUNT(*) FROM v$pdbs")
    res_pdbc = execute_query(cursor, q_pdbc)
    if isinstance(res_pdbc, tuple):
        _, rows = res_pdbc
        cdb_data["pdbCount"] = _safe_int(rows[0][0]) if rows else 0
    else:
        cdb_data["pdbCount"] = res_pdbc

    q_pdbm = queries.get(
        "cdb_pdb.pdb_open_modes",
        "SELECT open_mode, COUNT(*) FROM v$pdbs GROUP BY open_mode",
    )
    res_pdbm = execute_query(cursor, q_pdbm)
    if isinstance(res_pdbm, tuple):
        _, rows = res_pdbm
        for r in rows:
            cdb_data["openModes"][str(r[0])] = int(r[1])
    else:
        cdb_data["openModes"] = res_pdbm

    q_pdbs = queries.get(
        "cdb_pdb.pdbs",
        "SELECT con_id, name, open_mode, restricted FROM v$pdbs ORDER BY con_id",
    )
    res_pdbs = execute_query(cursor, q_pdbs)
    if isinstance(res_pdbs, tuple):
        cols, rows = res_pdbs
        for r in rows:
            row = dict(zip(cols, r, strict=False))
            pname = str(row.get("name") or "")
            cdb_data["pdbs"].append(
                {
                    "conId": row.get("con_id"),
                    "name": redactor.redact_pdb(pname),
                    "openMode": row.get("open_mode"),
                    "restricted": row.get("restricted"),
                }
            )
    else:
        cdb_data["pdbs"] = res_pdbs

    # 5. TCPS / Network
    net_data: dict[str, Any] = {
        "networkProtocol": None,
        "authenticationMethod": None,
        "serviceBanners": [],
    }
    q_net = queries.get(
        "tcps_network.session_context",
        "SELECT SYS_CONTEXT('USERENV', 'NETWORK_PROTOCOL') AS network_protocol, "
        "SYS_CONTEXT('USERENV', 'AUTHENTICATION_METHOD') AS authentication_method FROM dual",
    )
    res_net = execute_query(cursor, q_net)
    if isinstance(res_net, tuple):
        cols, rows = res_net
        if rows:
            row = dict(zip(cols, rows[0], strict=False))
            net_data["networkProtocol"] = row.get("network_protocol")
            net_data["authenticationMethod"] = row.get("authentication_method")
    else:
        net_data["networkProtocol"] = res_net
        net_data["authenticationMethod"] = res_net

    q_sb = queries.get(
        "tcps_network.service_banners",
        "SELECT DISTINCT network_service_banner FROM v$session_connect_info "
        "WHERE sid = SYS_CONTEXT('USERENV', 'SID') AND network_service_banner IS NOT NULL "
        "ORDER BY network_service_banner",
    )
    res_sb = execute_query(cursor, q_sb)
    if isinstance(res_sb, tuple):
        _, rows = res_sb
        net_data["serviceBanners"] = [redactor.redact_text(str(r[0])) for r in rows]
    else:
        net_data["serviceBanners"] = res_sb

    # 6. Character Sets
    cs_data: dict[str, Any] = {
        "nlsCharacterset": None,
        "nlsNcharCharacterset": None,
    }
    q_cs = queries.get(
        "character_sets.parameters",
        "SELECT parameter, value FROM nls_database_parameters "
        "WHERE parameter IN ('NLS_CHARACTERSET', 'NLS_NCHAR_CHARACTERSET') ORDER BY parameter",
    )
    res_cs = execute_query(cursor, q_cs)
    if isinstance(res_cs, tuple):
        cols, rows = res_cs
        for r in rows:
            param = str(r[0]).upper()
            val = str(r[1])
            if param == "NLS_CHARACTERSET":
                cs_data["nlsCharacterset"] = val
            elif param == "NLS_NCHAR_CHARACTERSET":
                cs_data["nlsNcharCharacterset"] = val
    else:
        cs_data["nlsCharacterset"] = res_cs
        cs_data["nlsNcharCharacterset"] = res_cs

    # 7. Licensing-relevant flags without packs
    lic_data: dict[str, Any] = {
        "controlManagementPackAccess": None,
        "edition": None,
    }
    q_pack = queries.get(
        "licensing.pack_access",
        "SELECT value FROM v$parameter WHERE name = 'control_management_pack_access'",
    )
    res_pack = execute_query(cursor, q_pack)
    if isinstance(res_pack, tuple):
        _, rows = res_pack
        lic_data["controlManagementPackAccess"] = str(rows[0][0]) if rows else None
    else:
        lic_data["controlManagementPackAccess"] = res_pack

    q_ed = queries.get("licensing.edition", "SELECT edition FROM v$instance")
    res_ed = execute_query(cursor, q_ed)
    if isinstance(res_ed, tuple):
        _, rows = res_ed
        lic_data["edition"] = str(rows[0][0]) if rows else None
    else:
        lic_data["edition"] = res_ed

    # 8. Readiness
    readiness_data: dict[str, Any] = {}
    capabilities = [
        ("connect", queries.get("readiness.connect", "SELECT 1 FROM dual")),
        (
            "session_schema",
            queries.get(
                "readiness.session_schema",
                "SELECT COUNT(*) FROM user_objects WHERE ROWNUM <= 1",
            ),
        ),
        (
            "all_objects",
            queries.get(
                "readiness.all_objects",
                "SELECT COUNT(*) FROM all_objects WHERE ROWNUM <= 1",
            ),
        ),
        (
            "explain_plan",
            queries.get(
                "readiness.explain_plan",
                "SELECT COUNT(*) FROM plan_table WHERE ROWNUM <= 1",
            ),
        ),
        (
            "display_cursor",
            queries.get(
                "readiness.display_cursor",
                "SELECT COUNT(*) FROM v$sql_plan WHERE ROWNUM <= 1",
            ),
        ),
        (
            "v_session",
            queries.get(
                "readiness.v_session",
                "SELECT COUNT(*) FROM v$session WHERE ROWNUM <= 1",
            ),
        ),
        ("v_sql", queries.get("readiness.v_sql", "SELECT COUNT(*) FROM v$sql WHERE ROWNUM <= 1")),
        (
            "dba_tablespaces",
            queries.get(
                "readiness.dba_tablespaces",
                "SELECT COUNT(*) FROM dba_tablespace_usage_metrics WHERE ROWNUM <= 1",
            ),
        ),
        (
            "dba_scheduler_jobs",
            queries.get(
                "readiness.dba_scheduler_jobs",
                "SELECT COUNT(*) FROM dba_scheduler_jobs WHERE ROWNUM <= 1",
            ),
        ),
        (
            "dbms_stats",
            queries.get(
                "readiness.dbms_stats",
                "SELECT COUNT(*) FROM all_objects WHERE owner = 'SYS' "
                "AND object_name = 'DBMS_STATS' AND object_type = 'PACKAGE' AND ROWNUM <= 1",
            ),
        ),
        (
            "compile_objects",
            queries.get(
                "readiness.compile_objects",
                "SELECT COUNT(*) FROM all_errors WHERE ROWNUM <= 1",
            ),
        ),
    ]
    for cap_name, cap_sql in capabilities:
        readiness_data[cap_name] = check_capability(cursor, cap_sql)

    return {
        "target": target_label,
        "discoveredAt": now_iso,
        "showNames": redactor.show_names,
        "connection": {
            "driverMode": driver_mode,
            "user": user,
            "dsn": redactor.redact_dsn(dsn),
        },
        "version": version_data,
        "rac": rac_data,
        "dataGuard": dg_data,
        "cdbPdb": cdb_data,
        "network": net_data,
        "characterSets": cs_data,
        "licensing": lic_data,
        "readiness": readiness_data,
    }


def render_markdown(data: dict[str, Any]) -> str:
    """Render discovery report as GitHub-Flavored Markdown."""
    lines: list[str] = []
    target = data.get("target", "unknown")
    conn = data.get("connection", {})
    v = data.get("version", {})
    rac = data.get("rac", {})
    dg = data.get("dataGuard", {})
    cdb = data.get("cdbPdb", {})
    net = data.get("network", {})
    cs = data.get("characterSets", {})
    lic = data.get("licensing", {})
    ready = data.get("readiness", {})

    lines.append(f"# Fleet Discovery Report: `{target}`")
    lines.append("")
    lines.append("> [!IMPORTANT]")
    lines.append("> **Observation-Only Fleet Discovery (A8 / P5 Preparation)**")
    lines.append(
        "> Running fleet discovery on production is an authorized human DBA action; "
        "AI agents prepared the tool only."
    )
    lines.append(
        "> P5 remains OPEN until human DBAs execute this against production targets "
        "and vetted facts are recorded."
    )
    lines.append("")
    lines.append(f"- **Discovered At:** `{data.get('discoveredAt')}`")
    lines.append(f"- **DSN:** `{conn.get('dsn')}`")
    lines.append(f"- **User:** `{conn.get('user')}`")
    lines.append(f"- **Driver Mode:** `{conn.get('driverMode')}`")
    redact_desc = (
        "Opted-in (--show-names)" if data.get("showNames") else "Active (stable hashed labels)"
    )
    lines.append(f"- **Redaction:** `{redact_desc}`")
    lines.append("")

    # 1. Version / RU
    lines.append("## 1. Version and Release Update (RU) Level")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("| --- | --- |")
    lines.append(f"| Database Version | `{v.get('version')}` |")
    lines.append(f"| Full Version (RU) | `{v.get('versionFull')}` |")
    lines.append(f"| Edition | `{v.get('edition')}` |")
    patch = v.get("latestSqlpatch")
    if isinstance(patch, dict):
        patch_str = (
            f"Patch {patch.get('patchId')} ({patch.get('action')}: "
            f"{patch.get('status')}) - {patch.get('description')}"
        )
    else:
        patch_str = str(patch)
    lines.append(f"| Latest Applied Patch | `{patch_str}` |")
    lines.append(f"| Banner Full | `{v.get('bannerFull')}` |")
    lines.append("")

    # 2. RAC
    lines.append("## 2. RAC (Real Application Clusters)")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("| --- | --- |")
    lines.append(f"| RAC Cluster Active | `{rac.get('isRac')}` |")
    lines.append(f"| `CLUSTER_DATABASE` Parameter | `{rac.get('clusterDatabase')}` |")
    lines.append(f"| Instance Count (GV$INSTANCE) | `{rac.get('instanceCountGv')}` |")
    lines.append(f"| Instance Count (V$INSTANCE) | `{rac.get('instanceCountV')}` |")
    insts = rac.get("instances")
    if isinstance(insts, list):
        if insts:
            inst_summary = ", ".join(
                f"{i.get('instanceName')} (inst_id={i.get('instId')}, host={i.get('hostName')})"
                for i in insts
            )
        else:
            inst_summary = "<none>"
    else:
        inst_summary = str(insts)
    lines.append(f"| Instances | `{inst_summary}` |")
    lines.append("")

    # 3. Data Guard
    lines.append("## 3. Data Guard and Replication")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("| --- | --- |")
    lines.append(f"| Database Role | `{dg.get('databaseRole')}` |")
    lines.append(f"| Protection Mode | `{dg.get('protectionMode')}` |")
    lines.append(f"| Open Mode | `{dg.get('openMode')}` |")
    lines.append(f"| Force Logging | `{dg.get('forceLogging')}` |")
    lines.append(f"| Flashback On | `{dg.get('flashbackOn')}` |")
    lines.append(f"| Data Guard Broker | `{dg.get('dataguardBroker')}` |")
    lines.append(f"| Remote Archive Destinations | `{dg.get('remoteDestinationsCount')}` |")
    lines.append("")

    # 4. CDB / PDB
    lines.append("## 4. Container & Pluggable Database (CDB / PDB)")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("| --- | --- |")
    lines.append(f"| Is CDB | `{cdb.get('isCdb')}` |")
    lines.append(f"| Session Container ID (`CON_ID`) | `{cdb.get('sessionConId')}` |")
    lines.append(f"| Session Container Name (`CON_NAME`) | `{cdb.get('sessionConName')}` |")
    lines.append(f"| PDB Count | `{cdb.get('pdbCount')}` |")
    modes = cdb.get("openModes")
    modes_str = (
        ", ".join(f"{k}: {v}" for k, v in modes.items()) if isinstance(modes, dict) else str(modes)
    )
    lines.append(f"| PDB Open Modes | `{modes_str or '<none>'}` |")
    pdbs = cdb.get("pdbs")
    if isinstance(pdbs, list):
        if pdbs:
            pdbs_str = "; ".join(
                f"{p.get('name')} (con_id={p.get('conId')}, {p.get('openMode')})" for p in pdbs
            )
        else:
            pdbs_str = "<none>"
    else:
        pdbs_str = str(pdbs)
    lines.append(f"| Pluggable Databases | `{pdbs_str}` |")
    lines.append("")

    # 5. TCPS / Network
    lines.append("## 5. Network & Encryption (TCPS)")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("| --- | --- |")
    lines.append(f"| Network Protocol | `{net.get('networkProtocol')}` |")
    lines.append(f"| Authentication Method | `{net.get('authenticationMethod')}` |")
    banners = net.get("serviceBanners")
    banners_str = (
        " <br> ".join(banners)
        if isinstance(banners, list) and banners
        else str(banners or "<none>")
    )
    lines.append(f"| Network Service Banners | {banners_str} |")
    lines.append("")

    # 6. Character Sets
    lines.append("## 6. Database Character Sets")
    lines.append("")
    lines.append("| Parameter | Value |")
    lines.append("| --- | --- |")
    lines.append(f"| `NLS_CHARACTERSET` | `{cs.get('nlsCharacterset')}` |")
    lines.append(f"| `NLS_NCHAR_CHARACTERSET` | `{cs.get('nlsNcharCharacterset')}` |")
    lines.append("")

    # 7. Licensing & Packs
    lines.append("## 7. Licensing Flags (No Pack Features Used)")
    lines.append("")
    lines.append("| Parameter / Feature | Value |")
    lines.append("| --- | --- |")
    lines.append(
        f"| `CONTROL_MANAGEMENT_PACK_ACCESS` | `{lic.get('controlManagementPackAccess')}` |"
    )
    lines.append(f"| Database Edition | `{lic.get('edition')}` |")
    lines.append("")

    # 8. Readiness
    lines.append("## 8. Harness Readiness Probes")
    lines.append("")
    lines.append("| Capability | Status | Detail |")
    lines.append("| --- | --- | --- |")
    if isinstance(ready, dict):
        for cap, status in ready.items():
            avail = "AVAILABLE" if status.get("available") else "UNAVAILABLE"
            detail = status.get("detail", "")
            lines.append(f"| `{cap}` | **{avail}** | {detail} |")
    lines.append("")

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Observation-only fleet discovery runner (A8 / P5 preparation)."
    )
    parser.add_argument("--dsn", help="Oracle DSN (e.g. host:1521/service)")
    parser.add_argument("--user", help="Oracle username")
    parser.add_argument("--password-file", help="Path to file containing database password")
    parser.add_argument(
        "--thick",
        action="store_true",
        help="Opt in to thick driver mode (thin mode is default)",
    )
    parser.add_argument(
        "--show-names",
        action="store_true",
        help="Opt in to show real host, IP, DB, instance, and PDB names (unredacted)",
    )
    parser.add_argument(
        "--targets",
        help="Path to targets.json list of {label, dsn, user, passwordFile}",
    )
    parser.add_argument(
        "--out-dir",
        default="./fleet_discovery_reports",
        help="Output directory for JSON and Markdown reports",
    )
    parser.add_argument(
        "--sql-file",
        help="Path to fleet_discovery.sql (defaults to oracle/discovery/fleet_discovery.sql)",
    )

    args = parser.parse_args(argv)

    repo_root = Path(__file__).resolve().parent.parent
    default_sql = repo_root / "oracle" / "discovery" / "fleet_discovery.sql"
    sql_path = Path(args.sql_file) if args.sql_file else default_sql

    try:
        queries = load_queries_from_sql(sql_path)
    except Exception as exc:  # noqa: BLE001
        print(f"Error loading SQL file from {sql_path}: {exc}", file=sys.stderr)
        return 2

    targets_list: list[dict[str, Any]] = []
    if args.targets:
        targets_file = Path(args.targets)
        if not targets_file.is_file():
            print(f"Targets file not found: {args.targets}", file=sys.stderr)
            return 2
        try:
            loaded = json.loads(targets_file.read_text(encoding="utf-8"))
            if not isinstance(loaded, list):
                print(f"Targets file {args.targets} must contain a JSON array", file=sys.stderr)
                return 2
            targets_list = loaded
        except Exception as exc:  # noqa: BLE001
            print(f"Failed to parse targets file {args.targets}: {exc}", file=sys.stderr)
            return 2
    elif args.dsn and args.user:
        targets_list.append(
            {
                "label": "target",
                "dsn": args.dsn,
                "user": args.user,
                "passwordFile": args.password_file,
            }
        )
    else:
        print(
            "Either --targets <targets.json> or both --dsn and --user must be specified.",
            file=sys.stderr,
        )
        return 2

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    redactor = Redactor(show_names=args.show_names)
    driver_mode = "thick" if args.thick else "thin"

    if oracledb is None:
        print(
            "python-oracledb is required to connect to Oracle targets. "
            "Run with 'uv run --extra oracle ...' or install python-oracledb.",
            file=sys.stderr,
        )
        return 1

    if args.thick:
        try:
            oracledb.init_oracle_client()
        except Exception as exc:  # noqa: BLE001
            print(f"Failed to initialize Oracle Client for thick mode: {exc}", file=sys.stderr)
            return 1

    overall_success = True
    summaries: list[dict[str, Any]] = []

    for target_spec in targets_list:
        label = target_spec.get("label", "target")
        dsn = target_spec.get("dsn", "")
        user = target_spec.get("user", "")
        pw_file = target_spec.get("passwordFile")

        password: str | None = None
        if pw_file:
            p_path = Path(pw_file)
            if p_path.is_file():
                password = p_path.read_text(encoding="utf-8").strip()
            else:
                print(f"Password file not found for target {label}: {pw_file}", file=sys.stderr)
                overall_success = False
                continue

        try:
            conn = oracledb.connect(user=user, password=password, dsn=dsn)
        except Exception as exc:  # noqa: BLE001
            red_dsn = redactor.redact_dsn(dsn)
            print(f"Connection failed for target '{label}' ({red_dsn}): {exc}", file=sys.stderr)
            overall_success = False
            continue

        try:
            with conn.cursor() as cur:
                report = discover_target(
                    cursor=cur,
                    redactor=redactor,
                    queries=queries,
                    dsn=dsn,
                    user=user,
                    driver_mode=driver_mode,
                    target_label=label,
                )
        finally:
            conn.close()

        summaries.append(report)

        # Write per-target JSON and Markdown
        target_json_path = out_dir / f"{label}-discovery.json"
        target_md_path = out_dir / f"{label}-discovery.md"
        target_json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        target_md_path.write_text(render_markdown(report), encoding="utf-8")
        print(
            f"Target '{label}' discovery complete: {target_json_path.name}, {target_md_path.name}"
        )

    if len(summaries) > 1:
        fleet_json = out_dir / "fleet_summary.json"
        fleet_md = out_dir / "fleet_summary.md"
        fleet_json.write_text(json.dumps(summaries, indent=2), encoding="utf-8")
        combined_md = "\n\n---\n\n".join(render_markdown(s) for s in summaries)
        fleet_md.write_text(combined_md, encoding="utf-8")
        print(f"Fleet summary written to {fleet_json.name}, {fleet_md.name}")

    return 0 if overall_success else 1


if __name__ == "__main__":
    sys.exit(main())
