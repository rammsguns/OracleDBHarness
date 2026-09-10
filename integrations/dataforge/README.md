# OracleDataForge integration

This directory holds the DataForge side of the copilot integration: the backend
bridge, the same-origin routes, contract fixtures, and the setup guide.

The code here is written to be copied or vendored into the DataForge repository. It
has no runtime dependencies, and its Express types are structural, so it drops into
the existing app without changing DataForge's stack.

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

3. **Configure DataForge.** These are new settings to add; they are not existing
   DataForge environment variables:

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

## Not done yet

This is the adapter side of a two-repository change. The DataForge repository still
needs:

- Independent worksheet buffers with stable revisions. The inspected state store
  shares one SQL buffer across tabs, which has to be fixed before named-editor apply
  actions are safe.
- The settings screen and status indicator described above.
- A `resolveContextForActor` implementation over its existing authorization helpers,
  extracted rather than called through an internal HTTP request with a shared
  administrator credential.

See `COMPATIBILITY.md` for what has and has not been tested.
