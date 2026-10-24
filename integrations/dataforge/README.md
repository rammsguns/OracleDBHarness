# OracleDataForge integration

This directory holds the DataForge side of the copilot integration: the backend
bridge, the same-origin routes, contract fixtures, and the setup guide.

The code here is written to be copied or vendored into the DataForge repository. It
has no runtime dependencies, and its Express types are structural, so it drops into
the existing app without changing DataForge's stack.

## Upstream status

**merged upstream**: [OracleDataForge PR #77](https://github.com/rammsguns/OracleDataForge/pull/77),
head `a40cf9c8a91046371e10598100f55021067624d6`, merged into DataForge main as
`431147a5ff69a3aecbb9ee2d3906b62af428994c` on 2026-10-24 after green CI and an independent agent review.
The vendored `src/*.ts` matches this PR. The refreshed
`spike/dataforge-3404f19.patch` is the exact binary-capable diff from upstream main
to that head; retaining it preserves the existing pinned-checkout reproduction path.
Prefer the PR branch when reviewing history. No live suite was rerun for B3.

## What each side owns

| Owns | DataForge | OracleDBHarness |
| --- | --- | --- |
| Oracle connections, wallets, sessions, transactions | yes | no |
| User authentication and authorization for the IDE | yes | no |
| Editor buffers, revisions, apply | yes | no |
| Database execution, compilation, GitHub sync | yes | no |
| Model provider configuration and the model call | no | yes |
| Copilot context rules, request limits, copilot history | no | yes |

The browser authenticates to DataForge only. The DataForge backend authenticates to
the harness with a scoped service credential stored on the server. The harness never
calls DataForge, never sees a database credential, and cannot execute anything
against a DataForge connection.

## Setup

1. **Start the harness** separately, using its own image or `deploy/compose.yaml`.
   DataForge's own startup is unchanged.

2. **Issue an integration credential** in the harness console (Administrator role):

   ```
   POST /api/v1/admin/integrations
   { "name": "dataforge-prod", "kind": "dataforge", "scopes": ["copilot:assist"] }
   ```

   The credential is shown once. It carries `copilot:assist` and nothing else: no
   database execution, no harness target access, and no harness administration. A
   DataForge Administrator does not become a harness Administrator.

3. **Configure DataForge.** The upstream PR reads these server-side settings (disabled by default):

   ```
   DATAFORGE_HARNESS_ENABLED=true
   DATAFORGE_HARNESS_URL=https://harness.internal:8000
   DATAFORGE_HARNESS_TOKEN=<the credential from step 2>
   ```

   The URL is administrator-controlled and validated. It is never taken from a chat
   prompt, a request header, or anything a user can influence. `https` is required
   unless the harness is on loopback. In Compose, remember that `localhost` inside a
   container is that container: use the service name, or the host gateway.

4. **Test the connection.** `GET /api/ai/capabilities` returns readiness, protocol
   compatibility, provider availability and limits. It sends no source text.

5. **Enable the feature.** Users select their existing DataForge connection; no
   credential or wallet is copied anywhere.

Disconnecting revokes the credential in the harness and clears active copilot
context. It does not disconnect any Oracle session.

## Wiring it up

```ts
import { HarnessAdapter } from "./adapter.ts";
import { readConfig } from "./config.ts";
import { registerHarnessRoutes } from "./routes.ts";

const adapter = new HarnessAdapter({
  config: readConfig(process.env),
  instanceId: installationId,          // stable per DataForge installation
  resolver: {
    targetReference: (instanceId, connectionId, schema) =>
      `dataforge:${instanceId}:${connectionId}:${schema ?? ""}`,
    databaseVersion: (connectionId) => connections.versionOf(connectionId),
    resolve: (actor, request) => resolveContextForActor(actor, request),
  },
});

registerHarnessRoutes({
  router: app,
  adapter,
  currentActor: async (req) => sessionActor(req),   // the existing DataForge session
});
```

`resolveContextForActor` is the part that has to live in DataForge, because only
DataForge knows its own permission rules. It must re-check the *current* actor
before every attachment it returns. The adapter refuses to accept context, a role,
a target reference, or an actor reference from the browser.

## What the adapter guarantees

- **Nothing executes.** Applying a proposal replaces editor text. Compiling or
  running the result is a separate DataForge action with its existing role,
  read-only and confirmation checks.
- **Stale edits are refused.** Every proposal is pinned to the editor id, revision
  and content hash it was generated from. `applyCheck` rejects it if the document,
  the editor, the target or the actor changed.
- **Outage and disabled modes are ordinary states.** With the feature off or the
  harness unreachable, `capabilities()` answers with a reason and the IDE keeps
  working. The adapter never throws into DataForge's request path.
- **A partial answer is never replayed.** If the stream stops halfway, that is what
  the caller sees. Re-asking is the user's decision.
- **Analysts get documentation-only assistance.** DataForge roles are
  capability-based, not a ladder: Analyst is restricted to table browsing, so it does
  not get schema-context expansion through the adapter until that is reviewed
  separately. `filterActionsForRole` is where that lives.

## Fixtures and tests

`fixtures/` holds recorded harness responses so the DataForge side can be built and
demonstrated before a model provider is configured:

| File | What it is |
| --- | --- |
| `capabilities.json` | A healthy harness |
| `capabilities-incompatible.json` | A harness one major protocol version ahead |
| `stream-diagnose.sse` | A full answer with a proposal and usage |
| `stream-provider-failure.sse` | A provider outage mid-request |

```bash
node --test integrations/dataforge/test/adapter.test.ts
```

The tests cover disabled mode, outage, protocol negotiation, credential revocation,
role filtering, context sanitising, and the browser-supplied-field refusals.

## Running against a real DataForge

To run integration checks against a real OracleDataForge Express server:

1. Clone `rammsguns/OracleDataForge` and apply the pinned PR diff:

   ```bash
   git clone https://github.com/rammsguns/OracleDataForge /path/to/dataforge
   git -C /path/to/dataforge checkout 3404f1905da183e1903a35193a7d5b0df18d0870
   git -C /path/to/dataforge apply integrations/dataforge/spike/dataforge-3404f19.patch
   npm --prefix /path/to/dataforge ci
   ```

2. Run the integration runner against a disposable harness:

   ```bash
   uv run python -m tests.dataforge_live dataforge --dataforge-dir /path/to/dataforge --report report.md
   ```

   Or point it at an already running harness or deployment proxy:

   ```bash
   uv run python -m tests.dataforge_live dataforge \
     --dataforge-dir /path/to/dataforge \
     --harness-url http://127.0.0.1:18081 \
     --token-file /path/to/token \
     --report report.md
   ```

The runner exercises real HTTP requests through DataForge's own Express routes:
capability negotiation (`GET /api/ai/capabilities`), streaming assist with SSE chunking
(`POST /api/ai/chat`), proposal apply verification (stale rejection and reviewable match),
harness outage and disabled mode fallback, and invalid token and incompatible protocol refusal.

### B1: Oracle test targets and the local model

The Oracle mode selects only `load-db-1` (D1) and `load-db-2` (D2), both as
`harness_app`, from the capacity configuration. It starts disposable PostgreSQL,
provisions a credential-free manifest with `scripts/bootstrap_targets.py`, starts
the thin `oracledb` backend and places it behind `deploy/nginx.conf` in
`nginx:1.27-alpine`. The local provider must already report the requested
`qwen3.8-flash-next-iq3_s` model healthy at `http://127.0.0.1:8080/health`.
Its key is copied privately from `/home/kiwi/llm-rig/strata/.config/strata/api-key`
and registered as a file-backed `copilot_api_key_ref`; it is never a command argument.

```bash
mkdir -p /home/kiwi/.cache/harness-capacity-tmp
TMPDIR=/home/kiwi/.cache/harness-capacity-tmp \
UV_CACHE_DIR=/home/kiwi/.cache/harness-uv \
uv run python -m tests.dataforge_live dataforge \
  --backend oracledb --targets databases/capacity/a2-run.json \
  --dataforge-dir /home/kiwi/harness-drills/dataforge \
  --report databases/reports/2026-10-24-b1-dataforge-oracle.md
```

Docker bind mounts require this daemon-visible `TMPDIR` on the test host. The
runner serializes model calls, injects a malformed SSE frame only after a real
provider delta for the partial-response check, and removes its containers,
processes, temporary secrets and owned D2 table/procedure when finished. It
returns nonzero for failed checks and preserves them in Markdown and JSON.
Worksheet, compilation, confirmation and viewer-principal regression checks use
harness HTTP routes while DataForge is running; they do not claim DataForge UI
coverage. The setup timer starts after both applications and nginx are healthy,
and ends after consuming the first successful assist's terminal event; its exact
included and excluded steps are recorded in the report.

## Remaining qualification

PR #77 supplies the worksheet Assist panel, outgoing context preview, status,
cancellation and reviewed buffer apply. Analysts have an explain-only documentation
panel. Context currently includes supplied text only, without schema expansion or
an Oracle version probe. Integration settings remain server-side.

B1 passed 16/16 backend checks and B2 passed 6/6 Playwright scenarios on Oracle
19c/23ai test stand-ins, agent-qualified (AI agents, not human sign-off). Upstream
review/merge, a human pilot, hosted providers, other proxies, compile/confirmation
UI and production qualification remain open. See `COMPATIBILITY.md`.
