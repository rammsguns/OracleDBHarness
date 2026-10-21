#!/usr/bin/env bash
set -euo pipefail

STRATA_DIR="/home/kiwi/llm-rig/strata"
KEY_FILE="$STRATA_DIR/.config/strata/api-key"

if [[ ! -f "$KEY_FILE" ]]; then
  echo "Error: API key file not found at $KEY_FILE" >&2
  exit 1
fi

API_KEY="$(cat "$KEY_FILE")"
BASE_URL="http://127.0.0.1:8080"

# Check if Strata server is up
if ! curl -sf "$BASE_URL/health" > /dev/null 2>&1; then
  echo "[-] Strata is not responding on $BASE_URL" >&2
  exit 1
fi

HEALTH_JSON=$(curl -s "$BASE_URL/health")
STATUS_JSON=$(curl -s -H "Authorization: Bearer $API_KEY" "$BASE_URL/status")
METRICS_JSON=$(curl -s -H "Authorization: Bearer $API_KEY" "$BASE_URL/metrics")

echo "=========================================================="
echo "                  STRATA SYSTEM STATUS                    "
echo "=========================================================="

echo "$HEALTH_JSON" | jq -r '
  "Model:        \(.model)
Max Context:  \(.max_context) tokens
Service:      \(.service) (loaded: \(.loaded))"'

echo ""
echo "--- Live Activity ---"
echo "$STATUS_JSON" | jq -r '
if .busy then
  "Status:       BUSY (Phase: \(.phase // "unknown"))
Prompt:       \(.prompt_tokens // 0) tokens
Generated:    \(.generated // 0) tokens
Speed:        \(.tokens_per_s // 0) tok/s (avg: \(.tokens_per_s_mean // 0) tok/s)
Elapsed:      \(.elapsed_s // 0)s
Queued:       \(.queued // 0)"
else
  "Status:       IDLE
Queued:       \(.queued // 0)"
end'

echo ""
echo "--- Cumulative Totals ---"
echo "$METRICS_JSON" | jq -r '
.totals as $t |
.conversation_cache as $c |
($t.drafts_accepted / ($t.drafts_offered // 1) * 100) as $spec_acc |
($t.reused / ($t.prompt_tokens // 1) * 100) as $reuse_rate |
"Requests:     \($t.requests)
Prompts:      \($t.prompt_tokens) tokens (\($t.reused) reused, \($reuse_rate | round)% prefix hit)
Output:       \($t.output_tokens) tokens
Drafts Acc:   \($t.drafts_accepted)/\($t.drafts_offered) (\($spec_acc | round)%)"'

echo ""
echo "--- GPUs (RTX A4000) ---"
nvidia-smi --query-gpu=index,name,memory.used,memory.total,utilization.gpu,temperature.gpu --format=csv,noheader \
  | awk -F', ' '{printf "GPU %s: %s | VRAM: %s / %s | Util: %s | Temp: %s°C\n", $1, $2, $3, $4, $5, $6}'
echo "=========================================================="
