"""copilot-eval/compile_proposals.py against a mock sqlplus (round-14 review, C17).

The real compiler needs a disposable Oracle database and is run by hand (its logs are in
databases/reports). These tests cover what the tool itself decides: it drops K7P_APP only
when its own run printed the preflight and creation markers, so a pre-existing account
survives; it still cleans up after a failed setup, a timeout or an interruption; it fails
when a proposal does not compile, a case reports no result, setup did not finish or
cleanup left the account; it lists skipped and answer-only SQL as not checked; and the
passwords reach neither the log nor any process's arguments.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TOOL = REPO_ROOT / "copilot-eval" / "compile_proposals.py"
PASSWORD = "Not-A-Real-Pw_77"

# Dispatches on stdin: the main script carries the K7P-PREFLIGHT-CLEAR prompt, the cleanup
# script the REMAINING query. State in $MOCK_DIR: existing (K7P_APP exists), drops (DROP
# lines received), argv (every call's arguments).
MOCK = r"""#!/usr/bin/env bash
input="$(cat)"
printf '%s\n' "$*" >>"$MOCK_DIR/argv"
ids() { grep -o "PUT_LINE('CASE [^:]*" <<<"$input" | sed "s/PUT_LINE('CASE //"; }
case "$input" in
  *"PROMPT K7P-PREFLIGHT-CLEAR"*)
    if [[ $MOCK_SCENARIO == collision ]]; then
      touch "$MOCK_DIR/existing"
      echo "ORA-20000: K7P-COLLISION K7P_APP already exists; not touching it"; exit 1
    fi
    echo K7P-PREFLIGHT-CLEAR
    if [[ $MOCK_SCENARIO == unmarked ]]; then touch "$MOCK_DIR/existing"; exit 1; fi
    touch "$MOCK_DIR/existing"; echo "K7P-CREATED K7P_APP"
    case "$MOCK_SCENARIO" in
      partial) echo "ORA-01031: insufficient privileges"; exit 1 ;;
      timeout) exec sleep 30 ;;
      interrupt) touch "$MOCK_DIR/ready"; exec sleep 30 ;;
    esac
    echo K7P-SETUP-DONE
    first=1
    for id in $(ids); do
      if [[ $MOCK_SCENARIO == caseerror && $first == 1 ]]; then
        echo "CASE $id: ERROR 21:25 PL/SQL: ORA-00947: not enough values"
      elif [[ $MOCK_SCENARIO == missing && $first == 1 ]]; then
        :
      else
        echo "CASE $id: COMPILED"
      fi
      first=0
    done
    if grep -q "PROBE FIX-03 ONE" <<<"$input"; then
      own="raised -20001 ORA-20001: Multiple employees match last_name"
      echo "PROBE FIX-03 ONE: returned 100"; echo "PROBE FIX-03 NONE: returned NULL"
      echo "PROBE FIX-03 EQ: returned 200"; echo "PROBE FIX-03 ALLNULL: $own"
      case "$MOCK_SCENARIO" in
        probeunrelated)
          # Codex's round-15 control: a numeric/value error is not duplicate handling.
          bad="raised -6502 ORA-06502: PL/SQL: numeric or value error"
          echo "PROBE FIX-03 NEQ: $bad"; echo "PROBE FIX-03 MIX: $bad" ;;
        probeother)
          echo "PROBE FIX-03 NEQ: raised -20002 ORA-20002: another error"
          echo "PROBE FIX-03 MIX: $own" ;;
        *) echo "PROBE FIX-03 NEQ: $own" ;;
      esac
      if [[ $MOCK_SCENARIO == probefail ]]; then echo "PROBE FIX-03 MIX: returned 5000"
      elif [[ $MOCK_SCENARIO == probeok ]]; then echo "PROBE FIX-03 MIX: $own"; fi
    fi
    echo K7P-CASES-DONE ;;
  *REMAINING*)
    if grep -q '^DROP USER' <<<"$input"; then
      grep '^DROP USER' <<<"$input" >>"$MOCK_DIR/drops"
      if [[ $MOCK_SCENARIO == dropfail ]]; then
        echo "ORA-01940: cannot drop a user who is currently connected"
      else
        rm -f "$MOCK_DIR/existing"; echo "User dropped."
      fi
    fi
    if [[ -e $MOCK_DIR/existing ]]; then echo "REMAINING: 1"; else echo "REMAINING: 0"; fi ;;
esac
exit 0
"""

REPORT = {
    "cases": [
        {
            "id": "UNIT-1",
            "answer": "",
            "proposal": {
                "proposedText": "CREATE OR REPLACE PROCEDURE harness_app.p IS BEGIN NULL; END p;"
            },
        },
        {"id": "QUERY-1", "answer": "", "proposal": {"proposedText": "SELECT 1 FROM dual;"}},
        {"id": "GRANT-1", "answer": "", "proposal": {"proposedText": "GRANT SELECT ON t TO u"}},
        {
            "id": "KIWI-1",
            "answer": "Remedy:\n```sql\nCREATE OR REPLACE PACKAGE BODY x AS END x;\n```",
            "proposal": None,
        },
        {"id": "TEXT-1", "answer": "No SQL here.", "proposal": None},
    ]
}


FIX_03 = {
    "id": "FIX-03",
    "answer": "",
    "proposal": {
        "proposedText": (
            "CREATE OR REPLACE FUNCTION salary_for(p_last_name IN VARCHAR2) RETURN NUMBER IS "
            "BEGIN RETURN NULL; EXCEPTION WHEN TOO_MANY_ROWS THEN "
            "RAISE_APPLICATION_ERROR(\n  -20001, 'Multiple employees match'); END salary_for;"
        )
    },
}


@pytest.fixture
def run(tmp_path: Path):
    mock = tmp_path / "sqlplus"
    mock.write_text(MOCK)
    mock.chmod(0o755)
    pwfile = tmp_path / "pw"
    pwfile.write_text(PASSWORD + "\n")
    report = tmp_path / "report.json"
    report.write_text(json.dumps(REPORT))
    state = tmp_path / "state"
    state.mkdir()
    logs = tmp_path / "logs"

    def start(scenario: str, existing: bool = False, **env_extra: str) -> subprocess.Popen:
        if scenario.startswith("probe"):
            report.write_text(json.dumps({"cases": [*REPORT["cases"], FIX_03]}))
        if existing:
            (state / "existing").touch()
        env = {
            **os.environ,
            "SQLPLUS": str(mock),
            "MOCK_DIR": str(state),
            "MOCK_SCENARIO": scenario,
            "K7P_LOG_DIR": str(logs),
            "K7P_CLEANUP_RETRY_SECONDS": "0",
            **env_extra,
        }
        return subprocess.Popen(
            [sys.executable, "-I", str(TOOL), str(report), "db:1521/X", str(pwfile)],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

    def finish(proc: subprocess.Popen) -> dict:
        out, _ = proc.communicate(timeout=60)
        log_files = sorted(logs.glob("k7-proposal-compile-*.log"))
        return {
            "code": proc.returncode,
            "out": out,
            "log": log_files[-1].read_text() if log_files else "",
            "drops": (state / "drops").read_text() if (state / "drops").exists() else "",
            "argv": (state / "argv").read_text() if (state / "argv").exists() else "",
            "exists": (state / "existing").exists(),
        }

    def go(scenario: str, existing: bool = False, **env_extra: str) -> dict:
        return finish(start(scenario, existing, **env_extra))

    go.start = start  # type: ignore[attr-defined]
    go.finish = finish  # type: ignore[attr-defined]
    go.state = state  # type: ignore[attr-defined]
    return go


def _no_secret(result: dict) -> None:
    for where in ("out", "log", "argv"):
        assert PASSWORD not in result[where], where
    assert "K7p_" not in result["log"] and "K7p_" not in result["argv"]


def test_a_clean_run_passes_and_lists_what_it_did_not_check(run) -> None:
    result = run("ok")
    assert result["code"] == 0, result["out"]
    assert "result: PASSED" in result["log"]
    assert "CASE UNIT-1: COMPILED" in result["log"]
    assert "CASE QUERY-1: COMPILED" in result["log"]
    assert "CASE GRANT-1: SKIPPED" in result["log"] and "(not checked)" in result["log"]
    assert "CASE KIWI-1: NOT CHECKED 1 SQL block(s) in the answer" in result["log"]
    assert "CASE TEXT-1: NO SQL" in result["log"]
    assert "NOT CHECKED 1" in result["log"] and "NO SQL 1" in result["log"]
    assert "no other execution, no case grounding" in result["log"]
    assert "DROP USER K7P_APP CASCADE" in result["drops"]
    assert not result["exists"]
    _no_secret(result)


def test_a_proposal_that_does_not_compile_fails_the_run(run) -> None:
    # Codex reproduced exit 0 with a CASE ... ERROR line (round 14).
    result = run("caseerror")
    assert result["code"] == 1
    assert "ORA-00947" in result["log"] and "did not compile or parse" in result["log"]
    assert not result["exists"]


def test_a_case_with_no_result_fails_the_run(run) -> None:
    result = run("missing")
    assert result["code"] == 1
    assert "CASE UNIT-1: MISSING" in result["log"]
    assert "UNIT-1 reported no result" in result["log"]


def test_a_failed_setup_still_drops_the_account_it_created(run) -> None:
    result = run("partial")
    assert result["code"] == 1
    assert "setup did not complete" in result["log"]
    assert "DROP USER K7P_APP CASCADE" in result["drops"]
    assert not result["exists"]
    _no_secret(result)


def test_a_pre_existing_account_is_refused_and_never_dropped(run) -> None:
    result = run("collision", existing=True)
    assert result["code"] == 4
    assert "REFUSED" in result["log"]
    assert result["drops"] == ""
    assert result["exists"]


def test_an_account_without_a_creation_record_is_reported_not_dropped(run) -> None:
    result = run("unmarked")
    assert result["code"] == 1
    assert "exists without a creation record" in result["log"]
    assert result["drops"] == ""
    assert result["exists"]


def test_a_timeout_cleans_up_and_fails(run) -> None:
    result = run("timeout", K7P_RUN_TIMEOUT="1")
    assert result["code"] == 1
    assert "timed out" in result["log"]
    assert "DROP USER K7P_APP CASCADE" in result["drops"]
    assert not result["exists"]


def test_an_interruption_cleans_up(run) -> None:
    proc = run.start("interrupt")
    deadline = time.monotonic() + 20
    while not (run.state / "ready").exists():
        assert time.monotonic() < deadline, "the mock never started"
        time.sleep(0.05)
    proc.send_signal(signal.SIGTERM)
    result = run.finish(proc)
    assert result["code"] == 130
    assert "INTERRUPTED" in result["log"]
    assert "DROP USER K7P_APP CASCADE" in result["drops"]
    assert not result["exists"]
    _no_secret(result)


def test_a_failed_cleanup_fails_the_run(run) -> None:
    result = run("dropfail")
    assert result["code"] == 1
    assert "created by this run and is still there" in result["log"]
    assert result["drops"].count("DROP USER") == 3  # retried, then reported


def test_fix_03_behaviour_is_probed_and_a_silent_pick_fails(run) -> None:
    # Round 14 (C08): compiling is not behaviour. NULL beside 5000 must not return 5000.
    good = run("probeok")
    assert good["code"] == 0, good["out"]
    assert (
        "PROBE FIX-03 MIX: raised -20001 ORA-20001: Multiple employees match last_name "
        "(required deliberate: ok)"
    ) in good["log"]
    assert "PROBE FIX-03 EQ: returned 200 (recorded)" in good["log"]


def test_a_probe_that_returns_a_value_for_mixed_nulls_fails(run) -> None:
    bad = run("probefail")
    assert bad["code"] == 1
    assert (
        "PROBE FIX-03 MIX: returned 5000 (required deliberate: FAILED, returned a value "
        "where an error was required)"
    ) in bad["log"]


def test_a_probe_that_reports_nothing_fails(run) -> None:
    result = run("probemissing")
    assert result["code"] == 1
    assert "PROBE FIX-03 MIX: MISSING" in result["log"]


def test_an_unrelated_exception_is_not_deliberate_handling(run) -> None:
    # Round 15 (C17): Codex showed -6502 on NEQ and MIX was marked ok.
    result = run("probeunrelated")
    assert result["code"] == 1
    assert (
        "PROBE FIX-03 NEQ: raised -6502 ORA-06502: PL/SQL: numeric or value error "
        "(required deliberate: FAILED, unrelated exception; the proposal raises only "
        "-20001)"
    ) in result["log"]
    assert "FIX-03 probe MIX" in result["log"]


def test_an_application_error_the_proposal_does_not_raise_fails(run) -> None:
    result = run("probeother")
    assert result["code"] == 1
    assert (
        "NEQ: raised -20002 ORA-20002: another error (required deliberate: FAILED"
        in (result["log"])
    )
