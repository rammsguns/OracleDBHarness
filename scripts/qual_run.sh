#!/usr/bin/env bash
# Run the Oracle qualification, integration and end-to-end suites from one profile.
#
#   scripts/qual_run.sh <profile> [pytest args...]
#
# <profile> names databases/qual/<profile>.env (git-ignored; see
# databases.example/qual/profile.env.example for the shape). The profile holds only
# HARNESS_QUAL_* variables. Passwords stay in files it points at, never in the profile.
# Inside the profile, $REPO is the repository root.
#
# Without pytest args the three suites the switch in tests/oracle_config.py covers are
# run. The report goes to databases/reports/<date>-<profile>.md unless the profile sets
# HARNESS_QUAL_REPORT.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export REPO

if [[ $# -lt 1 ]]; then
  echo "usage: $0 <profile> [pytest args...]" >&2
  exit 2
fi
profile="$1"
shift
env_file="$REPO/databases/qual/$profile.env"
if [[ ! -f "$env_file" ]]; then
  echo "No profile at $env_file" >&2
  exit 2
fi

# Start from a clean slate so a variable left in the shell cannot leak into the run.
while IFS= read -r name; do unset "$name"; done < <(compgen -e | grep '^HARNESS_QUAL_' || true)

set -a
# shellcheck disable=SC1090
source "$env_file"
set +a

if [[ -z "${HARNESS_QUAL_ORACLE_DSN:-}" ]]; then
  echo "$env_file does not set HARNESS_QUAL_ORACLE_DSN" >&2
  exit 2
fi

# G5 can isolate the staging twin and retain evidence outside historical reports.
while [[ $# -gt 0 ]]; do
  case "$1" in
    --report-path) export HARNESS_QUAL_REPORT="$2"; shift 2 ;;
    --d1-only)
      if [[ "$profile" != r1 && "$profile" != r3 ]]; then
        echo "--d1-only requires r1 or r3" >&2; exit 2
      fi
      if [[ "$HARNESS_QUAL_ORACLE_DSN" != localhost:1522/ORCL || "${HARNESS_QUAL_ADMIN_DSN:-}" != localhost:1522/ORCL ]]; then
        echo "D1 DSN differs; refusing qualification" >&2; exit 2
      fi
      while IFS= read -r name; do unset "$name"; done < <(compgen -v | grep '^HARNESS_QUAL_SECOND_' || true)
      export HARNESS_QUAL_REQUIRE="${HARNESS_QUAL_REQUIRE//second_target,/}"
      shift ;;
    *) break ;;
  esac
done

mkdir -p "$REPO/databases/reports"
export HARNESS_QUAL_REPORT="${HARNESS_QUAL_REPORT:-$REPO/databases/reports/$(date +%F)-$profile.md}"

if [[ $# -eq 0 ]]; then
  set -- tests/qualification tests/integration tests/e2e
fi

echo "profile:  $profile ($HARNESS_QUAL_ORACLE_DSN, ${HARNESS_QUAL_DRIVER_MODE:-thin})"
echo "requires: ${HARNESS_QUAL_REQUIRE:-<none>}"
echo "report:   $HARNESS_QUAL_REPORT"

cd "$REPO"
exec uv run --extra oracle pytest "$@"
