#!/usr/bin/env bash
#
# Install the pilot deployment from nothing, back the metadata store up, restore it into a
# fresh database, and bring the deployment up on the restored copy.
#
# One of the release criteria is demonstrating a restore rather than only a dump, so this is
# the drill, executable rather than described. It runs in CI on a clean runner
# (.github/workflows/ci.yml, the "Compose install and restore" job) and an operator can run
# the same script before a pilot. Every step prints what it did and the script fails on the
# first thing that does not hold.
#
# What it does NOT cover, deliberately:
#
#   * Identity. The documented pilot configuration authenticates through a real OIDC
#     provider, which this script does not have, so it uses a placeholder issuer and never
#     signs anybody in. Discovery is lazy, so the deployment starts and serves its unauthenticated
#     endpoints; authenticated calls would be refused. Provider-facing code is qualified by
#     the Keycloak job instead. See NP-06 in NEXT_PHASE_PLAN.md.
#   * Oracle. No target database is reachable here, so the seed registers profiles without
#     probing them. Store continuity is what is under test.
#   * Secret provisioning. The files are created here with throwaway values, which is the
#     one part of a real install that has to be done by hand. See deploy/secrets/README.md.
#
# Usage:  deploy/backup-restore-drill.sh [--keep]
#         --keep  leave the stack running afterwards instead of tearing it down.

set -euo pipefail

KEEP=no
if [[ "${1:-}" == "--keep" ]]; then
  KEEP=yes
fi

cd "$(dirname "$0")"
WORK="$(mktemp -d)"
DUMP="$WORK/harness-metadata.sql"
RESTORED_DB=harness_restored
# Files this run creates, and removes again at the end. The drill writes throwaway
# credentials, so it must neither overwrite nor leave behind anything of an operator's.
WROTE=()

say() { printf '\n== %s\n' "$*"; }
fail() { printf '\nFAILED: %s\n' "$*" >&2; exit 1; }

# docker inspect rather than `docker compose ps --format {{.Health}}`: the latter's field
# names have moved between Compose releases, and this script is meant to run on whatever an
# operator happens to have.
wait_for_healthy_api() {
  local id status
  for _ in $(seq 1 60); do
    id="$(docker compose ps -q api 2>/dev/null || true)"
    if [[ -n "$id" ]]; then
      status="$(docker inspect --format '{{.State.Health.Status}}' "$id" 2>/dev/null || true)"
      [[ "$status" == healthy ]] && return 0
      [[ "$status" == unhealthy ]] && return 1
    fi
    sleep 2
  done
  return 1
}

cleanup() {
  local status=$?
  # Before anything is removed. `docker compose down` takes the containers with it, so a
  # log-collecting step afterwards would find nothing left to read: whatever explains a
  # failure has to be captured here, while it still exists.
  if [[ $status -ne 0 ]]; then
    say "the drill failed; compose logs follow"
    docker compose logs --no-color --timestamps || true
  fi
  if [[ "$KEEP" == yes ]]; then
    say "leaving the stack running (--keep); metadata dump is at $DUMP"
    return $status
  fi
  say "tearing down"
  # -v removes the metadata volume: this is a drill, and leaving a half-restored store
  # behind would make the next run start from something other than nothing.
  docker compose down -v --remove-orphans >/dev/null 2>&1 || true
  if [[ ${#WROTE[@]} -gt 0 ]]; then
    rm -f "${WROTE[@]}"
  fi
  rm -rf "$WORK"
  return $status
}
trap cleanup EXIT

# -- 1. install, exactly as docs/setup.md describes ------------------------------------

say "checking this checkout has nothing of its own to lose"
# The drill writes throwaway credentials and a throwaway .env. Doing that over an operator's
# real ones would leave the deployment's PostgreSQL, Oracle and provider credentials replaced
# with rubbish, and they would not find out until the next real start. Refuse instead.
EXISTING=()
for candidate in .env secrets/postgres_password secrets/oracle_app.password \
                 secrets/provider_api_key; do
  if [[ -e "$candidate" ]]; then
    EXISTING+=("deploy/$candidate")
  fi
done
if [[ ${#EXISTING[@]} -gt 0 ]]; then
  printf '\nThese exist already, and this drill would overwrite them with throwaway values:\n' >&2
  printf '  %s\n' "${EXISTING[@]}" >&2
  fail "move them aside, or run the drill on a fresh checkout"
fi

say "creating the secret files (deploy/secrets/README.md)"
printf '%s' 'drill-postgres-password' > secrets/postgres_password
printf '%s' 'drill-oracle-password'   > secrets/oracle_app.password
printf '%s' 'drill-provider-key'      > secrets/provider_api_key
WROTE+=(secrets/postgres_password secrets/oracle_app.password secrets/provider_api_key)
# 700 on the directory, 644 on the files, for the reason deploy/secrets/README.md gives: the
# API runs as uid 10001 and reads the mounted secret itself, so a mode that only the
# operator can read stops it starting. The directory is what keeps other host users out.
chmod 700 secrets
chmod 644 secrets/postgres_password secrets/oracle_app.password secrets/provider_api_key

say "writing deploy/.env"
cp .env.example .env
WROTE+=(.env)
# A placeholder issuer. The API never contacts it in this drill; see the header.
{
  # The seeded demonstration profiles point at localhost:1521, so allow that and nothing
  # else: an install whose allowlist contradicts its own profiles is not a clean install.
  echo "HARNESS_ALLOWED_ENDPOINTS=localhost:1521"
  echo "HARNESS_OIDC_ISSUER=https://login.invalid/realms/drill"
  echo "HARNESS_OIDC_JWKS_URL=https://login.invalid/realms/drill/protocol/openid-connect/certs"
} >> .env

say "docker compose up -d --build"
START_INSTALL=$SECONDS
docker compose up -d --build
INSTALL_SECONDS=$((SECONDS - START_INSTALL))

say "waiting for the API to report healthy"
if ! wait_for_healthy_api; then
  fail "the API never became healthy"
fi
echo "API healthy after ${INSTALL_SECONDS}s of build and start"

say "the console is served"
for attempt in $(seq 1 15); do
  if curl -fsS http://127.0.0.1:8080/ >/dev/null 2>&1; then
    break
  fi
  [[ "$attempt" == 15 ]] && fail "the console did not serve on 127.0.0.1:8080"
  sleep 2
done
# The console proxies /api to the API container, which is what lets them share an origin.
curl -fsS http://127.0.0.1:8080/api/v1/system/info >/dev/null   || fail "the console did not proxy /api to the API"

say "the store was created at the schema version this build expects"
INFO="$(curl -fsS http://127.0.0.1:8000/api/v1/system/info)"
echo "$INFO"
VERSION="$(printf '%s' "$INFO" | python3 -c 'import json,sys; print(json.load(sys.stdin)["metadataSchemaVersion"])')"
[[ -n "$VERSION" ]] || fail "no schema version reported"
echo "schema version: $VERSION"

# -- 2. put records in it --------------------------------------------------------------

say "seeding profiles, users and grants"
# --no-probe: no Oracle target is reachable from this drill, and a failed probe is not what
# is being tested.
#
# Two environment overrides, for this one command only. The running API sees neither.
#
# HARNESS_AUTH_MODE=dev because the seed refuses to run under any other mode: it creates
# demonstration accounts, and in a pilot those come from the identity provider through the
# admin API instead. A drill wants the demonstration accounts; the API beside it goes on
# using OIDC exactly as deployed.
#
# HARNESS_SECRET_DIR because the seed writes a placeholder password file for any target
# credential that does not exist yet, and /run/secrets is mounted read-only. The placeholders
# are worthless by design; the point of running the seed is that it writes profiles, users,
# grants and runbook definitions through the application's own code path into the real store
# rather than by hand. Nothing in this drill resolves a credential, so where they land does
# not matter.
docker compose exec -T \
  -e HARNESS_AUTH_MODE=dev \
  -e HARNESS_SECRET_DIR=/tmp/drill-secrets \
  api python -m harness_api.seed --no-probe

psql_store() {  # psql against the live store, quiet and tuple-only
  docker compose exec -T metadata psql -U harness -d "${1}" -At -c "${2}"
}

counts_for() {
  # Every table the store actually holds, read from the catalogue rather than from a list
  # kept here. A hardcoded list quietly stops covering whatever is added next, and this is
  # the check standing behind the words "no data loss", so it has to mean every table or
  # else claim less. A table present in one database and absent from the other shows up as
  # a differing line.
  local db="$1" table
  while read -r table; do
    if [[ -n "$table" ]]; then
      printf '%s=%s\n' "$table" "$(psql_store "$db" "SELECT count(*) FROM \"${table}\";")"
    fi
  done < <(psql_store "$db" "
    SELECT table_name FROM information_schema.tables
     WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
     ORDER BY table_name;")
}

say "recording an interrupted execution, so the restore is not of a tidy store"
# A restored store can hold work that was in flight when the backup was taken. Starting the
# API on it must reconcile that rather than fall over, which is the interesting case and one
# a dump of a tidy store would never produce.
#
# user_id and profile_id are selected rather than invented: both are foreign keys, and
# PostgreSQL enforces them.
psql_store harness "
  INSERT INTO executions
    (id, user_id, profile_id, operation_id, statement_kind, risk_class,
     statement_fingerprint, bind_names, limits_json, policy_decision, policy_reason,
     state, truncated, error_code, error_message, verification_json, owner_id,
     dispatched_at, started_at)
  SELECT 'exe_drill_in_flight', u.id, p.id, 'worksheet.execute', 'dml', 'persistent_write',
         repeat('a', 64), '[]', '{}', 'allowed', '', 'running', false, '', '', '{}',
         'rt_the_process_that_died', now(), now()
  FROM (SELECT id FROM users ORDER BY subject LIMIT 1) u,
       (SELECT id FROM connection_profiles ORDER BY name LIMIT 1) p;"
[[ "$(psql_store harness "SELECT count(*) FROM executions WHERE id = 'exe_drill_in_flight';")" == 1 ]] \
  || fail "could not record the interrupted execution"

say "counts before the backup"
BEFORE="$(counts_for harness)"
echo "$BEFORE"

# -- 3. back up ------------------------------------------------------------------------

say "pg_dump, as docs/setup.md documents"
START_DUMP=$SECONDS
docker compose exec -T metadata pg_dump -U harness harness > "$DUMP"
DUMP_SECONDS=$((SECONDS - START_DUMP))
[[ -s "$DUMP" ]] || fail "the dump is empty"
echo "dumped $(wc -c < "$DUMP") bytes in ${DUMP_SECONDS}s"

# -- 4. restore into a fresh database --------------------------------------------------

say "restoring into a fresh database ($RESTORED_DB)"
START_RESTORE=$SECONDS
psql_store postgres "CREATE DATABASE ${RESTORED_DB};" >/dev/null
docker compose exec -T metadata   psql -U harness -d "$RESTORED_DB" -q -v ON_ERROR_STOP=1 < "$DUMP" >/dev/null
RESTORE_SECONDS=$((SECONDS - START_RESTORE))
echo "restored in ${RESTORE_SECONDS}s"

say "counts after the restore"
AFTER="$(counts_for "$RESTORED_DB")"
echo "$AFTER"

if [[ "$BEFORE" != "$AFTER" ]]; then
  printf 'before:\n%s\nafter:\n%s\n' "$BEFORE" "$AFTER" >&2
  fail "the restored store does not hold what the original did"
fi
say "no data loss: every table in the store matches row for row"

say "the restored store's own contents, not just its row counts"
psql_store "$RESTORED_DB" \
  "SELECT name || ' worksheets=' || worksheets_enabled FROM connection_profiles ORDER BY name;"
psql_store "$RESTORED_DB" \
  "SELECT u.subject || ' -> ' || p.name || ' ' || g.permissions
     FROM user_target_grants g
     JOIN users u ON u.id = g.user_id
     JOIN connection_profiles p ON p.id = g.profile_id
    ORDER BY u.subject, p.name;"
# No password ever reaches the store, so a restore cannot leak one. Worth asserting on the
# restored copy, which is the artefact most likely to be handled casually.
psql_store "$RESTORED_DB" "SELECT name || ' -> ' || provider || ':' || locator
                             FROM secret_references ORDER BY name;"
grep -qiE "drill-postgres-password|drill-oracle-password|drill-provider-key" "$DUMP" \
  && fail "a secret value is present in the metadata dump" || true
say "the dump holds no secret values, only references to them"

# -- 5. bring the deployment up on the restored copy -----------------------------------

say "recreating the API against the restored database"
START_CUTOVER=$SECONDS
HARNESS_METADATA_URL="postgresql+psycopg://harness@metadata:5432/${RESTORED_DB}" \
  docker compose up -d --no-build --force-recreate api
if ! wait_for_healthy_api; then
  fail "the API did not come up on the restored store"
fi
CUTOVER_SECONDS=$((SECONDS - START_CUTOVER))
echo "serving on the restored store after ${CUTOVER_SECONDS}s"

say "the API accepted the restored store at the expected schema version"
curl -fsS http://127.0.0.1:8000/api/v1/system/info

say "and reconciled the execution that was in flight when the backup was taken"
STATE="$(psql_store "$RESTORED_DB" \
  "SELECT state FROM executions WHERE id = 'exe_drill_in_flight';")"
echo "exe_drill_in_flight is now: $STATE"
[[ "$STATE" == "outcome_unknown" ]] || \
  fail "expected the interrupted write to be reconciled to outcome_unknown, got '$STATE'"
docker compose logs api 2>&1 | grep -E "recovery:" || fail "startup logged no reconciliation"

# -- summary ---------------------------------------------------------------------------

cat <<SUMMARY

== drill passed

  clean install (build included)   ${INSTALL_SECONDS}s
  pg_dump                          ${DUMP_SECONDS}s
  restore into a fresh database    ${RESTORE_SECONDS}s
  API recreated on the restore     ${CUTOVER_SECONDS}s
  recovery time (dump excluded)    $((RESTORE_SECONDS + CUTOVER_SECONDS))s
  data loss                        none: every table matched row for row
  schema version                   ${VERSION}

  Not covered: identity (placeholder issuer), Oracle connectivity (no target), and
  secret provisioning, which is the manual part of a real install.
SUMMARY
