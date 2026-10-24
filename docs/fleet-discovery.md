# Observation-Only Fleet Discovery Runbook (A8 / P5 Preparation)

> [!IMPORTANT]
> **Production Execution is a Human DBA Action**
> Running fleet discovery on production is an authorized human DBA action; AI agents prepared the tool and runbook only (AI agent, not a human).
> AI agents have not run, will not run, and must not run these scripts against production databases.
> Task **P5** in `PLAN.md` remains **OPEN** until a human DBA runs this procedure against real production targets and vetted output is recorded.

---

## 1. Purpose and Scope

This runbook guides the DBA in gathering fleet topology and environment facts required to unblock shape-dependent decisions in `PLAN.md` (P5, Section 5 "Decisions still needed").

The discovery tool operates under strict non-interference rules:
1. **STRICTLY Observation-Only**: SELECT queries against data dictionary and dynamic performance views only. No DDL, no DML, no PL/SQL that modifies state, and no session modifications beyond formatting.
2. **ZERO Pack-Licensed Features**: Strictly excludes Oracle Diagnostics Pack and Tuning Pack views and packages (`DBA_HIST_*`, `V$ACTIVE_SESSION_HISTORY` / `ASH`, `V$SQL_MONITOR`, `DBMS_SQL_MONITOR`, `DBMS_SQLTUNE`, `DBMS_WORKLOAD_*`, `DBMS_ADDM`, `DBA_ADVISOR_*`). Entitlement to management packs is never assumed or exercised.
3. **Graceful Privilege Tolerance**: Missing dictionary view grants raise `ORA-00942` or `ORA-01031`, which are trapped and reported as `"not visible to this account"` rather than terminating the run.
4. **Deterministic Redaction by Default**: All hostnames, IP addresses, database names, instance names, PDB names, and service names are automatically replaced with stable, deterministic hashed identifiers (e.g. `host_a1b2c3d4`, `db_e3b0c442`) so that output artifacts can be shared safely.

---

## 2. Prerequisites and Grants

### 2.1 Database Account

The discovery script should be run using a dedicated read-only database user (e.g., `HARNESS_RO`).

Prefer the reviewed role defined in [`oracle/grants/harness_roles.sql`](oracle/grants/harness_roles.sql):
- `harness_diagnostics_role`

### 2.2 Optional Additional View Grants

`harness_diagnostics_role` grants read access to the minimal set of views used by shipped application panels (`V$SESSION`, `V$SQL`, `V$SQL_PLAN`, `DBA_TABLESPACES`, `DBA_TABLESPACE_USAGE_METRICS`, `DBA_SCHEDULER_JOBS`, `DBA_SCHEDULER_JOB_RUN_DETAILS`).

To enable complete discovery visibility across RAC, Data Guard, CDB, and patch history without receiving `"not visible to this account"`, a DBA can grant SELECT on the following views:

```sql
-- Run as SYSDBA on the target database:
GRANT SELECT ON sys.v_$database               TO harness_ro;
GRANT SELECT ON sys.v_$instance               TO harness_ro;
GRANT SELECT ON sys.gv_$instance              TO harness_ro;
GRANT SELECT ON sys.v_$pdbs                   TO harness_ro;
GRANT SELECT ON sys.v_$archive_dest_status    TO harness_ro;
GRANT SELECT ON sys.v_$parameter              TO harness_ro;
GRANT SELECT ON sys.v_$session_connect_info   TO harness_ro;
GRANT SELECT ON sys.dba_registry_sqlpatch     TO harness_ro;
```

*Alternative:* Granting `SELECT_CATALOG_ROLE` provides read access to all dictionary views.

*Note:* If the DBA chooses not to grant any of these additional views, the discovery script will still run to completion without error, reporting `"not visible to this account"` for the ungranted sections.

---

## 3. How to Run Discovery

### 3.1 Option A: Interactive SQL\*Plus / SQLcl Execution

DBAs connecting directly via SQL\*Plus or SQLcl can execute the standalone SQL script [`oracle/discovery/fleet_discovery.sql`](oracle/discovery/fleet_discovery.sql):

```sh
sqlplus harness_ro/<password>@db-host.example.com:1521/sales_service @oracle/discovery/fleet_discovery.sql
```

The script outputs structured, labelled rows:
```text
==============================================================================
Fleet Discovery (Observation-Only, P5 Preparation)
==============================================================================
SECTION            | KEY                            | VALUE
-------------------+--------------------------------+-------------------------
VERSION            | version                        | 19.0.0.0.0
VERSION            | version_full                   | 19.9.0.0.0
VERSION            | banner_full                    | Oracle Database 19c Enterprise Edition ...
VERSION            | latest_sqlpatch                | 31281373 (APPLY : SUCCESS) - Database Release Update : 19.9.0.0.201020
RAC                | instance_count_gv              | 2
RAC                | cluster_database               | TRUE
RAC                | instances                      | [REDACTED: pass SHOW_NAMES to display names]
DATA_GUARD         | database_role                  | PRIMARY
DATA_GUARD         | remote_destinations_count      | 1
CDB_PDB            | cdb                            | YES
...
```

To display real instance and PDB names instead of redacted placeholders:
```sh
sqlplus harness_ro/<password>@db-host.example.com:1521/sales_service @oracle/discovery/fleet_discovery.sql SHOW_NAMES
```

### 3.2 Option B: Automated Python Runner

The Python runner [`scripts/fleet_discovery.py`](scripts/fleet_discovery.py) (and wrapper [`scripts/fleet_discovery.sh`](scripts/fleet_discovery.sh)) connects with `python-oracledb`, executes the queries, and writes machine-readable JSON and human-readable Markdown reports to an output directory.

#### Single Target Execution

Store the target password in a secure, mode 0600 file (never in shell arguments or environment variables):

```sh
echo "<password>" > /tmp/target_pw.txt
chmod 600 /tmp/target_pw.txt

./scripts/fleet_discovery.sh \
  --dsn "db-host.example.com:1521/sales_service" \
  --user "harness_ro" \
  --password-file /tmp/target_pw.txt \
  --out-dir ./fleet_reports

rm -f /tmp/target_pw.txt
```

#### Multi-Target Fleet Execution

To discover an entire fleet in a single run, prepare a `targets.json` manifest:

```json
[
  {
    "label": "core-oltp",
    "dsn": "rac-cluster.example.com:1521/core_service",
    "user": "harness_ro",
    "passwordFile": "/secure/secrets/core_pw.txt"
  },
  {
    "label": "dw-reporting",
    "dsn": "dw-standby.example.com:1521/dw_service",
    "user": "harness_ro",
    "passwordFile": "/secure/secrets/dw_pw.txt"
  }
]
```

Run discovery across all targets:
```sh
./scripts/fleet_discovery.sh \
  --targets targets.json \
  --out-dir ./fleet_reports
```

This generates:
- `<label>-discovery.json` and `<label>-discovery.md` for each target.
- `fleet_summary.json` and `fleet_summary.md` consolidating all targets.

#### CLI Flags
- `--thick`: Opt-in to Oracle thick driver mode (`init_oracle_client`) if required by specific network drivers or advanced TCPS setups. Default is thin mode.
- `--show-names`: Opt-in to show unredacted hostnames, IPs, DB names, instance names, and PDB names (for internal DBA analysis only).

---

## 4. What Each Section Means for PLAN.md

The discovery output directly resolves pending architectural decisions in `PLAN.md`:

| Discovery Section | Queries Used | Plan Decisions & Diagnostics Unblocked |
| --- | --- | --- |
| **1. Version & RU Level** | `V$VERSION`, `V$INSTANCE.version_full`, `DBA_REGISTRY_SQLPATCH` | Unblocks **RU-level compatibility**. Verifies baseline 19c RU (19.9 vs 19.16+), JSON and vector capabilities, SQL optimizer quirks, and installed RU patch level. |
| **2. RAC Topology** | `GV$INSTANCE`, `V$INSTANCE`, `V$PARAMETER (cluster_database)` | Unblocks **shape-dependent session and blocker diagnostics** (P5). Single-instance databases query `V$SESSION`; RAC clusters require querying `GV$SESSION` and pairing `INST_ID` + `SID` for cross-instance blocking chains in `oracle/diagnostics/dba/blocking.sql` and `sessions.sql`. |
| **3. Data Guard** | `V$DATABASE (database_role, protection_mode, open_mode)`, `V$ARCHIVE_DEST_STATUS` | Unblocks **standby worksheet and diagnostic routing**. Confirms whether a target is PRIMARY or PHYSICAL STANDBY (Active Data Guard), unblocking read-only query routing and standby replication lag visibility. |
| **4. CDB / PDB Architecture** | `V$DATABASE.cdb`, `V$PDBS`, `SYS_CONTEXT('USERENV')` | Unblocks **container session context**. Identifies whether connections land in CDB root or a specific PDB, determining whether `ALTER SESSION SET CONTAINER` is permitted/needed, and scoping object metadata lookups. |
| **5. TCPS & Network Encryption** | `SYS_CONTEXT('USERENV','NETWORK_PROTOCOL')`, `V$SESSION_CONNECT_INFO` | Unblocks **TCPS and wallet configuration requirements** (P6, R5 transport). Confirms whether TLS/TCPS or Native Network Encryption (NNE) is active, verifying encryption and checksumming service adapters. |
| **6. Database Character Sets** | `NLS_DATABASE_PARAMETERS (NLS_CHARACTERSET, NLS_NCHAR_CHARACTERSET)` | Unblocks **multibyte character set handling**. Differentiates `AL32UTF8` from single-byte character sets (e.g. `WE8MSWIN1252`), establishing bind buffer sizes and preview truncation boundaries. |
| **7. Licensing Flags** | `V$PARAMETER (control_management_pack_access)`, `V$INSTANCE.edition` | Unblocks **compliance verification**. Verifies `CONTROL_MANAGEMENT_PACK_ACCESS` is `NONE` (or documents existing pack settings) to ensure the harness never triggers unentitled pack usage. Confirms Enterprise Edition vs Standard Edition. |
| **8. Harness Readiness** | Probes on 11 diagnostic views and packages | Unblocks **account capability baseline**. Tests read access to `V$SESSION`, `V$SQL`, `V$SQL_PLAN`, `DBA_TABLESPACES`, `DBA_SCHEDULER_JOBS`, `DBMS_STATS`, `ALL_ERRORS`, determining exactly which UI panels will be fully functional vs degraded. |

---

## 5. Sharing Results Safely (Zero Information Leakage)

To enable engineering and planning alignment without leaking sensitive infrastructure details:

1. **Keep Redaction Enabled**:
   Do NOT use `--show-names` when generating reports intended for sharing or attaching to change requests.
   The default redaction hashes:
   - Hostnames: `host_a1b2c3d4`
   - IP Addresses: `ip_87654321`
   - Database Names: `db_c2f7881c`
   - Instance Names: `inst_79ab430e`
   - PDB Names: `pdb_99a8b7c6`
   - Service Names: `svc_12345678`
2. **Deterministic Integrity**:
   Because the hashes are deterministic, relationships across instances in the same RAC cluster or services pointing to the same database remain completely clear in the report while masking the actual production strings.
3. **Verify with Secret Scan**:
   Before committing or sharing any discovery artifact, run the repository secret scan:
   ```sh
   ./scripts/secret_scan.sh
   ```
4. **Recording Findings**:
   Once vetted by the human DBA, fleet topology facts can be recorded in `docs/compatibility.md` and the corresponding P5 decisions in `PLAN.md` can be updated.
