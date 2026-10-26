"""scripts/k7_oracle_checks.sh against a mock sqlplus: ownership, cleanup and secrets.

The real checks need a disposable Oracle database and are run by hand (their logs are in
databases/reports). These tests cover what the wrapper itself decides: it drops only the
accounts the check script reported creating, and only after the script showed the names
were free; it never drops anything after a failed connection, a missing privilege or a
collision; an interruption still cleans up what was created; a failed cleanup fails the
run; and the password reaches neither the log nor any process's arguments.
"""

from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
WRAPPER = REPO_ROOT / "scripts" / "k7_oracle_checks.sh"
PASSWORD = "Not-A-Real-Pw_42"

# Dispatches on what the wrapper sends on stdin. State lives in $MOCK_DIR: existing/<NAME>
# for each account or role that exists, drops for the DROP lines received, argv for the
# arguments of every call.
MOCK = r"""#!/usr/bin/env bash
input="$(cat)"
printf '%s\n' "$*" >>"$MOCK_DIR/argv"
first="${input%%$'\n'*}"
mkdir -p "$MOCK_DIR/existing"
create() { touch "$MOCK_DIR/existing/$1"; echo "K7Q-CREATED $1"; }
case "$input" in
  *'v$version'*)
    if [[ $MOCK_SCENARIO == noconnect ]]; then
      echo "ERROR:"; echo "ORA-12541: TNS:no listener"; exit 1
    fi
    echo "Oracle Database 19c Enterprise Edition Release 19.0.0.0.0 - Production" ;;
  *05_k7_codegen_checks*)
    echo "echo of the connect line: $first"
    case "$MOCK_SCENARIO" in
      privilege) echo "ORA-01031: insufficient privileges"; exit 1 ;;
      collision) touch "$MOCK_DIR/existing/K7Q_OWNER"
                 echo "K7Q-COLLISION already exists, nothing was changed: K7Q_OWNER"
                 echo "ORA-20001: K7Q-COLLISION K7Q_OWNER"; exit 1 ;;
      partial) echo K7Q-PREFLIGHT-CLEAR; create K7Q_OWNER; create K7Q_USER
               echo "ORA-01031: insufficient privileges"; exit 1 ;;
      unmarked) echo K7Q-PREFLIGHT-CLEAR; create K7Q_OWNER
                touch "$MOCK_DIR/existing/K7Q_USER"; exit 1 ;;
      interrupt) echo K7Q-PREFLIGHT-CLEAR; create K7Q_OWNER; touch "$MOCK_DIR/ready"
                 exec sleep 30 ;;
      *) echo K7Q-PREFLIGHT-CLEAR; create K7Q_OWNER; create K7Q_USER; create K7Q_READ
         for i in $(seq 68); do echo "PASS check $i"; done; echo K7Q-DONE ;;
    esac ;;
  *'DROP '*)
    grep '^DROP ' <<<"$input" >>"$MOCK_DIR/drops"
    if [[ $MOCK_SCENARIO == dropfail ]]; then
      echo "ERROR at line 1:"; echo "ORA-01940: cannot drop a user who is currently connected"
      exit 0
    fi
    for name in $(grep -Eo 'K7Q_[A-Z]+' <<<"$input"); do rm -f "$MOCK_DIR/existing/$name"; done ;;
  *dba_users*)
    ls "$MOCK_DIR/existing" ;;
esac
exit 0
"""


@pytest.fixture
def run(tmp_path: Path):
    mock = tmp_path / "sqlplus"
    mock.write_text(MOCK)
    mock.chmod(0o755)
    pwfile = tmp_path / "pw"
    pwfile.write_text(PASSWORD + "\n")
    state = tmp_path / "state"
    state.mkdir()
    logs = tmp_path / "logs"

    def start(
        scenario: str, reports: tuple[str, ...] = (), **env_extra: str
    ) -> subprocess.Popen[str]:
        env = {
            **os.environ,
            "SQLPLUS": str(mock),
            "MOCK_DIR": str(state),
            "MOCK_SCENARIO": scenario,
            **env_extra,
        }
        return subprocess.Popen(
            ["bash", str(WRAPPER), "db:1521/svc", str(pwfile), "system", str(logs), *reports],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def finish(proc: subprocess.Popen[str]) -> dict:
        out, err = proc.communicate(timeout=60)
        (log,) = logs.glob("k7-oracle-checks-*.log")
        drops = (state / "drops").read_text() if (state / "drops").exists() else ""
        existing = (
            sorted(p.name for p in (state / "existing").glob("*"))
            if (state / "existing").exists()
            else []
        )
        argv = (state / "argv").read_text()
        text = log.read_text()
        # The password is in no log and no process's arguments, whatever happened.
        assert PASSWORD not in text and PASSWORD not in out + err and PASSWORD not in argv
        return {
            "rc": proc.returncode,
            "log": text,
            "drops": drops,
            "existing": existing,
            "err": err,
        }

    def execute(scenario: str) -> dict:
        return finish(start(scenario))

    execute.start = start  # type: ignore[attr-defined]
    execute.finish = finish  # type: ignore[attr-defined]
    execute.state = state  # type: ignore[attr-defined]
    return execute


def test_a_normal_run_drops_exactly_what_it_created(run) -> None:
    result = run("normal")
    assert result["rc"] == 0, result["log"]
    assert result["drops"].split("\n")[:3] == [
        "DROP USER K7Q_USER CASCADE;",
        "DROP USER K7Q_OWNER CASCADE;",
        "DROP ROLE K7Q_READ;",
    ]
    assert result["existing"] == []
    assert "result: PASSED - 68/68" in result["log"]
    assert "<password>" in result["log"]  # the mock echoed it; the log has it scrubbed


@pytest.mark.parametrize("scenario,code", [("noconnect", 3), ("privilege", 1), ("collision", 4)])
def test_nothing_is_dropped_without_proof_the_names_were_free(run, scenario, code) -> None:
    result = run(scenario)
    assert result["rc"] == code
    assert result["drops"] == ""
    if scenario == "collision":
        assert result["existing"] == ["K7Q_OWNER"]  # someone else's account survives
    if scenario == "privilege":
        assert "stopped before confirming the names were free" in result["log"]


def test_a_partial_setup_drops_only_the_accounts_it_made(run) -> None:
    result = run("partial")
    assert result["rc"] == 1
    assert result["drops"].strip().split("\n") == [
        "DROP USER K7Q_USER CASCADE;",
        "DROP USER K7Q_OWNER CASCADE;",
    ]
    assert result["existing"] == []


def test_an_account_without_a_creation_record_is_reported_not_dropped(run) -> None:
    result = run("unmarked")
    assert result["rc"] == 1
    assert "K7Q_USER" not in result["drops"]
    assert result["existing"] == ["K7Q_USER"]
    assert "K7Q_USER exists without a creation record" in result["log"]


def test_a_failed_drop_fails_the_run(run) -> None:
    result = run("dropfail")
    assert result["rc"] == 1
    assert "a DROP failed" in result["log"] and "is still there" in result["log"]
    assert "result: FAILED" in result["log"]


def test_an_interrupted_run_still_drops_what_it_created(run) -> None:
    proc = run.start("interrupt")
    deadline = time.monotonic() + 20
    while not (run.state / "ready").exists():
        assert time.monotonic() < deadline and proc.poll() is None, "mock never got going"
        time.sleep(0.05)
    proc.send_signal(signal.SIGTERM)
    result = run.finish(proc)
    assert result["rc"] == 130
    assert result["drops"].strip() == "DROP USER K7Q_OWNER CASCADE;"
    assert result["existing"] == []
    assert "result: INTERRUPTED" in result["log"]


@pytest.mark.parametrize("compiler_exit", [0, 1])
def test_optional_reports_are_forwarded_and_compiler_failure_fails_the_run(
    run, tmp_path, compiler_exit
) -> None:
    compiler = tmp_path / "python-compiler"
    calls = tmp_path / "compiler-calls"
    compiler.write_text(
        '#!/bin/bash\nprintf "%s\\n" "$@" >> "$COMPILER_CALLS"\nexit "$COMPILER_EXIT"\n'
    )
    compiler.chmod(0o755)
    proc = run.start(
        "normal",
        reports=("holdout-one.json", "holdout-two.json"),
        PYTHON=str(compiler),
        COMPILER_CALLS=str(calls),
        COMPILER_EXIT=str(compiler_exit),
    )
    result = run.finish(proc)
    assert result["rc"] == compiler_exit
    assert result["existing"] == []
    forwarded = calls.read_text()
    assert "compile_proposals.py\nholdout-one.json\ndb:1521/svc\n" in forwarded
    assert "holdout-two.json" in forwarded
    assert PASSWORD not in forwarded
    assert "result: PASSED - 68/68" in result["log"]
