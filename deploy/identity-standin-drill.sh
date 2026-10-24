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
# 2. Keycloak container: published on 127.0.0.1:8180 and on the docker bridge gateway
#    address :8180 only (never every interface: it is a development realm with a fixed
#    admin password), from a realm copy that also accepts this run's console origin.
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
KEYCLOAK_REALM="$REPO_ROOT/tests/identity/keycloak/realm.json"

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

# Host ports of the console and the API (deploy/compose.yaml); override them on a host
# where 8080 or 8000 is already taken.
WEB_PORT="${HARNESS_WEB_HOST_PORT:-8080}"
API_PORT="${HARNESS_API_HOST_PORT:-8000}"
export HARNESS_WEB_HOST_PORT="$WEB_PORT" HARNESS_API_HOST_PORT="$API_PORT"

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
# The API container reaches Keycloak through the docker bridge gateway (host-gateway),
# so Keycloak listens there as well as on loopback, never on every interface: this is a
# development realm with a fixed admin password.
GATEWAY="$(docker network inspect bridge -f '{{(index .IPAM.Config 0).Gateway}}')"
[[ -n "$GATEWAY" ]] || fail "could not read the docker bridge gateway address"
# A copy of the fixture realm that also accepts this run's console origin.
python3 - "$KEYCLOAK_REALM" "$WORK/realm.json" "http://127.0.0.1:$WEB_PORT" <<'PY'
import json, sys
source, target, origin = sys.argv[1:]
realm = json.load(open(source))
for client in realm["clients"]:
    if client.get("clientId") == "oracledbharness-console":
        client["redirectUris"].append(f"{origin}/auth/callback")
        client["webOrigins"].append(origin)
json.dump(realm, open(target, "w"), indent=2)
PY
chmod 644 "$WORK/realm.json"
cat > "$WORK/keycloak.override.yaml" <<YAML
services:
  keycloak:
    ports: !override
      - "127.0.0.1:8180:8080"
      - "$GATEWAY:8180:8080"
    volumes: !override
      - "$WORK/realm.json:/opt/keycloak/data/import/realm.json:ro"
YAML
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
  if curl -fsS http://127.0.0.1:$WEB_PORT/healthz >/dev/null 2>&1; then
    break
  fi
  [[ "$attempt" == 30 ]] && fail "the console did not answer /healthz on 127.0.0.1:$WEB_PORT"
  sleep 2
done
curl -fsS http://127.0.0.1:$WEB_PORT/api/v1/system/info >/dev/null || fail "nginx proxy /api failed"

# -- 5. seed targets and user grants ---------------------------------------------------

say "seeding demonstration targets into metadata database"
docker compose exec -T \
  -e HARNESS_AUTH_MODE=dev \
  -e HARNESS_SECRET_DIR=/tmp/drill-secrets \
  api python -m harness_api.seed --no-probe

say "registering Alice and granting target 'development' (production ungranted)"
# Through the application's own models in the API container, as an administrator's
# registration would be: Alice is a developer granted 'development' only.
docker compose exec -T -e ALICE_SUB="$ALICE_SUB" api python - <<'PY'
import os

from sqlalchemy import select

from harness_api.config import get_settings
from harness_api.db import build_engine, build_session_factory
from harness_api.models import ConnectionProfile, User, UserTargetGrant
from harness_api.policy import PERMISSION_READ, PERMISSION_WORKSHEET

subject = os.environ["ALICE_SUB"]
with build_session_factory(build_engine(get_settings()))() as db:
    user = db.scalars(select(User).where(User.subject == subject)).one_or_none()
    if user is None:
        user = User(subject=subject, display_name="Alice Example", roles=["developer"])
        db.add(user)
        db.flush()
    profile = db.scalars(
        select(ConnectionProfile).where(ConnectionProfile.name == "development")
    ).one()
    db.add(
        UserTargetGrant(
            user_id=user.id,
            profile_id=profile.id,
            permissions=[PERMISSION_READ, PERMISSION_WORKSHEET],
        )
    )
    db.commit()
print(f"registered {subject} with a grant on 'development'")
PY

FORBIDDEN_TARGET_ID="$(docker compose exec -T metadata psql -U harness -d harness -At -c "
  SELECT id FROM connection_profiles WHERE name = 'production';
")"
[[ -n "$FORBIDDEN_TARGET_ID" ]] || fail "could not determine forbidden target ID for 'production'"
echo "Forbidden target ID (production): $FORBIDDEN_TARGET_ID"

# -- 6. run browser qualification ------------------------------------------------------

say "running browser qualification in standin mode"
mkdir -p "$(dirname "$REPORT")"

export HARNESS_BROWSER_MODE=standin
export HARNESS_BROWSER_CONSOLE_ORIGIN="http://127.0.0.1:$WEB_PORT"
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

  Deployment origin: http://127.0.0.1:$WEB_PORT (via nginx proxy)
  Stand-in IdP:      http://keycloak.localhost:8180/realms/engineering
  Registered user:   alice ($ALICE_SUB) -> granted target 'development'
  Unregistered user: bob (refused identity checked)
  Forbidden target:  production ($FORBIDDEN_TARGET_ID)
  Report:            $REPORT
  Report verdict:    $VERDICT
  Pilot qualified:   $QUALIFIED (stand-in qualification; not a pilot qualification)

SUMMARY
