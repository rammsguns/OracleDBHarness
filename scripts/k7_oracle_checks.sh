#!/usr/bin/env bash
# Run the K-7 Oracle code-rule checks, clean up, and record the outcome.
#
#   scripts/k7_oracle_checks.sh <connect-id> <password-file> [user] [log-dir]
#
#   connect-id     host:port/service of a NON-PRODUCTION database
#   password-file  file holding the privileged account's password (never printed)
#   user           privileged account, default system
#   log-dir        default databases/reports
#
# The password reaches sqlplus on stdin and is scrubbed from output inside this shell, so
# it is never a process argument. Exit status is nonzero when any check failed, fewer or
# more than the expected checks reported, the database could not be reached, SQL failed,
# the accounts already existed, the run was interrupted, or cleanup failed or left
# anything behind.
#
# Cleanup needs proof of ownership. The check script prints K7Q-PREFLIGHT-CLEAR once it
# has confirmed that none of K7Q_OWNER, K7Q_USER and K7Q_READ exists, and K7Q-CREATED
# <name> after creating each one. Only names with both are dropped. Without the preflight
# line (no connection, missing privilege, a collision) nothing is dropped. A name that
# exists afterwards without a creation line is reported, never dropped. The log has the
# database version, the check counts, the full check output and the cleanup result, and
# no credentials.
set -uo pipefail

EXPECTED_CHECKS=66
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SQLPLUS="${SQLPLUS:-/opt/oracle/instantclient_21_4/sqlplus}"

if [[ $# -lt 2 ]]; then
  echo "usage: $0 <connect-id> <password-file> [user] [log-dir]" >&2
  exit 2
fi
connect="$1"
pwfile="$2"
user="${3:-system}"
logdir="${4:-$REPO/databases/reports}"
[[ -r "$pwfile" ]] || { echo "cannot read the password file" >&2; exit 2; }
[[ -x "$SQLPLUS" ]] || SQLPLUS="$(command -v sqlplus || true)"
[[ -n "$SQLPLUS" ]] || { echo "sqlplus not found (set SQLPLUS)" >&2; exit 2; }
mkdir -p "$logdir"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
log="$logdir/k7-oracle-checks-$stamp.log"
work="$(mktemp -d)"
password="$(<"$pwfile")"
password="${password%$'\n'}"

# Everything the log gets passes through this. Bash does the replacement, so the password
# is not handed to another program's command line.
scrub() {
  local text
  text="$(cat)"
  [[ -n "$password" ]] && text="${text//"$password"/<password>}"
  printf '%s\n' "$text"
}

child=""
# sql <output-file> <body...>: connect and run the body lines, in the background so that
# a signal is handled at once. Returns sqlplus's exit status.
sql() {
  local out="$1"
  shift
  { printf 'CONNECT %s/"%s"@%s\n' "$user" "$password" "$connect"; printf '%s\n' "$@"; } \
    | "$SQLPLUS" -S /nolog >"$out" 2>&1 &
  child=$!
  wait "$child"
  local rc=$?
  child=""
  return $rc
}

marked() { grep -q "^K7Q-CREATED $1\$" "$work/checks" 2>/dev/null; }
preflight_clear() { grep -q '^K7Q-PREFLIGHT-CLEAR' "$work/checks" 2>/dev/null; }

# Drops what this run created and checks what remains. Sets cleanup_problems.
cleanup_problems=()
cleaned=0
cleanup() {
  [[ $cleaned -eq 1 ]] && return
  cleaned=1
  cleanup_problems=()
  if ! preflight_clear; then
    echo "== cleanup: not attempted, the run never showed the names were free" >>"$log"
    return
  fi
  local drops=("WHENEVER SQLERROR CONTINUE" "SET FEEDBACK ON")
  local name dropped=()
  # Dependants first: the user holding the role, then the owner, then the role.
  for name in K7Q_USER K7Q_OWNER K7Q_READ; do
    marked "$name" || continue
    dropped+=("$name")
    if [[ $name == K7Q_READ ]]; then drops+=("DROP ROLE $name;"); else drops+=("DROP USER $name CASCADE;"); fi
  done
  drops+=("EXIT")
  if [[ ${#dropped[@]} -gt 0 ]]; then
    sql "$work/drop" "${drops[@]}"
    local rc=$?
    {
      echo "== cleanup: dropping ${dropped[*]}"
      scrub <"$work/drop"
    } >>"$log"
    [[ $rc -eq 0 ]] || cleanup_problems+=("cleanup sqlplus exited $rc")
    grep -Eq '^ORA-[0-9]+|^SP2-[0-9]+|^ERROR' "$work/drop" \
      && cleanup_problems+=("a DROP failed")
  else
    echo "== cleanup: nothing was created" >>"$log"
  fi
  sql "$work/left" "SET HEADING OFF FEEDBACK OFF PAGESIZE 0" \
    "SELECT n FROM (SELECT username n FROM dba_users WHERE username IN ('K7Q_OWNER','K7Q_USER') UNION ALL SELECT role FROM dba_roles WHERE role = 'K7Q_READ');" \
    "EXIT"
  local rc=$?
  local remaining
  remaining="$(grep -Eo '^K7Q_[A-Z]+' "$work/left" | sort -u | tr '\n' ' ')"
  {
    echo "== remaining after cleanup: ${remaining:-none}"
  } >>"$log"
  if [[ $rc -ne 0 ]] || grep -Eq '^ORA-[0-9]+|^SP2-[0-9]+|^ERROR' "$work/left"; then
    cleanup_problems+=("could not confirm what remains")
    scrub <"$work/left" >>"$log"
  fi
  for name in $remaining; do
    if marked "$name"; then
      cleanup_problems+=("$name was created by this run and is still there")
    else
      cleanup_problems+=("$name exists without a creation record from this run; not dropped, check it by hand")
    fi
  done
}

finish() { rm -rf "$work"; }
on_signal() {
  trap - INT TERM
  [[ -n "$child" ]] && kill "$child" 2>/dev/null && wait "$child" 2>/dev/null
  echo "== interrupted" >>"$log"
  [[ -f "$work/checks" ]] && { echo "== check output so far"; scrub <"$work/checks"; } >>"$log"
  cleanup
  echo "result: INTERRUPTED - ${cleanup_problems[*]:-cleanup complete}" >>"$log"
  echo "INTERRUPTED: ${cleanup_problems[*]:-cleaned up} (see $log)" >&2
  finish
  exit 130
}
trap on_signal INT TERM
trap finish EXIT

{
  echo "K-7 Oracle checks $stamp"
  echo "connect: $connect  user: $user"
} >"$log"
sql "$work/version" "SET HEADING OFF FEEDBACK OFF PAGESIZE 0" "SELECT banner_full FROM v\$version;" "EXIT"
version="$(scrub <"$work/version")"
echo "version: $version" >>"$log"

if ! grep -qi 'oracle database' <<<"$version"; then
  echo "result: NOT RUN - could not connect or read the version; nothing changed" >>"$log"
  echo "FAILED: could not connect (see $log)" >&2
  exit 3
fi

sql "$work/checks" "@$REPO/oracle/qualification/05_k7_codegen_checks.sql $connect"
sql_status=$?
out="$(scrub <"$work/checks")"
{
  echo "== check output"
  echo "$out"
} >>"$log"

pass=$(grep -c '^PASS ' <<<"$out")
fail=$(grep -c '^FAIL ' <<<"$out")
errors=$(grep -Ec '^SP2-[0-9]+|ERROR at line|^ERROR:' <<<"$out")
collision=$(grep -c 'K7Q-COLLISION' <<<"$out")
done_marker=$(grep -c '^K7Q-DONE' <<<"$out")

if [[ $collision -gt 0 ]]; then
  # The names were not ours: nothing was created, so nothing is dropped.
  echo "result: REFUSED - K7Q_OWNER, K7Q_USER or K7Q_READ already exists; nothing changed, nothing dropped" >>"$log"
  echo "REFUSED: pre-existing K7Q accounts (see $log)" >&2
  exit 4
fi

cleanup

{
  echo "== summary"
  echo "checks passed: $pass  failed: $fail  expected: $EXPECTED_CHECKS"
  echo "sql errors: $errors  sqlplus exit: $sql_status  completed: $done_marker"
  echo "preflight clear: $(preflight_clear && echo yes || echo no)"
} >>"$log"

problems=()
preflight_clear || problems+=("the check script stopped before confirming the names were free")
[[ $fail -eq 0 ]] || problems+=("$fail check(s) failed")
[[ $pass -eq $EXPECTED_CHECKS ]] || problems+=("$pass checks passed, $EXPECTED_CHECKS expected")
[[ $errors -eq 0 ]] || problems+=("$errors SQL error line(s)")
[[ $sql_status -eq 0 ]] || problems+=("sqlplus exited $sql_status")
[[ $done_marker -ge 1 ]] || problems+=("the script did not finish")
problems+=("${cleanup_problems[@]}")

if [[ ${#problems[@]} -gt 0 ]]; then
  echo "result: FAILED - ${problems[*]}" >>"$log"
  echo "FAILED: ${problems[*]} (see $log)" >&2
  exit 1
fi
echo "result: PASSED - $pass/$EXPECTED_CHECKS checks, accounts removed" >>"$log"
echo "PASSED: $pass/$EXPECTED_CHECKS checks, cleaned up (see $log)"
