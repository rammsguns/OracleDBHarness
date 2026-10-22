# Restarting Strata

This guide provides instructions to restart the Strata inference engine quickly and easily.

---

## Method 1: Helper Script (Quickest)

You can run the helper script directly:

```bash
./scripts/restart_strata.sh
```

---

## Method 2: Fast Engine Reload via API (One-Liner)

When the Strata HTTP server (`server.py`) is already running (even if run under another user or `root`), you can recycle GPU VRAM and reload the model in one step via Strata's control API.

### One-Liner Command

```bash
API_KEY=$(cat ${HOME}/llm-rig/strata/.config/strata/api-key) && \
curl -s -X POST http://127.0.0.1:8080/unload -H "Authorization: Bearer $API_KEY" -H "Content-Type: application/json" -d '{}' && \
curl -s -X POST http://127.0.0.1:8080/load -H "Authorization: Bearer $API_KEY" -H "Content-Type: application/json" -d '{}'
```

> **Note:** The reload step (`/load`) takes approximately 25–60 seconds as the weights and KV caches are mapped into the dual RTX A4000 GPUs.

---

## Method 3: Cold Restart (Process Level)

If the HTTP server process has stopped or crashed, restart it directly from the Strata workspace.

### Foreground

```bash
cd ${HOME}/llm-rig/strata
./start.sh
```

### Background with Logging

```bash
cd ${HOME}/llm-rig/strata
mkdir -p logs
nohup ./start.sh > "logs/server-$(date -u +%Y%m%dT%H%M%SZ).log" 2>&1 &
```

If an existing `server.py` or `strata` process is stuck and needs to be killed first:
```bash
# Check running processes
ps aux | grep -E "strata|server.py" | grep -v grep

# Terminate process if owned by current user
pkill -f "serve/server.py"
pkill -f "engine/strata"
```
*(If started by root, stopping the engine via Method 1 or root permissions is required).*

---

## Verification & Status

### 1. Check Health Endpoint
```bash
curl -s http://127.0.0.1:8080/health | jq .
```
Expected output:
```json
{
  "status": "ok",
  "max_context": 131072,
  "model": "qwen3.8-flash-next-iq3_s",
  "images": false,
  "api_key": true,
  "loaded": true,
  "service": "strata"
}
```

### 2. Verify GPU Allocation
```bash
nvidia-smi
```
Expected: Each RTX A4000 GPU should show ~15.5 GiB VRAM utilized by the `strata` process.

---

## Key Configurations & Paths

- **Strata Root:** `${HOME}/llm-rig/strata`
- **Startup Script:** `${HOME}/llm-rig/strata/start.sh`
- **Configuration:** `${HOME}/llm-rig/strata/engine/strata-iq3_s.json`
- **API Key File:** `${HOME}/llm-rig/strata/.config/strata/api-key`
- **Base API URL:** `http://127.0.0.1:8080/v1`
