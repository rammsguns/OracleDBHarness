# Strata Utilities & Fast Stats Guide

This guide details fast and convenient ways to inspect Strata's runtime status, generation speed, context usage, and hardware telemetry.

---

## 1. Quickest Method: Prebuilt Stats Script

Run the helper script directly from `OracleDBHarness`:

```bash
./scripts/strata_stats.sh
```

### Live Continuous Monitoring

To refresh stats every 1 or 2 seconds in your terminal:

```bash
watch -n 1 ./scripts/strata_stats.sh
```

---

## 2. Shell Aliases (Add to `~/.bashrc`)

For instant access from any directory without typing full paths:

```bash
alias strata-stats='/home/kiwi/OracleDBHarness/scripts/strata_stats.sh'
alias strata-restart='/home/kiwi/OracleDBHarness/scripts/restart_strata.sh'
alias strata-live='watch -n 1 /home/kiwi/OracleDBHarness/scripts/strata_stats.sh'
```

---

## 3. Fast One-Liners & Direct Endpoints

Strata listens on `http://127.0.0.1:8080` (and `0.0.0.0:8080`).

```bash
API_KEY=$(cat /home/kiwi/llm-rig/strata/.config/strata/api-key)
```

### A. Health & Service State (No Auth Required)
```bash
curl -s http://127.0.0.1:8080/health | jq .
```
Shows model name, loaded status, and max context size (`131072`).

### B. Live Inference Status
```bash
curl -s -H "Authorization: Bearer $API_KEY" http://127.0.0.1:8080/status | jq .
```
Shows:
- `busy`: `true` / `false`
- `phase`: `thinking` / `answering`
- `tokens_per_s`: Current generation tokens/sec
- `tokens_per_s_mean`: Average generation speed
- `prompt_tokens` & `generated`: Tokens in current call
- `tail`: Sneak-peek of the latest tokens generated

### C. Cumulative Totals & Cache Efficiency
```bash
curl -s -H "Authorization: Bearer $API_KEY" http://127.0.0.1:8080/metrics | jq '.totals, .conversation_cache'
```
Shows:
- Total requests and tokens generated
- KV prefix reuse / cache hit rate
- Speculative decoding drafts offered and accepted

### D. Slots & Context Window Status
```bash
curl -s -H "Authorization: Bearer $API_KEY" http://127.0.0.1:8080/slots | jq .
```

### E. GPU Memory & Thermals
```bash
nvidia-smi --query-gpu=index,name,memory.used,memory.total,utilization.gpu,temperature.gpu --format=csv,noheader
```

---

## 4. Web Monitor Dashboard

Strata also provides an integrated real-time web UI:
- Local URL: **`http://127.0.0.1:8080`**
- LAN URL: **`http://192.168.3.36:8080`**
- API Monitor: **`http://127.0.0.1:8080/api-monitor`**
