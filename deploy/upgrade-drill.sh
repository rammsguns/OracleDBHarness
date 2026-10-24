#!/usr/bin/env bash
#
# Upgrade drill with retained data against a disposable PostgreSQL instance.
#
# Tests migrating the metadata store from an older release baseline (defaulting to
# initial commit c31c97e at schema version 3, falling back to 530e9c2 at schema version 4
# if the oldest commit cannot run) up to the current schema version (version 5).
#
# Asserts that:
#   * Every pre-upgrade table and row is retained with identifying values intact.
#   * New tables (store_owner, execution_runtimes, copilot_tool_calls, proposed_edit_parts)
#     are created.
#   * New columns (executions.owner_id, executions.dispatched_at, worksheet_sessions.owner_id,
#     worksheet_sessions.commit_requested_at, connection_profiles.wallet_secret_reference_id)
#     are added with safe defaults.
#   * store_owner singleton lock row (id=1, runtime_id="") exists.
#   * Second execution of initialize_schema is completely idempotent (no-op).
#
# What it does NOT cover, deliberately:
#   * Identity: uses throwaway accounts rather than an external OIDC provider.
#   * Oracle: no live Oracle database is contacted; store continuity is what is under test.
#   * Secret provisioning: uses throwaway passwords and secret references.
#
# Usage:  deploy/upgrade-drill.sh [--keep] [--from <commit>]
#         --keep           leave the postgres container and snapshot files up on completion
#         --from <commit>  baseline commit to seed data from (default: c31c97e)

set -euo pipefail

START_SECONDS=$SECONDS

KEEP=no
FROM_COMMIT="c31c97e"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --keep)
      KEEP=yes
      shift
      ;;
    --from)
      [[ $# -ge 2 ]] || { printf 'Error: --from requires an argument\n' >&2; exit 1; }
      FROM_COMMIT="$2"
      shift 2
      ;;
    --from=*)
      FROM_COMMIT="${1#*=}"
      shift
      ;;
    -h|--help)
      cat <<EOF
Usage: deploy/upgrade-drill.sh [--keep] [--from <commit>]

Runs an end-to-end upgrade drill with retained data against disposable PostgreSQL:
  --keep           leave the postgres container and snapshot files up on completion
  --from <commit>  baseline commit to seed data from (default: c31c97e)
EOF
      exit 0
      ;;
    *)
      printf 'Error: unrecognized option %s\n' "$1" >&2
      exit 1
      ;;
  esac
done

say() { printf '\n== %s\n' "$*"; }
fail() { printf '\nFAILED: %s\n' "$*" >&2; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MAIN_REPO="$(cd "$SCRIPT_DIR/.." && pwd)"

export UV_CACHE_DIR="${UV_CACHE_DIR:-/tmp/oracledbharness-uv-cache}"
export UV_LINK_MODE="${UV_LINK_MODE:-copy}"
mkdir -p "$UV_CACHE_DIR"

WORK="$(mktemp -d /tmp/upgrade-drill-XXXXXX)"
OLD_WORKTREE="$WORK/old"
SNAPSHOT_PRE="$WORK/snapshot-pre-upgrade.json"
SNAPSHOT_POST="$WORK/snapshot-post-upgrade.json"
PASS_FILE="$WORK/postgres_password"

CONTAINER_NAME="harness-upgrade-drill-$$-${RANDOM}"
PG_PORT="$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')"

cleanup() {
  local status=$?
  if [[ $status -ne 0 ]]; then
    say "the upgrade drill failed"
    if [[ -n "${CONTAINER_NAME:-}" ]] && docker inspect "$CONTAINER_NAME" >/dev/null 2>&1; then
      say "container logs ($CONTAINER_NAME) follow"
      docker logs --tail 100 "$CONTAINER_NAME" 2>&1 || true
    fi
  fi

  if [[ -d "$OLD_WORKTREE" ]]; then
    say "removing temporary worktree"
    git -C "$MAIN_REPO" worktree remove --force "$OLD_WORKTREE" >/dev/null 2>&1 || true
    rm -rf "$OLD_WORKTREE" 2>/dev/null || true
  fi

  if [[ "$KEEP" == yes ]]; then
    say "leaving container $CONTAINER_NAME running on 127.0.0.1:$PG_PORT (--keep)"
    say "work directory and snapshots preserved at $WORK"
    return $status
  fi

  say "tearing down container and temporary files"
  if [[ -n "${CONTAINER_NAME:-}" ]]; then
    docker stop "$CONTAINER_NAME" >/dev/null 2>&1 || true
    docker rm -v "$CONTAINER_NAME" >/dev/null 2>&1 || true
  fi
  rm -rf "$WORK"
  return $status
}
trap cleanup EXIT

# -- 1. generate credentials & start disposable postgres --------------------------------

say "generating database credentials"
python3 -c 'import secrets; print(secrets.token_urlsafe(24), end="")' > "$PASS_FILE"
chmod 600 "$PASS_FILE"
PG_PASSWORD="$(cat "$PASS_FILE")"

say "starting disposable postgres:16-alpine container ($CONTAINER_NAME on port $PG_PORT)"
docker run -d \
  --name "$CONTAINER_NAME" \
  -e POSTGRES_USER=harness \
  -e POSTGRES_PASSWORD_FILE=/run/secrets/pg_password \
  -e POSTGRES_DB=harness \
  -v "$PASS_FILE:/run/secrets/pg_password:ro" \
  -p "127.0.0.1:${PG_PORT}:5432" \
  postgres:16-alpine >/dev/null

say "waiting for PostgreSQL to accept connections"
ready=0
for _ in $(seq 1 30); do
  if docker exec "$CONTAINER_NAME" pg_isready -U harness -d harness >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 1
done
[[ $ready -eq 1 ]] || fail "PostgreSQL container did not become ready in 30s"

PG_URL="postgresql+psycopg://harness:${PG_PASSWORD}@127.0.0.1:${PG_PORT}/harness"

# -- 2. checkout baseline worktree & seed with baseline code ----------------------------

say "checking out baseline commit $FROM_COMMIT"
git -C "$MAIN_REPO" worktree add --detach "$OLD_WORKTREE" "$FROM_COMMIT"

run_old_seed() {
  local target_wt="$1"
  local target_url="$2"
  (
    cd "$target_wt"
    if uv run --package harness-api --extra postgres python -c "import psycopg" >/dev/null 2>&1; then
      uv run --package harness-api --extra postgres \
        python "$MAIN_REPO/deploy/upgrade_drill_helper.py" seed --db-url "$target_url"
    elif uv run --extra postgres python -c "import psycopg" >/dev/null 2>&1; then
      uv run --extra postgres \
        python "$MAIN_REPO/deploy/upgrade_drill_helper.py" seed --db-url "$target_url"
    else
      uv run --with "psycopg[binary]>=3.1" \
        python "$MAIN_REPO/deploy/upgrade_drill_helper.py" seed --db-url "$target_url"
    fi
  )
}

EXERCISED_BASELINE="$FROM_COMMIT"
EXPECTED_OLD_VERSION="3"
if [[ "$FROM_COMMIT" == "530e9c2"* ]]; then
  EXPECTED_OLD_VERSION="4"
fi

say "initializing and seeding store using baseline code ($EXERCISED_BASELINE)"
if ! run_old_seed "$OLD_WORKTREE" "$PG_URL"; then
  if [[ "$FROM_COMMIT" == "c31c97e"* ]]; then
    say "baseline $FROM_COMMIT failed to run against Postgres (dependency drift or environment); falling back to schema 4 baseline (530e9c2)"
    git -C "$MAIN_REPO" worktree remove --force "$OLD_WORKTREE" >/dev/null 2>&1 || true
    rm -rf "$OLD_WORKTREE" 2>/dev/null || true
    git -C "$MAIN_REPO" worktree add --detach "$OLD_WORKTREE" 530e9c2

    # Wipe database to start clean with fallback baseline
    docker exec "$CONTAINER_NAME" psql -U harness -d postgres -q -c "DROP DATABASE IF EXISTS harness; CREATE DATABASE harness;" >/dev/null

    EXERCISED_BASELINE="530e9c2"
    EXPECTED_OLD_VERSION="4"
    run_old_seed "$OLD_WORKTREE" "$PG_URL" || fail "fallback baseline $EXERCISED_BASELINE also failed to seed"
  else
    fail "baseline $FROM_COMMIT failed to seed"
  fi
fi

# -- 3. record pre-upgrade state and row counts ----------------------------------------

say "recording pre-upgrade state and row counts to $SNAPSHOT_PRE"
(
  cd "$MAIN_REPO"
  uv run --extra postgres \
    python deploy/upgrade_drill_helper.py snapshot --db-url "$PG_URL" --output "$SNAPSHOT_PRE"
)
cat "$SNAPSHOT_PRE"

say "verifying schema_version matches expected baseline version ($EXPECTED_OLD_VERSION)"
(
  cd "$MAIN_REPO"
  uv run --extra postgres \
    python deploy/upgrade_drill_helper.py verify-version --db-url "$PG_URL" --expected "$EXPECTED_OLD_VERSION"
)

# -- 4. run initialize_schema with main code -------------------------------------------

say "running initialize_schema from current worktree (main code) against the store"
(
  cd "$MAIN_REPO"
  uv run --extra postgres \
    python deploy/upgrade_drill_helper.py upgrade --db-url "$PG_URL"
)

say "verifying upgrade results against pre-upgrade snapshot (schema v5, retained rows, new tables/columns)"
(
  cd "$MAIN_REPO"
  uv run --extra postgres \
    python deploy/upgrade_drill_helper.py verify --db-url "$PG_URL" --snapshot "$SNAPSHOT_PRE" --expected-version "5"
)

# -- 5. verify idempotency on a second run ---------------------------------------------

say "recording post-upgrade state to $SNAPSHOT_POST"
(
  cd "$MAIN_REPO"
  uv run --extra postgres \
    python deploy/upgrade_drill_helper.py snapshot --db-url "$PG_URL" --output "$SNAPSHOT_POST"
)

say "running initialize_schema a second time to verify idempotency (no-op)"
(
  cd "$MAIN_REPO"
  uv run --extra postgres \
    python deploy/upgrade_drill_helper.py verify-idempotent --db-url "$PG_URL" --snapshot "$SNAPSHOT_POST"
)

# -- 6. summary ------------------------------------------------------------------------

say "upgrade drill passed"
PRE_TABLES="$(python3 -c "import json; d=json.load(open('$SNAPSHOT_PRE')); print(len(d.get('table_counts', {})))")"
POST_TABLES="$(python3 -c "import json; d=json.load(open('$SNAPSHOT_POST')); print(len(d.get('table_counts', {})))")"
PRE_ROWS="$(python3 -c "import json; d=json.load(open('$SNAPSHOT_PRE')); print(sum(d.get('table_counts', {}).values()))")"

cat <<SUMMARY

== upgrade drill passed

  baseline commit                  ${EXERCISED_BASELINE} (schema v${EXPECTED_OLD_VERSION})
  target schema version            5
  database container               ${CONTAINER_NAME} (127.0.0.1:${PG_PORT})
  pre-upgrade tables               ${PRE_TABLES}
  post-upgrade tables              ${POST_TABLES}
  pre-upgrade rows retained        ${PRE_ROWS} (100% match)
  idempotency check                passed (zero diff on repeat run)
  drill duration                   $((SECONDS - START_SECONDS))s

  Not covered: identity (placeholder issuer), Oracle connectivity (no target), and
  secret provisioning, which is the manual part of a real install.
SUMMARY
