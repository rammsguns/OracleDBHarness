#!/usr/bin/env bash
#
# Stand-in deployed-origin identity qualification drill (PLAN A5 / NP-06).
#
# Deploys the compose stack behind its nginx proxy (deploy/compose.yaml and
# deploy/nginx.conf) with the Keycloak fixture realm (tests/identity/keycloak/compose.yaml)
# as an automated stand-in for the pilot IdP registration.
#
# Seeds one demonstration target ('development') granted to account A ('alice') only,
# leaving 'production' ungranted, and runs the browser qualification suite against the
# DEPLOYED origin through the proxy: callback, expiry, logout and authorization
# (refused identity with unregistered user 'bob', ungranted target 'production', direct API
# access without credentials).
#
# Plainly stated: this is an AI agent qualification on test stand-ins, NOT a human
# pilot qualification.
#
# Hostname resolution architecture:
# Keycloak calculates token issuer claims based on the Host header of requests it receives.
# Both the browser (running on the host) and the API container (inside Docker) must reach
# Keycloak at the exact same issuer URL: http://keycloak.localhost:8180/realms/engineering
#
# 1. Host side: RFC 6761 specifies that *.localhost always resolves to loopback (127.0.0.1)
#    without requiring any modifications to /etc/hosts.
# 2. Keycloak container: Bound to 8180:8080 on the host via an override compose file so that
#    requests arriving on host interfaces (including Docker bridge gateway) reach port 8080.
# 3. Docker container side: compose.override.yaml configures extra_hosts on api and web:
#      - keycloak.localhost:host-gateway
#      - nginx.localhost:host-gateway
#      - api.localhost:host-gateway
#    Inside the containers, keycloak.localhost resolves to the host's bridge gateway IP,
#    which reaches port 8180 on the host and forwards into Keycloak.
#
# Usage:  deploy/identity-standin-drill.sh [--report PATH] [--keep]
#         --report PATH  path to output report markdown (default under $TMPDIR)
#         --keep         leave the stack running afterwards instead of tearing it down

set -euo pipefail

KEEP=no
REPORT=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --keep)
      KEEP=yes
      shift
      ;;
    --report)
      REPORT="$2"
      shift 2
      ;;
    *)
      printf 'Unknown option: %s\n' "$1" >&2
      exit 1
      ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$SCRIPT_DIR"

WORK="$(mktemp -d "${TMPDIR:-/tmp}/harness-identity-drill-XXXXXX")"
chmod 700 "$WORK"
REPORT="${REPORT:-$WORK/harness-standin-report.md}"
KEYCLOAK_COMPOSE="$REPO_ROOT/tests/identity/keycloak/compose.yaml"

WROTE=()

say() { printf '\n== %s\n' "$*"; }
fail() { printf '\nFAILED: %s\n' "$*" >&2; exit 1; }

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
  if [[ $status -ne 0 ]]; then
    say "the drill failed; compose logs follow"
    docker compose -f compose.yaml logs --no-color --timestamps || true
    docker compose -f "$KEYCLOAK_COMPOSE" logs --no-color --timestamps || true
  fi
  if [[ "$KEEP" == yes ]]; then
    say "leaving the stack running (--keep); report is at $REPORT"
    return $status
  fi
  say "tearing down"
  docker compose -f compose.yaml down -v --remove-orphans >/dev/null 2>&1 || true
  docker compose -f "$KEYCLOAK_COMPOSE" down -v --remove-orphans >/dev/null 2>&1 || true
  if [[ ${#WROTE[@]} -gt 0 ]]; then
    rm -f "${WROTE[@]}"
  fi
  rm -rf "$WORK"
  return $status
}
trap cleanup EXIT

# -- 1. safety checks ------------------------------------------------------------------

say "checking this checkout has nothing of its own to lose"
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

# -- 2. throwaway secrets and configuration --------------------------------------------

say "creating the secret files (deploy/secrets/README.md)"
mkdir -p secrets
chmod 700 secrets
printf '%s' 'drill-postgres-password' > secrets/postgres_password
printf '%s' 'drill-oracle-password'   > secrets/oracle_app.password
printf '%s' 'drill-provider-key'      > secrets/provider_api_key
chmod 644 secrets/postgres_password secrets/oracle_app.password secrets/provider_api_key
WROTE+=(secrets/postgres_password secrets/oracle_app.password secrets/provider_api_key)

say "writing deploy/.env"
cp .env.example .env
WROTE+=(.env)
{
  echo "HARNESS_ALLOWED_ENDPOINTS=localhost:1521"
  echo "HARNESS_OIDC_ISSUER=http://keycloak.localhost:8180/realms/engineering"
  echo "HARNESS_OIDC_JWKS_URL=http://keycloak.localhost:8180/realms/engineering/protocol/openid-connect/certs"
  echo "HARNESS_OIDC_CLIENT_ID=oracledbharness-console"
} >> .env

# -- 3. start Keycloak fixture ---------------------------------------------------------

say "starting Keycloak fixture with host port publishing"
cat > "$WORK/keycloak.override.yaml" <<'EOF'
services:
  keycloak:
    ports:
      - "8180:8080"
EOF
chmod 644 "$WORK/keycloak.override.yaml"

docker compose -f "$KEYCLOAK_COMPOSE" -f "$WORK/keycloak.override.yaml" up -d --wait

say "waiting for Keycloak readiness and resolving Alice's subject"
ADMIN_TOKEN=""
for _ in $(seq 1 30); do
  ADMIN_TOKEN=$(curl -fsS http://127.0.0.1:8180/realms/master/protocol/openid-connect/token \
    -d "grant_type=password&client_id=admin-cli&username=admin&password=admin" 2>/dev/null | \
    python3 -c "import sys, json; print(json.load(sys.stdin).get('access_token', ''))" 2>/dev/null || true)
  if [[ -n "$ADMIN_TOKEN" ]]; then
    break
  fi
  sleep 2
done
[[ -n "$ADMIN_TOKEN" ]] || fail "Keycloak admin API not available"

ALICE_SUB=$(curl -fsS -H "Authorization: Bearer $ADMIN_TOKEN" \
  http://127.0.0.1:8180/admin/realms/engineering/users?username=alice 2>/dev/null | \
  python3 -c "import sys, json; users = json.load(sys.stdin); print(users[0]['id'] if users else '')" 2>/dev/null || true)
[[ -n "$ALICE_SUB" ]] || fail "could not determine alice subject from Keycloak"
echo "Alice subject in Keycloak: $ALICE_SUB"

# -- 4. deploy compose stack behind nginx ----------------------------------------------

say "writing compose override for host-gateway hostname resolution"
cat > "$WORK/compose.override.yaml" <<'EOF'
services:
  api:
    extra_hosts:
      - "keycloak.localhost:host-gateway"
      - "nginx.localhost:host-gateway"
      - "api.localhost:host-gateway"
  web:
    extra_hosts:
      - "keycloak.localhost:host-gateway"
      - "nginx.localhost:host-gateway"
      - "api.localhost:host-gateway"
EOF
chmod 644 "$WORK/compose.override.yaml"

say "starting deploy/compose.yaml stack"
docker compose -f compose.yaml -f "$WORK/compose.override.yaml" up -d --build

say "waiting for the API to report healthy"
if ! wait_for_healthy_api; then
  fail "the API never became healthy"
fi

say "verifying console is served through nginx"
for attempt in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:8080/healthz >/dev/null 2>&1; then
    break
  fi
  [[ "$attempt" == 30 ]] && fail "the console did not answer /healthz on 127.0.0.1:8080"
  sleep 2
done
curl -fsS http://127.0.0.1:8080/api/v1/system/info >/dev/null || fail "nginx proxy /api failed"

# -- 5. seed targets and user grants ---------------------------------------------------

say "seeding demonstration targets into metadata database"
docker compose exec -T \
  -e HARNESS_AUTH_MODE=dev \
  -e HARNESS_SECRET_DIR=/tmp/drill-secrets \
  api python -m harness_api.seed --no-probe

say "registering Alice and granting target 'development' (production ungranted)"
docker compose exec -T metadata psql -U harness -d harness -c "
  INSERT INTO users (id, subject, display_name, roles)
  VALUES ('usr_alice', '$ALICE_SUB', 'Alice Example', '[\"developer\"]')
  ON CONFLICT (subject) DO NOTHING;
"

docker compose exec -T metadata psql -U harness -d harness -c "
  INSERT INTO user_target_grants (id, user_id, profile_id, permissions)
  SELECT 'grt_alice_dev', u.id, p.id, '[\"read\", \"worksheet\"]'
  FROM users u, connection_profiles p
  WHERE u.subject = '$ALICE_SUB' AND p.name = 'development'
  ON CONFLICT DO NOTHING;
"

FORBIDDEN_TARGET_ID="$(docker compose exec -T metadata psql -U harness -d harness -At -c "
  SELECT id FROM connection_profiles WHERE name = 'production';
")"
[[ -n "$FORBIDDEN_TARGET_ID" ]] || fail "could not determine forbidden target ID for 'production'"
echo "Forbidden target ID (production): $FORBIDDEN_TARGET_ID"

# -- 6. run browser qualification ------------------------------------------------------

say "running browser qualification in standin mode"
mkdir -p "$(dirname "$REPORT")"

export HARNESS_BROWSER_MODE=standin
export HARNESS_BROWSER_CONSOLE_ORIGIN="http://127.0.0.1:8080"
export HARNESS_BROWSER_OIDC_ISSUER="http://keycloak.localhost:8180/realms/engineering"
export HARNESS_BROWSER_CLIENT_ID="oracledbharness-console"
export HARNESS_BROWSER_USER="alice"
export HARNESS_BROWSER_UNREGISTERED_USER="bob"
export HARNESS_BROWSER_CHECK_UNREGISTERED="1"
export HARNESS_BROWSER_FORBIDDEN_TARGET_ID="$FORBIDDEN_TARGET_ID"
export HARNESS_BROWSER_REPORT="$REPORT"

(
  cd "$REPO_ROOT"
  uv run --group browser python -m tests.browser run --report "$REPORT"
)

# -- 7. verify report verdict ----------------------------------------------------------

say "verifying stand-in report verdict"
[[ -f "$REPORT" ]] || fail "report file was not created: $REPORT"
JSON_REPORT="${REPORT%.md}.json"
[[ -f "$JSON_REPORT" ]] || fail "JSON sidecar was not created: $JSON_REPORT"

VERDICT="$(python3 -c "import json; print(json.load(open('$JSON_REPORT'))['verdict'])")"
QUALIFIED="$(python3 -c "import json; print(json.load(open('$JSON_REPORT'))['qualified'])")"

echo "Report verdict: $VERDICT"
echo "Qualified: $QUALIFIED"

if [[ "$QUALIFIED" != "False" ]]; then
  fail "stand-in run must never report qualified=true (got $QUALIFIED)"
fi

EXPECTED_VERDICT="AGENT-QUALIFIED ON TEST STAND-IN (AI agent, not a human; not a pilot qualification)"
if [[ "$VERDICT" != "$EXPECTED_VERDICT" ]]; then
  fail "unexpected stand-in verdict: '$VERDICT' (expected '$EXPECTED_VERDICT')"
fi

cat <<SUMMARY

== identity stand-in drill passed

  Deployment origin: http://127.0.0.1:8080 (via nginx proxy)
  Stand-in IdP:      http://keycloak.localhost:8180/realms/engineering
  Registered user:   alice ($ALICE_SUB) -> granted target 'development'
  Unregistered user: bob (refused identity checked)
  Forbidden target:  production ($FORBIDDEN_TARGET_ID)
  Report:            $REPORT
  Report verdict:    $VERDICT
  Pilot qualified:   $QUALIFIED (stand-in qualification; not a pilot qualification)

SUMMARY
