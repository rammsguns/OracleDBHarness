#!/usr/bin/env bash
set -euo pipefail

STRATA_DIR="/home/kiwi/llm-rig/strata"
KEY_FILE="$STRATA_DIR/.config/strata/api-key"

if [[ ! -f "$KEY_FILE" ]]; then
  echo "Error: API key file not found at $KEY_FILE" >&2
  exit 1
fi

API_KEY="$(cat "$KEY_FILE")"

echo "Checking Strata service status..."
if ! curl -sf http://127.0.0.1:8080/health > /dev/null 2>&1; then
  echo "Strata HTTP server is not responding on port 8080."
  echo "Starting fresh instance via $STRATA_DIR/start.sh..."
  cd "$STRATA_DIR"
  mkdir -p logs
  nohup ./start.sh > "logs/server-$(date -u +%Y%m%dT%H%M%SZ).log" 2>&1 &
  echo "Started in background. Waiting for initialization..."
  for i in {1..30}; do
    if curl -sf http://127.0.0.1:8080/health > /dev/null 2>&1; then
      echo "Strata is ready!"
      exit 0
    fi
    sleep 2
  done
  echo "Strata didn't respond within 60s. Check logs in $STRATA_DIR/logs."
  exit 1
fi

echo "Unloading model from GPU..."
curl -s -X POST http://127.0.0.1:8080/unload \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{}'
echo ""

echo "Reloading model into GPU (this takes ~25-60s)..."
curl -s -X POST http://127.0.0.1:8080/load \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{}'
echo ""

echo "Verifying health..."
curl -s http://127.0.0.1:8080/health
echo ""
echo "Done!"
