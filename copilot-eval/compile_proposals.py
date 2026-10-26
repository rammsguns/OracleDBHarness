"""Compile the proposals of an evaluation report on Oracle, in a disposable schema.

Round-13 review (C08): a passing structural check is not evidence that a generated unit
compiles. This takes a report, rebuilds the tables the cases' schema metadata describe
in a new account, and for each case's proposal:

- CREATE OR REPLACE <unit>: runs the DDL and reads USER_ERRORS for that unit;
- SELECT / WITH / DECLARE / BEGIN: parses it with DBMS_SQL.PARSE, which compiles a
  query or anonymous block without running it;
- anything else (GRANT, DML): skipped, because it would change something.

What this shows and what it does not (round 14, C17): a COMPILED or PARSED line is
compile/parse evidence only. Nothing is executed, so it says nothing about behaviour.
The tables are the union of the cases' metadata (one EMPLOYEES with every column any
case lists), so a column a case did not supply still compiles here: column grounding
stays the reviewer's job. HARNESS_APP. prefixes are removed so the units land in the
disposable account. Text that names another schema (hr.) is skipped. By default only
the stored proposal is checked; SQL that appears only in an answer (a Kiwi diagnosis has no
proposal field) is listed as NOT CHECKED, never counted as a success.

    python copilot-eval/compile_proposals.py REPORT.json localhost:1522/ORCL PASSWORD_FILE

K7P_FIXTURE_SQL may name an operator-authored SQL fixture file, loaded in the
disposable schema before setup is marked complete; fixture errors fail setup.
Standalone FUNCTION/PROCEDURE editor proposals receive a CREATE OR REPLACE prefix.
K7P_CHECK_ANSWER_SQL=1 also checks fenced SQL when there is no stored proposal;
unsupported statements remain skipped and are never counted as compiled.

The password file is read here and the password goes to SQL*Plus on stdin, never in an
argument; it and the generated account password are scrubbed from the saved log.

Behaviour probes (round 14, C08): compiling does not show behaviour, so for a case
listed in PROBES the compiled unit is also called on seeded rows in the disposable
schema, and each call prints what it returned or raised. FIX-03 calls salary_for for a
last name with one row, none, two equal salaries, two different salaries, a NULL beside
a value, and two NULLs. A unit that silently returns a value for different or
NULL-mixed salaries fails; the other outcomes are recorded for the reviewer, since which
policy is right is the user's decision. A required "deliberate" outcome (round 15, C17)
is met only by an error the proposal itself raises: a RAISE_APPLICATION_ERROR code that
appears in its text. Any other exception (a -6502 from a type slip, a raw -1422) is
unrelated and fails, and so does a returned value; the error message is logged so the
reviewer can check it against what the answer says the unit does.

Ownership and cleanup follow scripts/k7_oracle_checks.sh (C05). The setup prints
K7P-PREFLIGHT-CLEAR once it has seen that K7P_APP does not exist, and K7P-CREATED
K7P_APP right after creating it. Cleanup runs in a separate SQL*Plus session after the
run, whether it finished, failed part-way, timed out or was interrupted, and drops the
account only when both markers were printed. An account that exists without them is
reported and left alone. Exit status: 0 only when setup completed, every checked
proposal compiled or parsed, every expected case reported once, and cleanup left
nothing behind; 4 when K7P_APP already existed (nothing dropped); 130 when interrupted;
1 for anything else.
"""

# This file writes a SQL*Plus script by design; the names it interpolates are fixed or
# matched by UNIT (\w+ only), and proposal text goes inside a q-quote.
# ruff: noqa: S608

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import secrets
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SQLPLUS = os.environ.get("SQLPLUS", "/opt/oracle/instantclient_21_4/sqlplus")
ACCOUNT = "K7P_APP"
RUN_TIMEOUT = float(os.environ.get("K7P_RUN_TIMEOUT", "600"))
CLEANUP_TIMEOUT = float(os.environ.get("K7P_CLEANUP_TIMEOUT", "120"))
CLEANUP_ATTEMPTS = 3
CLEANUP_RETRY_SECONDS = float(os.environ.get("K7P_CLEANUP_RETRY_SECONDS", "2"))
LOG_DIR = Path(os.environ.get("K7P_LOG_DIR", ROOT / "databases" / "reports"))

TABLES = """
CREATE TABLE departments (department_id NUMBER(4) PRIMARY KEY,
  department_name VARCHAR2(30) NOT NULL, manager_id NUMBER(6));
CREATE TABLE departments_stage AS SELECT * FROM departments WHERE 1 = 0;
CREATE TABLE employees (employee_id NUMBER(6) PRIMARY KEY, first_name VARCHAR2(20),
  last_name VARCHAR2(25) NOT NULL, salary NUMBER(8,2), department_id NUMBER(4),
  status VARCHAR2(10));
CREATE TABLE orders (order_id NUMBER(12) PRIMARY KEY, customer_id NUMBER(10) NOT NULL,
  order_date DATE NOT NULL, status VARCHAR2(12), total NUMBER(12,2));
CREATE TABLE orders_archive (order_id NUMBER(12) PRIMARY KEY,
  customer_id NUMBER(10) NOT NULL, order_date DATE NOT NULL, status VARCHAR2(12),
  total NUMBER(12,2));
CREATE OR REPLACE FUNCTION headcount(p_department_id IN NUMBER) RETURN NUMBER IS
  l_count NUMBER;
BEGIN
  SELECT COUNT(*) INTO l_count FROM employees WHERE department_id = p_department_id;
  RETURN l_count;
END headcount;
/
INSERT INTO employees (employee_id, last_name, salary) VALUES (1, 'ONE', 100);
INSERT INTO employees (employee_id, last_name, salary) VALUES (2, 'EQ', 200);
INSERT INTO employees (employee_id, last_name, salary) VALUES (3, 'EQ', 200);
INSERT INTO employees (employee_id, last_name, salary) VALUES (4, 'NEQ', 300);
INSERT INTO employees (employee_id, last_name, salary) VALUES (5, 'NEQ', 400);
INSERT INTO employees (employee_id, last_name, salary) VALUES (6, 'MIX', NULL);
INSERT INTO employees (employee_id, last_name, salary) VALUES (7, 'MIX', 5000);
INSERT INTO employees (employee_id, last_name, salary) VALUES (8, 'ALLNULL', NULL);
INSERT INTO employees (employee_id, last_name, salary) VALUES (9, 'ALLNULL', NULL);
COMMIT;
DECLARE
  l_n NUMBER;
BEGIN
  SELECT COUNT(*) INTO l_n FROM user_objects
   WHERE object_name = 'HEADCOUNT' AND status = 'VALID';
  IF l_n <> 1 THEN
    RAISE_APPLICATION_ERROR(-20000, 'K7P-SETUP-FAILED headcount is not valid');
  END IF;
END;
/
"""

UNIT = re.compile(
    r"^\s*CREATE\s+(?:OR\s+REPLACE\s+)?(?:EDITIONABLE\s+|NONEDITIONABLE\s+)?"
    r"(PACKAGE\s+BODY|PACKAGE|PROCEDURE|FUNCTION|TRIGGER|TYPE\s+BODY|TYPE|VIEW)\s+"
    r"(?:\"?\w+\"?\.)?\"?(\w+)\"?",
    re.IGNORECASE,
)
FENCE = re.compile(r"```(?:sql|plsql)\s*\n(.*?)```", re.IGNORECASE | re.DOTALL)
# case id -> (the unit it must be, the call with :arg for the probe's argument,
# {argument: required outcome or None for recorded only}). A required outcome is
# "returned <v>" or "deliberate": raised with a code the proposal's own
# RAISE_APPLICATION_ERROR calls name.
PROBES: dict[str, tuple[str, str, dict[str, str | None]]] = {
    "FIX-03": (
        "FUNCTION:SALARY_FOR",
        "salary_for(:arg)",
        {
            "ONE": "returned 100",
            "NONE": None,
            "EQ": None,
            "NEQ": "deliberate",
            "MIX": "deliberate",
            "ALLNULL": None,
        },
    ),
}
APP_ERROR = re.compile(r"RAISE_APPLICATION_ERROR\s*\(\s*(-\s*20\d{3})\b", re.IGNORECASE)
PROBE_LINE = re.compile(r"^PROBE (\S+) (\S+): (returned .*|raised .*|NOT RUN .*)$")
CASE_LINE = re.compile(r"^CASE (\S+): (COMPILED|PARSED|ERROR.*)$")
SQL_ERROR = re.compile(r"^(SP2-\d+|ORA-\d+|ERROR at line|ERROR:)")


class Interrupted(Exception):
    pass


class TimedOut(Exception):
    pass


def plan(text: str) -> tuple[str, str, str]:
    """(kind, unit name, prepared text); kind is unit, parse or skip:<reason>."""

    body = re.sub(r"(?im)^\s*/\s*$", "", text).strip()
    body = re.sub(r"(?i)\bharness_app\.", "", body)
    if re.search(r"(?i)\bhr\.", body):
        return "skip:names another schema (hr.)", "", body
    # Editor proposals may contain a standalone unit without its DDL prefix.
    if re.match(r"(?i)^(FUNCTION|PROCEDURE)\s+\w+\b", body):
        body = "CREATE OR REPLACE " + body
    match = UNIT.match(body)
    if match:
        kind = match.group(1).upper()
        if kind == "VIEW":
            body = body.rstrip(";")
        return "unit", f"{' '.join(kind.split())}:{match.group(2).upper()}", body
    first = body.split(None, 1)[0].upper() if body else ""
    if first in ("SELECT", "WITH"):
        return "parse", "", body.rstrip().rstrip(";")
    if first in ("DECLARE", "BEGIN"):
        return "parse", "", body
    return f"skip:{first or 'empty'} would change something or is not SQL", "", body


def quote(text: str) -> str:
    for delim in "~#^!|@%":
        if f"{delim}'" not in text:
            return f"q'{delim}{text}{delim}'"
    raise ValueError("no quote delimiter is free")


def coverage(report: dict) -> list[tuple[str, str, str, str]]:
    """(case id, status, unit, text) per case. Status is unit, parse, skip:<why>,
    unchecked:<why> (SQL only in the answer) or none (no SQL at all)."""

    rows = []
    for case in report["cases"]:
        proposed = (case.get("proposal") or {}).get("proposedText")
        if proposed:
            kind, unit, text = plan(proposed)
            rows.append((case["id"], kind, unit, text))
            continue
        blocks = FENCE.findall(case.get("answer") or "")
        if blocks and os.environ.get("K7P_CHECK_ANSWER_SQL") == "1":
            for number, block in enumerate(blocks, 1):
                kind, unit, text = plan(block)
                rows.append((f"{case['id']}-answer-{number}", kind, unit, text))
            continue
        if blocks:
            rows.append(
                (
                    case["id"],
                    f"unchecked:{len(blocks)} SQL block(s) in the answer, no stored proposal",
                    "",
                    "",
                )
            )
        else:
            rows.append((case["id"], "none", "", ""))
    return rows


def case_block(case_id: str, kind: str, unit: str, text: str) -> str:
    unit_type, _, unit_name = unit.partition(":")
    check = (
        f"""
  IF l_err IS NULL OR l_err LIKE 'ORA-24344%' THEN
    SELECT MAX(line || ':' || position || ' ' || text) KEEP (DENSE_RANK FIRST ORDER BY sequence)
      INTO l_err FROM user_errors WHERE name = '{unit_name}' AND type = '{unit_type}';
  END IF;"""
        if kind == "unit"
        else ""
    )
    label = "COMPILED" if kind == "unit" else "PARSED"
    return f"""DECLARE
  c INTEGER := DBMS_SQL.OPEN_CURSOR;
  l_err VARCHAR2(4000);
BEGIN
  BEGIN
    DBMS_SQL.PARSE(c, {quote(text)}, DBMS_SQL.NATIVE);
  EXCEPTION WHEN OTHERS THEN
    l_err := SQLERRM;
  END;
  DBMS_SQL.CLOSE_CURSOR(c);{check}
  DBMS_OUTPUT.PUT_LINE('CASE {case_id}: ' || CASE WHEN l_err IS NULL THEN '{label}'
    ELSE 'ERROR ' || REPLACE(l_err, CHR(10), ' ') END);
END;
/
"""


def probe_block(case_id: str, unit: str) -> str:
    if case_id not in PROBES:
        return ""
    want, call, args = PROBES[case_id]
    unit_type, _, unit_name = want.partition(":")
    if unit != want:
        got = unit or "not a unit"
        return f"PROMPT PROBE {case_id} ALL: NOT RUN the proposal is {got}, not {want}\n"
    calls = "".join(
        f"""  BEGIN
    DBMS_OUTPUT.PUT_LINE('PROBE {case_id} {arg}: returned '
      || NVL(TO_CHAR({call.replace(":arg", repr(arg))}), 'NULL'));
  EXCEPTION WHEN OTHERS THEN
    DBMS_OUTPUT.PUT_LINE('PROBE {case_id} {arg}: raised ' || SQLCODE || ' '
      || REPLACE(SUBSTR(SQLERRM, 1, 200), CHR(10), ' '));
  END;
"""
        for arg in args
    )
    return f"""DECLARE
  l_n NUMBER;
BEGIN
  SELECT COUNT(*) INTO l_n FROM user_objects
   WHERE object_name = '{unit_name}' AND object_type = '{unit_type}' AND status = 'VALID';
  IF l_n = 0 THEN
    DBMS_OUTPUT.PUT_LINE('PROBE {case_id} ALL: NOT RUN the unit is not valid');
    RETURN;
  END IF;
{calls}END;
/
"""


def script(rows: list, system_password: str, account_password: str, service: str) -> str:
    """Setup and the checked cases. Cleanup is a separate session (cleanup_script)."""

    blocks = "".join(
        case_block(cid, kind, unit, text) + probe_block(cid, unit)
        for cid, kind, unit, text in rows
        if kind in ("unit", "parse")
    )
    return f"""WHENEVER SQLERROR EXIT FAILURE
CONNECT system/"{system_password}"@{service}
SET SERVEROUTPUT ON SIZE UNLIMITED FEEDBACK OFF LINESIZE 400
DECLARE
  l_n NUMBER;
BEGIN
  SELECT COUNT(*) INTO l_n FROM dba_users WHERE username = '{ACCOUNT}';
  IF l_n > 0 THEN
    RAISE_APPLICATION_ERROR(-20000, 'K7P-COLLISION {ACCOUNT} already exists; not touching it');
  END IF;
END;
/
PROMPT K7P-PREFLIGHT-CLEAR
CREATE USER {ACCOUNT} IDENTIFIED BY "{account_password}" QUOTA 10M ON users;
PROMPT K7P-CREATED {ACCOUNT}
GRANT CREATE SESSION, CREATE TABLE, CREATE PROCEDURE, CREATE VIEW, CREATE TRIGGER TO {ACCOUNT};
CONNECT {ACCOUNT}/"{account_password}"@{service}
SET SERVEROUTPUT ON SIZE UNLIMITED FEEDBACK OFF LINESIZE 400
{TABLES}
{Path(os.environ["K7P_FIXTURE_SQL"]).read_text() if os.environ.get("K7P_FIXTURE_SQL") else ""}
PROMPT K7P-SETUP-DONE
WHENEVER SQLERROR CONTINUE
{blocks}
PROMPT K7P-CASES-DONE
EXIT
"""


def cleanup_script(system_password: str, service: str, drop: bool) -> str:
    drop_line = f"DROP USER {ACCOUNT} CASCADE;\n" if drop else ""
    return f"""WHENEVER SQLERROR CONTINUE
CONNECT system/"{system_password}"@{service}
SET HEADING OFF FEEDBACK ON PAGESIZE 0
{drop_line}SELECT 'REMAINING: ' || COUNT(*) FROM dba_users WHERE username = '{ACCOUNT}';
EXIT
"""


def run_sqlplus(text: str, timeout: float) -> tuple[int, str]:
    """Run SQL*Plus with the script on stdin. Output goes to a file, so what was printed
    before a timeout or interruption is kept. Raises TimedOut or Interrupted with the
    partial output as the argument."""

    with tempfile.TemporaryFile(mode="w+") as out:
        proc = subprocess.Popen(  # noqa: S603 - fixed client path; credentials go on stdin
            [SQLPLUS, "-S", "/nolog"],
            stdin=subprocess.PIPE,
            stdout=out,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            proc.communicate(text, timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            out.seek(0)
            raise TimedOut(out.read()) from None
        except (Interrupted, KeyboardInterrupt):
            proc.kill()
            proc.wait()
            out.seek(0)
            raise Interrupted(out.read()) from None
        out.seek(0)
        return proc.returncode, out.read()


def cleanup(output: str, system_password: str, service: str) -> tuple[str, list[str]]:
    """Drop the account if this run created it, then confirm what remains. Returns the
    cleanup output and its problems."""

    preflight = "K7P-PREFLIGHT-CLEAR" in output
    created = f"K7P-CREATED {ACCOUNT}" in output
    if not preflight:
        return "== cleanup: not attempted, the run never showed the name was free\n", []
    text, problems = "", []
    for attempt in range(1, CLEANUP_ATTEMPTS + 1):
        try:
            rc, out = run_sqlplus(
                cleanup_script(system_password, service, created), CLEANUP_TIMEOUT
            )
        except TimedOut as exc:
            rc, out = -1, str(exc) + "\n(cleanup timed out)"
        text += f"== cleanup attempt {attempt} ({'drop' if created else 'check only'})\n{out}\n"
        remaining = re.search(r"REMAINING: (\d+)", out)
        # ORA-01940: the killed session has not gone yet; try again.
        if created and "ORA-01940" in out and attempt < CLEANUP_ATTEMPTS:
            time.sleep(CLEANUP_RETRY_SECONDS)
            continue
        if rc != 0:
            problems.append(f"cleanup sqlplus exited {rc}")
        if remaining is None:
            problems.append("could not confirm what remains")
        elif remaining.group(1) != "0":
            problems.append(
                f"{ACCOUNT} was created by this run and is still there"
                if created
                else f"{ACCOUNT} exists without a creation record from this run; "
                "not dropped, check it by hand"
            )
        elif created and re.search(r"^ORA-\d+", out, re.MULTILINE):
            problems.append("the DROP reported an error")
        break
    return text, problems


def assess(rows: list, output: str, rc: int | None) -> tuple[list[str], list[str]]:
    """Per-case result lines and the run's problems (empty means the checks passed)."""

    expected = [cid for cid, kind, _, _ in rows if kind in ("unit", "parse")]
    seen: dict[str, list[str]] = {}
    for line in output.splitlines():
        match = CASE_LINE.match(line.strip())
        if match:
            seen.setdefault(match.group(1), []).append(match.group(2))
    problems: list[str] = []
    if "K7P-SETUP-DONE" not in output:
        problems.append("setup did not complete")
    if expected and "K7P-CASES-DONE" not in output:
        problems.append("the case checks did not finish")
    if rc not in (0, None):
        problems.append(f"sqlplus exited {rc}")
    setup = output.split("K7P-SETUP-DONE", 1)[0]
    errors = [line for line in setup.splitlines() if SQL_ERROR.match(line.strip())]
    if errors:
        problems.append(f"{len(errors)} SQL error line(s) during setup")
    if any(line.strip().startswith("SP2-") for line in output.splitlines()):
        problems.append("SQL*Plus reported an SP2 error")
    results = []
    for cid, kind, _, _ in rows:
        if kind in ("unit", "parse"):
            got = seen.get(cid, [])
            if not got:
                results.append(f"CASE {cid}: MISSING (no result reported)")
                problems.append(f"{cid} reported no result")
            elif len(got) > 1:
                results.append(f"CASE {cid}: {' / '.join(got)} (reported {len(got)} times)")
                problems.append(f"{cid} reported {len(got)} results")
            else:
                results.append(f"CASE {cid}: {got[0]}")
                if got[0].startswith("ERROR"):
                    problems.append(f"{cid} did not compile or parse")
        elif kind.startswith("skip:"):
            results.append(f"CASE {cid}: SKIPPED {kind[5:]} (not checked)")
        elif kind.startswith("unchecked:"):
            results.append(f"CASE {cid}: NOT CHECKED {kind[10:]}")
        else:
            results.append(f"CASE {cid}: NO SQL")
    results += assess_probes(rows, output, problems)
    unknown = sorted(set(seen) - set(expected))
    if unknown:
        problems.append(f"results for cases not checked: {', '.join(unknown)}")
    return results, problems


def probe_verdict(required: str, got: str, own: set[int]) -> str:
    """'ok', or why ``got`` does not meet ``required``."""

    if required != "deliberate":
        return "ok" if got == required else "a different outcome"
    if not got.startswith("raised"):
        return "returned a value where an error was required"
    code = got.split()[1] if len(got.split()) > 1 else ""
    try:
        number = int(code)
    except ValueError:
        return "no error code"
    if not own:
        return "the proposal raises no application error of its own"
    if number not in own:
        named = ", ".join(str(c) for c in sorted(own))
        return f"unrelated exception; the proposal raises only {named}"
    return "ok"


def assess_probes(rows: list, output: str, problems: list[str]) -> list[str]:
    seen: dict[tuple[str, str], str] = {}
    for line in output.splitlines():
        match = PROBE_LINE.match(line.strip())
        if match:
            seen[(match.group(1), match.group(2))] = match.group(3)
    lines = []
    for cid, kind, _, text in rows:
        if cid not in PROBES or kind not in ("unit", "parse"):
            continue
        own = {int(code.replace(" ", "")) for code in APP_ERROR.findall(text)}
        if (cid, "ALL") in seen:
            lines.append(f"PROBE {cid}: {seen[(cid, 'ALL')]}")
            problems.append(f"{cid} behaviour probe did not run")
            continue
        for arg, required in PROBES[cid][2].items():
            got = seen.get((cid, arg))
            if got is None:
                lines.append(f"PROBE {cid} {arg}: MISSING")
                problems.append(f"{cid} probe {arg} reported nothing")
                continue
            if required is None:
                lines.append(f"PROBE {cid} {arg}: {got} (recorded)")
                continue
            verdict = probe_verdict(required, got, own)
            if verdict == "ok":
                lines.append(f"PROBE {cid} {arg}: {got} (required {required}: ok)")
            else:
                lines.append(f"PROBE {cid} {arg}: {got} (required {required}: FAILED, {verdict})")
                problems.append(f"{cid} probe {arg}: {got}, required {required} ({verdict})")
    return lines


def summary(results: list[str]) -> str:
    counts: dict[str, int] = {}
    for line in results:
        if not line.startswith("CASE "):
            continue
        words = line.split(": ", 1)[1].split()
        word = " ".join(words[:2]) if words[0] in ("NO", "NOT") else words[0]
        counts[word] = counts.get(word, 0) + 1
    return "coverage: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items()))


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print(__doc__)
        return 2
    report_path, service, password_file = Path(argv[1]), argv[2], Path(argv[3])
    raw = report_path.read_bytes()
    rows = coverage(json.loads(raw))
    system_password = password_file.read_text().rstrip("\n")
    account_password = "K7p_" + secrets.token_hex(8)

    def on_signal(signum, frame):  # noqa: ARG001 - signal handler signature
        raise Interrupted("")

    previous = {s: signal.signal(s, on_signal) for s in (signal.SIGINT, signal.SIGTERM)}
    rc: int | None = None
    output, outcome = "", ""
    try:
        try:
            rc, output = run_sqlplus(
                script(rows, system_password, account_password, service), RUN_TIMEOUT
            )
        except TimedOut as exc:
            output, outcome = str(exc), f"TIMED OUT after {RUN_TIMEOUT:g}s"
        except Interrupted as exc:
            output, outcome = str(exc), "INTERRUPTED"
        # Cleanup must finish even if a second signal arrives.
        for s in previous:
            signal.signal(s, signal.SIG_IGN)
        cleanup_text, cleanup_problems = cleanup(output, system_password, service)
    finally:
        for s, handler in previous.items():
            signal.signal(s, handler)

    collision = "K7P-COLLISION" in output
    results, problems = assess(rows, output, rc)
    if outcome:
        problems.insert(0, outcome.lower())
    problems += cleanup_problems
    if collision:
        status, code = f"REFUSED - {ACCOUNT} already existed; nothing changed, nothing dropped", 4
    elif outcome == "INTERRUPTED":
        status, code = f"INTERRUPTED - {'; '.join(problems)}", 130
    elif problems:
        status, code = f"FAILED - {'; '.join(problems)}", 1
    else:
        status, code = "PASSED - every checked proposal compiled or parsed; cleaned up", 0

    def scrub(text: str) -> str:
        if system_password:
            text = text.replace(system_password, "<password>")
        return text.replace(account_password, "<account password>")

    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    log = LOG_DIR / f"k7-proposal-compile-{stamp}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    header = [
        f"report: {report_path.name}",
        f"report sha256: {hashlib.sha256(raw).hexdigest()}",
        f"service: {service}",
        f"sqlplus exit: {rc if rc is not None else outcome}",
        "scope: compile/parse against a union schema, plus the behaviour probes listed; "
        "no other execution, no case grounding; SKIPPED and NOT CHECKED are not "
        "successes",
        summary(results),
    ]
    body = header + ["", *results, "", f"result: {status}"]
    log.write_text(
        scrub("\n".join(body + ["", "-- full output --", output, "-- cleanup --", cleanup_text]))
        + "\n"
    )
    print(scrub("\n".join(body)))
    print(f"log: {log}")
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv))
