#!/usr/bin/env bash
#
# Repeatable NP-07 three-database, ten-user capacity run driver.
# Drives the validation, baseline, main capacity run and soak run against Oracle test databases.
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

CONFIG_PATH=""
REPORT_DIR=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --config)
      CONFIG_PATH="$2"
      shift 2
      ;;
    --report-dir)
      REPORT_DIR="$2"
      shift 2
      ;;
    -h|--help)
      echo "Usage: $0 <config.json> <report_dir>"
      echo "       $0 --config <config.json> --report-dir <report_dir>"
      exit 0
      ;;
    *)
      if [[ -z "$CONFIG_PATH" ]]; then
        CONFIG_PATH="$1"
        shift
      elif [[ -z "$REPORT_DIR" ]]; then
        REPORT_DIR="$1"
        shift
      else
        echo "Error: unexpected argument '$1'" >&2
        exit 2
      fi
      ;;
  esac
done

if [[ -z "$CONFIG_PATH" || -z "$REPORT_DIR" ]]; then
  echo "Usage: $0 <config.json> <report_dir>" >&2
  exit 2
fi

if [[ ! -f "$CONFIG_PATH" ]]; then
  echo "Error: config file not found: $CONFIG_PATH" >&2
  exit 2
fi

# Convert CONFIG_PATH and REPORT_DIR to absolute paths
CONFIG_PATH="$(cd "$(dirname "$CONFIG_PATH")" && pwd)/$(basename "$CONFIG_PATH")"
mkdir -p "$REPORT_DIR"
REPORT_DIR="$(cd "$REPORT_DIR" && pwd)"

export UV_CACHE_DIR="${UV_CACHE_DIR:-/tmp/oracledbharness-uv-cache}"
cd "$REPO_ROOT"

echo "=== NP-07 Capacity Driver ==="
echo "Config: $CONFIG_PATH"
echo "Report Directory: $REPORT_DIR"

# Validate configuration
uv run python -m tests.capacity.driver validate-config "$CONFIG_PATH" --check-files

TEMP_DIR="$(mktemp -d "/tmp/harness-capacity-run-XXXXXX")"
chmod 700 "$TEMP_DIR"

API_PID=""
STARTED_PG_CONTAINER=0

# shellcheck disable=SC2317
cleanup() {
  local exit_code=$?
  echo "--- Running teardown ---"

  # 1. Idempotently drop marker schema on each target
  if [[ -f "$CONFIG_PATH" ]]; then
    echo "Dropping marker schema on targets..."
    uv run python -m tests.capacity.driver teardown-markers "$CONFIG_PATH" || true
  fi

  # 2. Stop API server if running
  if [[ -n "$API_PID" ]] && kill -0 "$API_PID" 2>/dev/null; then
    echo "Stopping API server (PID: $API_PID)..."
    kill -TERM "$API_PID" 2>/dev/null || true
    wait "$API_PID" 2>/dev/null || true
  fi

  # 3. Stop & remove PostgreSQL container if started by this script
  if [[ "$STARTED_PG_CONTAINER" -eq 1 ]]; then
    echo "Stopping and removing PostgreSQL container harness-capacity-pg..."
    docker stop harness-capacity-pg >/dev/null 2>&1 || true
    docker rm -f harness-capacity-pg >/dev/null 2>&1 || true
  fi

  # 4. Clean up temporary directory
  if [[ -d "$TEMP_DIR" ]]; then
    rm -rf "$TEMP_DIR"
  fi

  echo "Teardown complete."
  exit "$exit_code"
}
trap cleanup EXIT INT TERM

# PostgreSQL metadata store setup
CONFIG_PG_URL="$(uv run python -m tests.capacity.driver get-postgres-url "$CONFIG_PATH")"
if [[ -z "$CONFIG_PG_URL" ]]; then
  echo "Starting disposable PostgreSQL container harness-capacity-pg..."
  PG_PASS_FILE="$TEMP_DIR/postgres_password"
  uv run python -m tests.capacity.driver generate-password-file "$PG_PASS_FILE"
  PG_PORT="$(uv run python -m tests.capacity.driver free-port)"

  # Remove any stale container with the same name
  docker rm -f harness-capacity-pg >/dev/null 2>&1 || true

  docker run -d \
    --name harness-capacity-pg \
    -p "127.0.0.1:${PG_PORT}:5432" \
    -e POSTGRES_USER=harness \
    -e POSTGRES_PASSWORD_FILE=/run/secrets/pg_password_file \
    -e POSTGRES_DB=harness \
    -v "${PG_PASS_FILE}:/run/secrets/pg_password_file:ro" \
    postgres:16-alpine >/dev/null

  STARTED_PG_CONTAINER=1

  echo "Waiting for PostgreSQL to become ready..."
  READY=0
  for _ in $(seq 1 30); do
    if docker exec harness-capacity-pg pg_isready -U harness -d harness >/dev/null 2>&1; then
      READY=1
      break
    fi
    sleep 1
  done
  if [[ "$READY" -ne 1 ]]; then
    echo "Error: PostgreSQL container did not become ready in time." >&2
    exit 2
  fi

  export HARNESS_METADATA_URL="postgresql+psycopg://harness@127.0.0.1:${PG_PORT}/harness"
  export HARNESS_METADATA_PASSWORD_FILE="$PG_PASS_FILE"
else
  echo "Reusing provided PostgreSQL metadata store: $CONFIG_PG_URL"
  export HARNESS_METADATA_URL="$CONFIG_PG_URL"
  unset HARNESS_METADATA_PASSWORD_FILE || true
fi

# Prepare staged secret directory
SECRET_DIR="$TEMP_DIR/secrets"
mkdir -p "$SECRET_DIR"
chmod 700 "$SECRET_DIR"
uv run python -m tests.capacity.driver prepare-secrets "$CONFIG_PATH" "$SECRET_DIR"
export HARNESS_SECRET_DIR="$SECRET_DIR"

# Target provisioning via scripts/bootstrap_targets.py
MANIFEST_FILE="$TEMP_DIR/manifest.json"
uv run python -m tests.capacity.driver generate-manifest "$CONFIG_PATH" "$MANIFEST_FILE"

export HARNESS_AUTH_MODE=dev
export HARNESS_ALLOW_DEV_AUTH_OUTSIDE_DEVELOPMENT=true
export HARNESS_ORACLE_BACKEND=oracledb
export HARNESS_ORACLE_DRIVER_MODE=thin

echo "Provisioning targets via scripts/bootstrap_targets.py..."
uv run python scripts/bootstrap_targets.py --manifest "$MANIFEST_FILE"

# Query registered profile IDs
PROFILES_FILE="$TEMP_DIR/profiles.json"
if [[ -n "${HARNESS_METADATA_PASSWORD_FILE:-}" ]]; then
  uv run python -m tests.capacity.driver query-profiles \
    --metadata-url "$HARNESS_METADATA_URL" \
    --password-file "$HARNESS_METADATA_PASSWORD_FILE" \
    --output "$PROFILES_FILE"
else
  uv run python -m tests.capacity.driver query-profiles \
    --metadata-url "$HARNESS_METADATA_URL" \
    --output "$PROFILES_FILE"
fi

# Generate main and soak workloads
MAIN_WORKLOAD="$TEMP_DIR/workload_main.json"
SOAK_WORKLOAD="$TEMP_DIR/workload_soak.json"
uv run python -m tests.capacity.driver generate-workloads \
  "$CONFIG_PATH" "$MAIN_WORKLOAD" "$SOAK_WORKLOAD" \
  --profile-ids-json "$PROFILES_FILE"

# Apply marker schema
echo "Applying marker schema to targets..."
uv run python -m tests.capacity.driver apply-markers "$CONFIG_PATH"

# Start API server
API_PORT="$(uv run python -m tests.capacity.driver get-api-port "$CONFIG_PATH")"
export HARNESS_ENV=test
export HARNESS_WORKER_PROCESSES=4
export HARNESS_STATEMENT_TIMEOUT_SECONDS=30
export HARNESS_MAX_ROWS=50
export HARNESS_COPILOT_ENABLED=false

echo "Starting API server on port $API_PORT..."
uv run uvicorn harness_api.app:create_app --factory --host 127.0.0.1 --port "$API_PORT" > "$TEMP_DIR/api.log" 2>&1 &
API_PID=$!

echo "Waiting for API /healthz..."
API_READY=0
for _ in $(seq 1 30); do
  if curl -sf "http://127.0.0.1:${API_PORT}/healthz" >/dev/null 2>&1; then
    API_READY=1
    break
  fi
  sleep 1
done

if [[ "$API_READY" -ne 1 ]]; then
  echo "Error: API failed to start on port $API_PORT. Inspecting log:" >&2
  cat "$TEMP_DIR/api.log" >&2 || true
  exit 2
fi
echo "API is ready."

MAIN_FAILED=0
SOAK_FAILED=0

# --- Main Workload Run ---
echo "=== Running Main Workload ==="
uv run python -m tests.capacity validate "$MAIN_WORKLOAD"
uv run python -m tests.capacity baseline "$MAIN_WORKLOAD"

MAIN_REPORT="$REPORT_DIR/pilot.md"
MAIN_METRICS_CSV="$REPORT_DIR/pilot-host-metrics.csv"
MAIN_METRICS_JSON="$REPORT_DIR/pilot-host-metrics.json"

echo "Executing main capacity run..."
uv run python -m tests.capacity run "$MAIN_WORKLOAD" --report "$MAIN_REPORT" &
CLIENT_PID=$!

uv run python -m tests.capacity.driver sample-metrics \
  --api-pid "$API_PID" \
  --client-pid "$CLIENT_PID" \
  --csv "$MAIN_METRICS_CSV" \
  --summary "$MAIN_METRICS_JSON" \
  --interval 5.0 &
SAMPLER_PID=$!

set +e
wait "$CLIENT_PID"
MAIN_RUN_RC=$?
wait "$SAMPLER_PID" 2>/dev/null || true
set -e

if [[ "$MAIN_RUN_RC" -ne 0 ]]; then
  MAIN_FAILED=1
fi

uv run python -m tests.capacity.driver report-verdict "$MAIN_REPORT" "Main run" || MAIN_FAILED=1

# --- Soak Workload Run ---
echo "=== Running Soak Workload ==="
uv run python -m tests.capacity validate "$SOAK_WORKLOAD"
uv run python -m tests.capacity baseline "$SOAK_WORKLOAD"

SOAK_REPORT="$REPORT_DIR/soak.md"
SOAK_METRICS_CSV="$REPORT_DIR/soak-host-metrics.csv"
SOAK_METRICS_JSON="$REPORT_DIR/soak-host-metrics.json"

echo "Executing soak capacity run..."
uv run python -m tests.capacity run "$SOAK_WORKLOAD" --report "$SOAK_REPORT" &
SOAK_CLIENT_PID=$!

uv run python -m tests.capacity.driver sample-metrics \
  --api-pid "$API_PID" \
  --client-pid "$SOAK_CLIENT_PID" \
  --csv "$SOAK_METRICS_CSV" \
  --summary "$SOAK_METRICS_JSON" \
  --interval 5.0 &
SOAK_SAMPLER_PID=$!

set +e
wait "$SOAK_CLIENT_PID"
SOAK_RUN_RC=$?
wait "$SOAK_SAMPLER_PID" 2>/dev/null || true
set -e

if [[ "$SOAK_RUN_RC" -ne 0 ]]; then
  SOAK_FAILED=1
fi

uv run python -m tests.capacity.driver report-verdict "$SOAK_REPORT" "Soak run" || SOAK_FAILED=1

echo "=== Capacity Runs Complete ==="
if [[ "$MAIN_FAILED" -ne 0 || "$SOAK_FAILED" -ne 0 ]]; then
  echo "Capacity runs completed with failures: main_failed=$MAIN_FAILED, soak_failed=$SOAK_FAILED"
  exit 1
fi

echo "All capacity runs completed successfully."
exit 0
