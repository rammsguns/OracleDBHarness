"""Keep pack-licensed access out of product SQL and reviewed tool catalogs."""

import ast
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FORBIDDEN = re.compile(
    r"\b(?:DBA_HIST_\w*|G?V_?\$ACTIVE_SESSION_HISTORY|ASH|G?V_?\$SQL_MONITOR|"
    r"SQL[ _-]+MONITOR(?:ING)?|DBMS_SQL_MONITOR|DBMS_SQLTUNE|DBMS_WORKLOAD_\w*|DBMS_ADDM)\b",
    re.IGNORECASE,
)
SQL_START = re.compile(
    r"^\s*(?:(?:--[^\n]*\n|/\*.*?\*/)\s*)*"
    r"(?:SELECT|WITH|FROM|JOIN|BEGIN|DECLARE|CALL|EXEC(?:UTE)?|GRANT|REVOKE|"
    r"CREATE|ALTER|DROP|INSERT|UPDATE|DELETE|MERGE|DBMS_\w+)\b",
    re.IGNORECASE | re.DOTALL,
)


def prohibited_references(path):
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".py":
        # Scan SQL literals (including f-string fragments) plus all catalog/toolbox
        # metadata. Ordinary prose and safety prompts may document exclusions.
        chunks = [
            (node.lineno, node.value)
            for node in ast.walk(ast.parse(text))
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and (
                SQL_START.match(node.value)
                or path.stem in {"catalog", "toolbox"}
                or re.search(
                    r"\b(?:DBA_HIST_|G?V_?\$ACTIVE_SESSION_HISTORY|G?V_?\$SQL_MONITOR|DBMS_SQL|DBMS_WORKLOAD_|DBMS_ADDM)",
                    node.value,
                    re.IGNORECASE,
                )
            )
        ]
    else:
        chunks = [(1, text)]
    return [
        f"{line + value[: match.start()].count(chr(10))}:{match.group()}"
        for line, value in chunks
        for match in FORBIDDEN.finditer(value)
    ]


def test_product_sql_and_catalogs_exclude_licensed_features():
    # Enumerate only product roots, without traversing excluded local stores.
    paths = (
        subprocess.check_output(
            ["git", "ls-files", "-z", "--", "services", "oracle", "runbooks", "kiwi"],
            cwd=ROOT,
        )
        .decode()
        .split("\0")
    )
    candidates = [
        ROOT / name
        for name in paths
        if name
        and Path(name).suffix in {".sql", ".py", ".json", ".yaml", ".yml", ".sh"}
        and "docs" not in Path(name).parts
    ]
    assert any(path.suffix == ".sql" for path in candidates)
    assert ROOT / "services/api/harness_api/copilot/toolbox.py" in candidates
    findings = [
        f"{path.relative_to(ROOT)}:{hit}"
        for path in candidates
        for hit in prohibited_references(path)
    ]
    assert findings == []


@pytest.mark.parametrize(
    "reference",
    [
        "DBA_HIST_SNAPSHOT",
        "dba_hist_sqlstat",
        "V$ACTIVE_SESSION_HISTORY",
        "gv$active_session_history",
        "V_$ACTIVE_SESSION_HISTORY",
        "ASH",
        "V$SQL_MONITOR",
        "gv$sql_monitor",
        "GV_$SQL_MONITOR",
        "SQL Monitor",
        "SQL_MONITOR",
        "DBMS_SQL_MONITOR",
        "DBMS_SQLTUNE",
        "DBMS_WORKLOAD_REPOSITORY",
        "DBMS_WORKLOAD_CAPTURE",
        "DBMS_WORKLOAD_REPLAY",
        "DBMS_ADDM",
    ],
)
@pytest.mark.parametrize("suffix", [".sql", ".py"])
def test_scan_rejects_licensed_sql(tmp_path, reference, suffix):
    path = tmp_path / f"query{suffix}"
    sql = f"SELECT * FROM {reference}"
    path.write_text(f"SQL = {sql!r}" if suffix == ".py" else sql)
    assert prohibited_references(path)
