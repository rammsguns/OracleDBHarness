"""Keep provisioning and its read-only verifier consistent without Oracle."""

import re
import sqlite3
from pathlib import Path

import pytest

GRANTS = Path(__file__).resolve().parents[2] / "oracle" / "grants"


def sql(name: str) -> str:
    return re.sub(r"--[^\n]*", "", (GRANTS / name).read_text(encoding="utf-8"))


def expected_rows(query: str) -> list[tuple[str, ...]]:
    """Read the literal UNION ALL rows of a verifier's expected relation."""
    relation = query.upper().split("FROM (", 1)[1].split(") EXPECTED", 1)[0]
    return [
        tuple(re.findall(r"'([^']*)'", row))
        for row in re.findall(r"SELECT\s+(.*?)\s+FROM DUAL", relation, re.DOTALL)
    ]


def verification_queries() -> list[str]:
    return re.findall(r"SELECT expected\..*?;", sql("verify_harness_grants.sql"), re.DOTALL)


def test_verifier_matches_all_active_role_and_object_grants() -> None:
    baseline = sql("harness_roles.sql").upper()
    role_query, object_query = verification_queries()
    roles = set(re.findall(r"CREATE ROLE\s+(\w+)\s*;", baseline))
    checked_roles = dict(expected_rows(role_query))
    assert roles
    assert set(checked_roles) == roles
    assert checked_roles == {
        "HARNESS_DIAGNOSTICS_ROLE": "Y",
        "HARNESS_DEVELOPER_ROLE": "N",
        "HARNESS_MAINTENANCE_ROLE": "N",
    }

    grants = re.findall(
        r"GRANT\s+(SELECT|EXECUTE)\s+ON\s+(\w+)\.([\w$]+)\s+TO\s+(\w+)\s*;",
        baseline,
    )
    objects = {(owner, name, privilege) for privilege, owner, name, _ in grants}
    checked_objects = {tuple(row[:3]): row[3] for row in expected_rows(object_query)}
    assert objects
    assert set(checked_objects) == objects
    required = {
        (owner, name, privilege)
        for privilege, owner, name, role in grants
        if role == "HARNESS_DIAGNOSTICS_ROLE"
    }
    assert {obj for obj, flag in checked_objects.items() if flag == "Y"} == required


@pytest.mark.parametrize("maintenance", [False, True])
def test_read_only_account_has_no_false_missing_rows(maintenance: bool) -> None:
    """Execute the verifier SELECTs against stand-in dictionary rows."""
    role_query, object_query = verification_queries()
    with sqlite3.connect(":memory:") as db:
        db.executescript(
            "CREATE TABLE dual (dummy TEXT); INSERT INTO dual VALUES ('X');"
            "CREATE TABLE session_roles (role TEXT);"
            "CREATE TABLE all_tab_privs (table_schema TEXT, table_name TEXT, privilege TEXT);"
        )
        db.execute("INSERT INTO session_roles VALUES ('HARNESS_DIAGNOSTICS_ROLE')")
        if maintenance:
            db.execute("INSERT INTO session_roles VALUES ('HARNESS_MAINTENANCE_ROLE')")
        for owner, name, privilege, required in expected_rows(object_query):
            if required == "Y" or maintenance:
                db.execute("INSERT INTO all_tab_privs VALUES (?, ?, ?)", (owner, name, privilege))
        roles = dict(db.execute(role_query).fetchall())
        assert roles["HARNESS_DIAGNOSTICS_ROLE"] == "PRESENT"
        assert roles["HARNESS_DEVELOPER_ROLE"] == "NOT GRANTED (optional)"
        assert roles["HARNESS_MAINTENANCE_ROLE"] == (
            "PRESENT" if maintenance else "NOT GRANTED (optional)"
        )
        objects = {name: status for _, name, _, status in db.execute(object_query)}
        assert "MISSING" not in objects.values()
        assert objects["DBMS_STATS"] == ("VISIBLE" if maintenance else "NOT GRANTED (optional)")
        # An unrelated privilege on the same object cannot satisfy SELECT.
        db.execute("UPDATE all_tab_privs SET privilege = 'EXECUTE' WHERE table_name = 'V_$SESSION'")
        objects = {name: status for _, name, _, status in db.execute(object_query)}
        assert objects["V_$SESSION"] == "MISSING"
        db.execute("DELETE FROM session_roles")
        assert dict(db.execute(role_query))["HARNESS_DIAGNOSTICS_ROLE"] == "MISSING"
