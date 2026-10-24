"""Helper logic for metadata store upgrade drills.

This module provides CLI actions and importable functions to:
1. Seed representative records into a metadata store using available models/APIs.
2. Snapshot store row counts and identifying values to JSON.
3. Verify store schema version and data retention across migrations.
4. Verify idempotency of subsequent upgrade runs.
"""

from __future__ import annotations

import argparse
import json
import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, inspect, select, text
from sqlalchemy.orm import Session

from harness_api.db import initialize_schema
from harness_api.models import SchemaVersion

log = logging.getLogger("harness.upgrade_drill")


def utcnow() -> datetime:
    return datetime.now(UTC)


def get_schema_version(engine: Engine) -> str | None:
    """Read the current schema version from the store, if recorded."""
    inspector = inspect(engine)
    if not inspector.has_table("schema_version"):
        return None
    with Session(engine) as session:
        row = session.scalars(select(SchemaVersion).limit(1)).first()
        return None if row is None else row.version


def seed_representative_data(engine: Engine, run_demo_seed: bool = True) -> None:
    """Seed demo data and representative records into the metadata store.

    Uses harness_api.seed if possible, and adds representative execution,
    worksheet session, saved script, and audit event records if not present.
    Uses parameterized SQL so it works across older schema versions.
    """
    if run_demo_seed:
        try:
            from harness_api.seed import seed

            seed(probe=False)
        except Exception as exc:  # noqa: BLE001
            log.warning("Demo seed did not run or was skipped: %s", exc)

    now = utcnow()
    with engine.begin() as conn:
        # 1. Ensure secret reference exists
        row = conn.execute(
            text("SELECT id FROM secret_references WHERE name = 'harness-app'")
        ).fetchone()
        if row is None:
            conn.execute(
                text(
                    "INSERT INTO secret_references (id, name, provider, locator, "
                    "description, created_at) VALUES "
                    "(:id, :name, :provider, :locator, :description, :created_at)"
                ),
                {
                    "id": "sec_drill_1",
                    "name": "harness-app",
                    "provider": "file",
                    "locator": "harness_app.password",
                    "description": "Oracle account used by demonstration targets.",
                    "created_at": now,
                },
            )
            sec_id = "sec_drill_1"
        else:
            sec_id = row[0]

        # 2. Ensure developer user exists
        row = conn.execute(
            text("SELECT id FROM users WHERE subject = 'dev@example.internal'")
        ).fetchone()
        if row is None:
            roles_val = (
                json.dumps(["developer"]) if engine.dialect.name == "sqlite" else ["developer"]
            )
            conn.execute(
                text(
                    "INSERT INTO users (id, subject, display_name, email, roles, "
                    "disabled, created_at) VALUES "
                    "(:id, :subject, :display_name, :email, :roles, :disabled, :created_at)"
                ),
                {
                    "id": "usr_drill_dev",
                    "subject": "dev@example.internal",
                    "display_name": "Dev Developer",
                    "email": "dev@example.internal",
                    "roles": roles_val,
                    "disabled": False,
                    "created_at": now,
                },
            )
            dev_user_id = "usr_drill_dev"
        else:
            dev_user_id = row[0]

        # 3. Ensure development connection profile exists
        row = conn.execute(
            text("SELECT id FROM connection_profiles WHERE name = 'development'")
        ).fetchone()
        if row is None:
            conn.execute(
                text(
                    "INSERT INTO connection_profiles (id, name, environment, host, "
                    "port, service_name, username, default_schema, protocol, "
                    "wallet_dir, secret_reference_id, worksheets_enabled, "
                    "mutating_runbooks_enabled, notes, created_at) VALUES "
                    "(:id, :name, :environment, :host, :port, :service_name, "
                    ":username, :default_schema, :protocol, :wallet_dir, "
                    ":secret_reference_id, :worksheets_enabled, "
                    ":mutating_runbooks_enabled, :notes, :created_at)"
                ),
                {
                    "id": "tgt_drill_dev",
                    "name": "development",
                    "environment": "development",
                    "host": "localhost",
                    "port": 1521,
                    "service_name": "DEVPDB1",
                    "username": "harness_app",
                    "default_schema": "HARNESS_APP",
                    "protocol": "tcp",
                    "wallet_dir": "",
                    "secret_reference_id": sec_id,
                    "worksheets_enabled": True,
                    "mutating_runbooks_enabled": True,
                    "notes": "Drill development profile.",
                    "created_at": now,
                },
            )
            dev_profile_id = "tgt_drill_dev"
        else:
            dev_profile_id = row[0]

        # 4. Ensure grant exists
        row = conn.execute(
            text("SELECT id FROM user_target_grants WHERE user_id = :u AND profile_id = :p"),
            {"u": dev_user_id, "p": dev_profile_id},
        ).fetchone()
        if row is None:
            perms_val = (
                json.dumps(["read", "worksheet", "compile"])
                if engine.dialect.name == "sqlite"
                else ["read", "worksheet", "compile"]
            )
            conn.execute(
                text(
                    "INSERT INTO user_target_grants (id, user_id, profile_id, "
                    "permissions, granted_by, created_at) VALUES "
                    "(:id, :user_id, :profile_id, :permissions, :granted_by, :created_at)"
                ),
                {
                    "id": "grt_drill_1",
                    "user_id": dev_user_id,
                    "profile_id": dev_profile_id,
                    "permissions": perms_val,
                    "granted_by": "",
                    "created_at": now,
                },
            )

        # 5. Ensure execution record exists
        row = conn.execute(text("SELECT id FROM executions WHERE id = 'exe_drill_1'")).fetchone()
        if row is None:
            inspector = inspect(conn)
            exec_cols = {c["name"] for c in inspector.get_columns("executions")}
            has_digest = "request_digest" in exec_cols
            has_owner = "owner_id" in exec_cols

            extra_cols = []
            extra_vals = []
            if has_digest:
                extra_cols.append("request_digest")
                extra_vals.append(":request_digest")
            if has_owner:
                extra_cols.append("owner_id")
                extra_vals.append(":owner_id")

            digest_col_str = (", " + ", ".join(extra_cols)) if extra_cols else ""
            digest_val_str = (", " + ", ".join(extra_vals)) if extra_vals else ""

            empty_list = json.dumps([]) if engine.dialect.name == "sqlite" else []
            empty_dict = json.dumps({}) if engine.dialect.name == "sqlite" else {}

            params: dict[str, Any] = {
                "id": "exe_drill_1",
                "user_id": dev_user_id,
                "profile_id": dev_profile_id,
                "operation_id": "worksheet.execute",
                "statement_kind": "query",
                "risk_class": "read",
                "statement_fingerprint": "a" * 64,
                "bind_names": empty_list,
                "limits_json": empty_dict,
                "policy_decision": "allowed",
                "policy_reason": "",
                "state": "succeeded",
                "truncated": False,
                "error_code": "",
                "error_message": "",
                "verification_json": empty_dict,
                "dedup_key": "drill-key-1",
                "started_at": now,
            }
            if has_digest:
                params["request_digest"] = "d" * 64
            if has_owner:
                params["owner_id"] = ""

            conn.execute(
                text(
                    f"INSERT INTO executions (id, user_id, profile_id, operation_id, "  # noqa: S608
                    f"statement_kind, risk_class, statement_fingerprint, bind_names, "
                    f"limits_json, policy_decision, policy_reason, state, truncated, "
                    f"error_code, error_message, verification_json, dedup_key, "
                    f"started_at{digest_col_str}) VALUES ("
                    f":id, :user_id, :profile_id, :operation_id, :statement_kind, "
                    f":risk_class, :statement_fingerprint, :bind_names, :limits_json, "
                    f":policy_decision, :policy_reason, :state, :truncated, :error_code, "
                    f":error_message, :verification_json, :dedup_key, :started_at{digest_val_str})"
                ),
                params,
            )

        # 6. Ensure worksheet session record exists
        row = conn.execute(
            text("SELECT id FROM worksheet_sessions WHERE id = 'wss_drill_1'")
        ).fetchone()
        if row is None:
            inspector = inspect(conn)
            wss_cols = {c["name"] for c in inspector.get_columns("worksheet_sessions")}
            has_wss_owner = "owner_id" in wss_cols

            wss_col_str = ", owner_id" if has_wss_owner else ""
            wss_val_str = ", :owner_id" if has_wss_owner else ""

            params = {
                "id": "wss_drill_1",
                "user_id": dev_user_id,
                "profile_id": dev_profile_id,
                "opened_at": now,
                "close_reason": "",
            }
            if has_wss_owner:
                params["owner_id"] = ""

            conn.execute(
                text(
                    f"INSERT INTO worksheet_sessions (id, user_id, profile_id, "  # noqa: S608
                    f"opened_at, close_reason{wss_col_str}) VALUES "
                    f"(:id, :user_id, :profile_id, :opened_at, :close_reason{wss_val_str})"
                ),
                params,
            )

        # 7. Ensure saved script exists
        row = conn.execute(text("SELECT id FROM saved_scripts WHERE id = 'scr_drill_1'")).fetchone()
        if row is None:
            conn.execute(
                text(
                    "INSERT INTO saved_scripts (id, user_id, profile_id, name, "
                    "body, created_at, updated_at) VALUES "
                    "(:id, :user_id, :profile_id, :name, :body, :created_at, :updated_at)"
                ),
                {
                    "id": "scr_drill_1",
                    "user_id": dev_user_id,
                    "profile_id": dev_profile_id,
                    "name": "drill_saved_script",
                    "body": "SELECT 1 FROM DUAL;",
                    "created_at": now,
                    "updated_at": now,
                },
            )

        # 8. Ensure audit event exists
        row = conn.execute(text("SELECT id FROM audit_events WHERE id = 'aud_drill_1'")).fetchone()
        if row is None:
            empty_dict = json.dumps({}) if engine.dialect.name == "sqlite" else {}
            conn.execute(
                text(
                    "INSERT INTO audit_events (id, created_at, actor_id, actor_subject, "
                    "profile_id, operation_id, execution_id, risk_class, policy_decision, "
                    "outcome, statement_fingerprint, affected_counts, detail) VALUES "
                    "(:id, :created_at, :actor_id, :actor_subject, :profile_id, "
                    ":operation_id, :execution_id, :risk_class, :policy_decision, "
                    ":outcome, :statement_fingerprint, :affected_counts, :detail)"
                ),
                {
                    "id": "aud_drill_1",
                    "created_at": now,
                    "actor_id": dev_user_id,
                    "actor_subject": "dev@example.internal",
                    "profile_id": dev_profile_id,
                    "operation_id": "worksheet.execute",
                    "execution_id": "exe_drill_1",
                    "risk_class": "read",
                    "policy_decision": "allowed",
                    "outcome": "succeeded",
                    "statement_fingerprint": "a" * 64,
                    "affected_counts": empty_dict,
                    "detail": empty_dict,
                },
            )


def _normalize_json_field(val: Any) -> Any:
    """Normalize JSON fields that might be string or Python collections."""
    if isinstance(val, str):
        try:
            parsed = json.loads(val)
            if isinstance(parsed, list):
                return sorted(parsed)
            return parsed
        except Exception:  # noqa: BLE001
            return val
    elif isinstance(val, list):
        return sorted(val)
    return val


def take_snapshot(engine: Engine) -> dict[str, Any]:
    """Capture row counts and identifying values for all tables in the store."""
    version = get_schema_version(engine)
    table_counts: dict[str, int] = {}

    with engine.connect() as conn:
        if engine.dialect.name == "sqlite":
            table_query = text(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%' "
                "ORDER BY name;"
            )
        else:
            table_query = text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_type = 'BASE TABLE' "
                "ORDER BY table_name;"
            )
        tables = [row[0] for row in conn.execute(table_query).fetchall()]

        for tbl in tables:
            cnt = conn.execute(text(f'SELECT count(*) FROM "{tbl}"')).scalar()  # noqa: S608
            table_counts[tbl] = int(cnt or 0)

        # Read identifying records from core tables
        records: dict[str, list[dict[str, Any]]] = {}

        if "users" in tables:
            rows = conn.execute(
                text("SELECT id, subject, display_name, roles FROM users ORDER BY id")
            ).fetchall()
            records["users"] = [
                {
                    "id": r[0],
                    "subject": r[1],
                    "display_name": r[2],
                    "roles": _normalize_json_field(r[3]),
                }
                for r in rows
            ]

        if "connection_profiles" in tables:
            rows = conn.execute(
                text(
                    "SELECT id, name, environment, worksheets_enabled "
                    "FROM connection_profiles ORDER BY id"
                )
            ).fetchall()
            records["connection_profiles"] = [
                {
                    "id": r[0],
                    "name": r[1],
                    "environment": r[2],
                    "worksheets_enabled": bool(r[3]),
                }
                for r in rows
            ]

        if "user_target_grants" in tables:
            rows = conn.execute(
                text(
                    "SELECT user_id, profile_id, permissions "
                    "FROM user_target_grants ORDER BY user_id, profile_id"
                )
            ).fetchall()
            records["user_target_grants"] = [
                {
                    "user_id": r[0],
                    "profile_id": r[1],
                    "permissions": _normalize_json_field(r[2]),
                }
                for r in rows
            ]

        if "executions" in tables:
            rows = conn.execute(
                text(
                    "SELECT id, user_id, profile_id, operation_id, state "
                    "FROM executions ORDER BY id"
                )
            ).fetchall()
            records["executions"] = [
                {
                    "id": r[0],
                    "user_id": r[1],
                    "profile_id": r[2],
                    "operation_id": r[3],
                    "state": r[4],
                }
                for r in rows
            ]

        if "worksheet_sessions" in tables:
            rows = conn.execute(
                text("SELECT id, user_id, profile_id FROM worksheet_sessions ORDER BY id")
            ).fetchall()
            records["worksheet_sessions"] = [
                {"id": r[0], "user_id": r[1], "profile_id": r[2]} for r in rows
            ]

        if "saved_scripts" in tables:
            rows = conn.execute(
                text("SELECT id, user_id, name FROM saved_scripts ORDER BY id")
            ).fetchall()
            records["saved_scripts"] = [{"id": r[0], "user_id": r[1], "name": r[2]} for r in rows]

        if "audit_events" in tables:
            rows = conn.execute(
                text(
                    "SELECT id, actor_subject, operation_id, outcome FROM audit_events ORDER BY id"
                )
            ).fetchall()
            records["audit_events"] = [
                {
                    "id": r[0],
                    "actor_subject": r[1],
                    "operation_id": r[2],
                    "outcome": r[3],
                }
                for r in rows
            ]

        if "secret_references" in tables:
            rows = conn.execute(
                text("SELECT id, name, provider, locator FROM secret_references ORDER BY id")
            ).fetchall()
            records["secret_references"] = [
                {"id": r[0], "name": r[1], "provider": r[2], "locator": r[3]} for r in rows
            ]

        if "runbook_definitions" in tables:
            rows = conn.execute(
                text("SELECT id, title FROM runbook_definitions ORDER BY id")
            ).fetchall()
            records["runbook_definitions"] = [{"id": r[0], "title": r[1]} for r in rows]

    return {
        "schema_version": version,
        "table_counts": table_counts,
        "records": records,
    }


def upgrade_schema(engine: Engine) -> str:
    """Run initialize_schema using current application models and migrations."""
    return initialize_schema(engine)


def verify_upgrade(
    engine: Engine,
    snapshot: dict[str, Any],
    expected_version: str = "5",
) -> None:
    """Verify that an upgraded database retained all records and matches schema version 5."""
    # 1. Verify schema version
    current_version = get_schema_version(engine)
    if current_version != expected_version:
        raise AssertionError(
            f"Schema version mismatch: expected {expected_version}, got {current_version}"
        )

    # 2. Verify all pre-upgrade tables still exist and have expected counts
    inspector = inspect(engine)
    current_tables = set(inspector.get_table_names())
    snapshot_tables = snapshot.get("table_counts", {})

    for tbl, expected_count in snapshot_tables.items():
        if tbl not in current_tables:
            raise AssertionError(f"Pre-upgrade table '{tbl}' is missing after upgrade!")
        with engine.connect() as conn:
            actual_count = int(
                conn.execute(text(f'SELECT count(*) FROM "{tbl}"')).scalar() or 0  # noqa: S608
            )
        # Pre-upgrade core tables must have exact same row count
        if actual_count != expected_count:
            raise AssertionError(
                f"Row count mismatch in '{tbl}': expected {expected_count}, got {actual_count}"
            )

    # 3. Verify identifying values for every pre-upgrade record
    snapshot_records = snapshot.get("records", {})
    with engine.connect() as conn:
        for tbl, expected_rows in snapshot_records.items():
            if not expected_rows:
                continue
            cols = list(expected_rows[0].keys())
            col_list = ", ".join(cols)
            # Query the rows by IDs or composite keys
            db_rows = conn.execute(text(f'SELECT {col_list} FROM "{tbl}"')).fetchall()  # noqa: S608
            db_records: dict[str, dict[str, Any]] = {}
            for r in db_rows:
                entry = {col: _normalize_json_field(val) for col, val in zip(cols, r, strict=False)}
                key = (
                    str(entry.get("id"))
                    if "id" in entry
                    else f"{entry.get('user_id')}:{entry.get('profile_id')}"
                )
                db_records[key] = entry

            for expected in expected_rows:
                key = (
                    str(expected.get("id"))
                    if "id" in expected
                    else f"{expected.get('user_id')}:{expected.get('profile_id')}"
                )
                if key not in db_records:
                    raise AssertionError(
                        f"Pre-upgrade record {key} in '{tbl}' was lost during upgrade!"
                    )
                actual = db_records[key]
                for col in cols:
                    if actual[col] != expected[col]:
                        raise AssertionError(
                            f"Field '{col}' in '{tbl}' record {key} changed: "
                            f"expected {expected[col]!r}, got {actual[col]!r}"
                        )

    # 4. Verify new tables introduced in version 4 and 5
    new_tables = ["store_owner", "execution_runtimes"]
    old_version = snapshot.get("schema_version")
    if old_version in ("1", "2", "3", "4"):
        new_tables.extend(["copilot_tool_calls", "proposed_edit_parts"])

    for tbl in new_tables:
        if tbl not in current_tables:
            raise AssertionError(f"Expected new table '{tbl}' was not created during upgrade!")

    # Verify store_owner table contains the singleton lock row
    with engine.connect() as conn:
        owner_rows = conn.execute(
            text("SELECT id, runtime_id FROM store_owner WHERE id = 1")
        ).fetchall()
        if not owner_rows or owner_rows[0][0] != 1 or owner_rows[0][1] != "":
            raise AssertionError(f"store_owner singleton row is missing or invalid: {owner_rows}")

    # 5. Verify new columns exist and default properly for pre-existing records
    exec_columns = {c["name"] for c in inspector.get_columns("executions")}
    if not {"owner_id", "dispatched_at"} <= exec_columns:
        raise AssertionError("executions table is missing owner_id or dispatched_at columns!")

    wss_columns = {c["name"] for c in inspector.get_columns("worksheet_sessions")}
    if not {"owner_id", "commit_requested_at"} <= wss_columns:
        raise AssertionError("worksheet_sessions is missing owner_id or commit_requested_at!")

    cp_columns = {c["name"] for c in inspector.get_columns("connection_profiles")}
    if "wallet_secret_reference_id" not in cp_columns:
        raise AssertionError("connection_profiles is missing wallet_secret_reference_id column!")

    # Verify existing rows have empty or NULL defaults
    with engine.connect() as conn:
        for row in conn.execute(
            text("SELECT id, owner_id, dispatched_at FROM executions")
        ).fetchall():
            if row[1] != "" or row[2] is not None:
                raise AssertionError(
                    f"Pre-upgrade execution {row[0]} has unexpected defaults: "
                    f"owner_id={row[1]!r}, dispatched_at={row[2]!r}"
                )
        for row in conn.execute(
            text("SELECT id, owner_id, commit_requested_at FROM worksheet_sessions")
        ).fetchall():
            if row[1] != "" or row[2] is not None:
                raise AssertionError(
                    f"Pre-upgrade worksheet session {row[0]} has unexpected defaults: "
                    f"owner_id={row[1]!r}, commit_requested_at={row[2]!r}"
                )
        for row in conn.execute(
            text("SELECT id, wallet_secret_reference_id FROM connection_profiles")
        ).fetchall():
            if row[1] is not None:
                raise AssertionError(
                    f"Pre-upgrade profile {row[0]} has unexpected wallet_secret_reference_id: "
                    f"{row[1]!r}"
                )

    # 6. Audit chain verification (if model has hash chaining)
    audit_columns = {c["name"] for c in inspector.get_columns("audit_events")}
    chain_col = next(
        (c for c in ("chain_hash", "previous_hash", "hash") if c in audit_columns),
        None,
    )
    if chain_col:
        log.info("Verifying audit hash chain on column %s...", chain_col)
    else:
        with engine.connect() as conn:
            cnt = conn.execute(text("SELECT count(*) FROM audit_events")).scalar()
            if int(cnt or 0) < len(snapshot_records.get("audit_events", [])):
                raise AssertionError("Audit events missing after upgrade!")


def verify_idempotency(engine: Engine, baseline_snapshot: dict[str, Any]) -> None:
    """Verify that a second initialize_schema run produces no changes."""
    upgrade_schema(engine)
    new_snapshot = take_snapshot(engine)

    if new_snapshot["schema_version"] != baseline_snapshot["schema_version"]:
        raise AssertionError(
            f"Schema version changed on second run: "
            f"expected {baseline_snapshot['schema_version']}, got {new_snapshot['schema_version']}"
        )

    if new_snapshot["table_counts"] != baseline_snapshot["table_counts"]:
        raise AssertionError(
            f"Table counts changed on second run: "
            f"before={baseline_snapshot['table_counts']}, after={new_snapshot['table_counts']}"
        )

    if new_snapshot["records"] != baseline_snapshot["records"]:
        raise AssertionError(
            f"Records changed on second run: "
            f"before={baseline_snapshot['records']}, after={new_snapshot['records']}"
        )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Metadata store upgrade drill helper.")
    subparsers = parser.add_subparsers(dest="action", required=True)

    # seed
    seed_p = subparsers.add_parser("seed", help="Seed demo and representative data.")
    seed_p.add_argument("--db-url", required=True, help="Database connection URL.")
    seed_p.add_argument(
        "--skip-demo-seed", action="store_true", help="Skip calling harness_api.seed."
    )

    # snapshot
    snap_p = subparsers.add_parser("snapshot", help="Snapshot row counts and identifying data.")
    snap_p.add_argument("--db-url", required=True, help="Database connection URL.")
    snap_p.add_argument("--output", required=True, help="Output JSON file path.")

    # verify-version
    ver_p = subparsers.add_parser("verify-version", help="Verify schema_version table value.")
    ver_p.add_argument("--db-url", required=True, help="Database connection URL.")
    ver_p.add_argument("--expected", required=True, help="Expected version string.")

    # upgrade
    up_p = subparsers.add_parser("upgrade", help="Run schema upgrade.")
    up_p.add_argument("--db-url", required=True, help="Database connection URL.")

    # verify
    verify_p = subparsers.add_parser("verify", help="Verify upgrade data retention and schema.")
    verify_p.add_argument("--db-url", required=True, help="Database connection URL.")
    verify_p.add_argument("--snapshot", required=True, help="Path to pre-upgrade snapshot JSON.")
    verify_p.add_argument("--expected-version", default="5", help="Expected schema version.")

    # verify-idempotent
    idem_p = subparsers.add_parser("verify-idempotent", help="Verify upgrade idempotency.")
    idem_p.add_argument("--db-url", required=True, help="Database connection URL.")
    idem_p.add_argument("--snapshot", required=True, help="Path to post-upgrade snapshot JSON.")

    args = parser.parse_args(argv)
    engine = create_engine(args.db_url, future=True, pool_pre_ping=True)

    try:
        if args.action == "seed":
            seed_representative_data(engine, run_demo_seed=not args.skip_demo_seed)
            print("Successfully seeded representative records.")
        elif args.action == "snapshot":
            snap = take_snapshot(engine)
            Path(args.output).write_text(json.dumps(snap, indent=2), encoding="utf-8")
            print(f"Snapshot written to {args.output}")
        elif args.action == "verify-version":
            v = get_schema_version(engine)
            if v != args.expected:
                raise SystemExit(f"Schema version mismatch: expected {args.expected}, got {v}")
            print(f"Schema version verified: {v}")
        elif args.action == "upgrade":
            result = upgrade_schema(engine)
            print(f"Upgraded schema to version {result}")
        elif args.action == "verify":
            snap_data = json.loads(Path(args.snapshot).read_text(encoding="utf-8"))
            verify_upgrade(engine, snap_data, expected_version=args.expected_version)
            print("Upgrade verified: all records and tables intact.")
        elif args.action == "verify-idempotent":
            snap_data = json.loads(Path(args.snapshot).read_text(encoding="utf-8"))
            verify_idempotency(engine, snap_data)
            print("Idempotency verified: re-running upgrade produced no changes.")
    finally:
        engine.dispose()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
