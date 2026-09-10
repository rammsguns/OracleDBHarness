# OracleDataForge integration plan

Status: Proposed design, not implemented or compatibility-certified. Companion to [MVP_PLAN.md](MVP_PLAN.md).

## Objective

Make OracleDataForge the first supported IDE for OracleDBHarness. A DataForge administrator should be able to connect a running harness, test the integration, and enable Oracle-aware assistance without re-entering Oracle passwords, moving wallets, or replacing the existing IDE.

The web console remains part of OracleDBHarness. DataForge is its first embedded copilot client; VS Code and other IDE adapters follow the same versioned contracts.

## Inspected baseline

Repository: [rammsguns/OracleDataForge](https://github.com/rammsguns/OracleDataForge). Inspected local checkout commit: `907359b1fcd0f32e294ecbb2b64d9984dc999bea`. The remote README was also retrieved; this is source inspection, not an executed integration test.

- React/TypeScript browser IDE, Express backend, and node-oracledb. The existing Node-based application should continue to install and operate independently of the harness.
- Existing API client: [src/utils/api.ts](https://github.com/rammsguns/OracleDataForge/blob/907359b1fcd0f32e294ecbb2b64d9984dc999bea/src/utils/api.ts). It defines connection metadata, schema/source retrieval, query, explain, compile, routine and DBA operations.
- Existing backend: [server/index.ts](https://github.com/rammsguns/OracleDataForge/blob/907359b1fcd0f32e294ecbb2b64d9984dc999bea/server/index.ts). It owns authentication, Oracle sessions, roles and write guards. `CONFIRM_REQUIRED` responses carry confirmation information; an adapter must not automatically retry with `confirm: true`.
- Editor integration points: `SqlEditor.tsx` exposes selection callbacks; `ObjectEditor.tsx`, `DiffView.tsx`, `ExplainPlan.tsx`, and `src/utils/editBuffers.ts` provide reusable UI/context pieces. DataForge uses its own editor; do not require a Monaco migration.
- `server/worksheetSessions.ts` owns manual transactions by actor and database. The harness must not borrow or commit these sessions.
- Roles are capability-based: Analyst is restricted to table browsing, while Viewer has broader metadata/query access. Do not translate these names into a simple increasing privilege ladder.
- The README explicitly excludes AI today. A local, untracked `docs/ai-chat-plan.md` proposes optional Explain/Validate/Optimize chat, context preview and reviewed edits; it is not an existing API or shipped feature. This integration should align with that plan rather than create competing chat/provider services. That file and the DataForge checkout were not modified.

Documentation has drifted from source, including its test inventory. Pin compatibility to an actual commit/release and contract tests rather than assuming README descriptions fully define behavior.

## Ownership and request flow

```text
DataForge editor / optional chat panel
       | existing same-origin authenticated browser request
DataForge Express integration adapter
       | authorized, bounded context + scoped service authentication
OracleDBHarness versioned copilot API
       | approved provider request
AI provider
       | streamed answer / proposed editor diff
DataForge review and apply UI
```

DataForge owns its connections, wallets, user authorization, editor buffers and database execution. OracleDBHarness owns AI provider configuration, model calls, shared copilot contracts, context-handling rules, request limits and copilot execution history. Its standalone Oracle adapter remains available for independently registered harness connections.

For DataForge-originated MVP assistance, the DataForge backend resolves user-selected metadata through existing authorization logic and pushes a bounded context snapshot to the harness. The harness does not call arbitrary DataForge URLs or obtain database credentials. No database connection is required in the harness to explain context supplied by DataForge.

Use an opaque target key `(integration instance ID, DataForge connection ID)` plus schema and known database/container identity. Connection display names are not stable identifiers. Never infer that a separately registered harness connection represents the same target or shares the same transaction.

Applying an edit does not execute, compile, save an Oracle object, or sync it to GitHub. The user subsequently invokes existing DataForge controls, which retain their role, read-only and write-confirmation checks. These actions remain recorded by DataForge; the harness must not claim a complete audit of every DataForge database operation. End-to-end execution correlation is a later extension.

## Simple connection experience

Proposed first-run flow:

1. Start OracleDBHarness separately, preferably using its supplied image; keep DataForge's existing Node startup unchanged.
2. In the harness web console, register a DataForge integration and issue a revocable integration credential restricted to `copilot:assist`. No database execution capability is granted.
3. In DataForge's proposed Integrations settings, an administrator enters the harness base URL and configures the credential as a backend secret. The URL is administrator-controlled and validated, never supplied by chat prompts.
4. Select **Test connection**. Verify authentication, protocol version, enabled actions, provider availability and context limits without submitting source text.
5. Enable the feature. Users select their existing DataForge connection and explicit context; no credential or wallet export is needed.

Proposed server configuration names: `DATAFORGE_HARNESS_ENABLED`, `DATAFORGE_HARNESS_URL`, and `DATAFORGE_HARNESS_TOKEN`. These are new settings to implement, not existing DataForge environment variables. Provider credentials remain solely in the harness. Supply an example environment file and a Compose example that explains host-versus-container addressing; `localhost` inside a container refers to that container.

Disabled mode, expired credentials, or a harness outage must leave the normal IDE usable. Show a concise integration status and diagnostic request ID. Disconnecting revokes integration access and clears active copilot context without disconnecting Oracle sessions.

## Proposed versioned contract

All endpoints below are new integration interfaces. Existing DataForge database endpoints are implementation references, not an unrestricted external tool catalog.

| Owner | Endpoint | Purpose |
| --- | --- | --- |
| Harness | `GET /api/v1/integrations/capabilities` | Protocol version, supported actions, limits, readiness and required adapter version; no secrets |
| Harness | `POST /api/v1/copilot/requests` | Authenticated request with approved context; streamed typed response |
| DataForge | `GET /api/ai/capabilities` | Caller-filtered capabilities and optional integration status |
| DataForge | `POST /api/ai/chat` | Existing browser identity -> authorized context -> harness request; stream reply back |

Implement the DataForge routes proposed in its chat plan as a harness-backed adapter, avoiding a second model-provider implementation. Extract reusable backend authorization/metadata functions where necessary; do not internally call routes using a shared Administrator credential.

Request fields: protocol version, request ID, integration ID, server-established actor reference, action, target reference, conversation ID, editor ID/revision or source hash, selected range/text, approved attachments and context provenance. Attachments may contain selected object definitions, known database version, existing plan output or user-selected error text. Credentials, wallets, result rows and bind values are excluded.

Stream events: `start`, `delta`, `proposal`, `usage`, `done`, `error`. Proposals contain editor/target identity and original revision/hash so DataForge can reject stale or wrong-target edits. Define typed disabled, unauthorized, unsupported-version, context-too-large, rate-limit, timeout and provider-failure outcomes. Propagate browser disconnect/cancel to the harness/provider and test streaming through compression and proxies. Do not replay a partially delivered request automatically.

Publish JSON Schema/OpenAPI contracts with generated TypeScript types/client. The Python backend can validate the same schema; language interoperability does not require moving DataForge to Python. Negotiate protocol major version, ignore explicitly optional unknown fields, reject incompatible major versions, and validate both requests and streamed events.

## Authentication and context boundaries

The browser authenticates only to DataForge for the embedded workflow. The DataForge backend authenticates to the harness using a scoped integration credential stored server-side. The harness treats actor references as assertions from that registered adapter, namespaces them by integration instance, and never accepts browser-supplied roles as authority.

DataForge checks its actual current actor and permissions before resolving every context attachment. The effective capability is the intersection of DataForge authorization, harness integration scope and administrator data-sharing policy. This delegated mode grants copilot access only; it does not turn a DataForge Administrator into a harness administrator or grant access to unrelated harness targets.

For DataForge installations without named accounts, identify the actor as an ephemeral local session and apply integration-wide limits; do not claim durable per-user audit. Keep transcripts transient by default. Require explicit provider/data-sharing enablement, show outgoing context for review, and clear conversations on account or target changes. Use TLS for non-loopback service traffic; keep tokens out of browser storage, URLs and routine logs.

Preserve DataForge's existing role distinctions. Analysts receive documentation-only assistance initially; no schema-context expansion through the adapter. Existing DataForge DBA/performance routes require individual capability/licensing review before any future exposure; importing a route into the harness does not make all of its operations appropriate for automated use.

## Implementation work packages

1. **Contract and handshake:** versioned schemas, capability negotiation, service credential lifecycle, actor namespaces, disabled-state behavior, generated TypeScript client.
2. **DataForge adapter:** optional same-origin routes, configuration/status UI, role-aware context resolution, request limits and stream forwarding. Keep Oracle session management unchanged.
3. **Editor foundation:** independent worksheet buffers and stable revisions. The inspected state store has a shared SQL buffer despite multiple tabs; fix this before adding new-worksheet and named-editor apply actions. Reuse existing object edit buffers and diff rendering.
4. **Copilot workflow:** Explain, Validate, Optimize, proposed SQL/PLSQL, selected-error help and existing-plan explanations; context preview, cancel, copy and reviewed apply. Validation remains advisory unless explicit Oracle diagnostics were supplied.
5. **Compatibility release:** integration fixtures, regression checks, setup guide, version matrix and demonstration on Oracle 19c plus the chosen development database.

Coordinate the DataForge-side changes with its AI-chat work. Only the plan is being changed here; implementation will require work in both repositories. The standalone harness web workflows remain in scope, while VS Code becomes the next adapter after DataForge.

## Release gates

- On an already configured DataForge and running harness, complete setup within ten minutes using only endpoint/credential configuration. Measure this in a pilot; do not claim the target before testing.
- List supported actions and explain a selected statement without registering a second Oracle connection or copying any wallet/password.
- Reject invalid/revoked credentials, incompatible protocol versions, spoofed browser roles and unauthorized context requests; verify no cross-user or cross-target context leakage.
- Reject edits if the source, target, account or selected editor changed; verify that applying a proposal causes no Oracle or GitHub mutation.
- Preserve all current DataForge tests, typecheck and build. Add adapter contract tests and end-to-end context/stream/diff tests, including outage, cancellation and partial-response handling.
- Verify existing worksheet commit/rollback, compilation, role restrictions and confirmation dialogs are unchanged by the integration.
- Test feature disabled and harness unreachable; users can still connect, edit and run through the ordinary IDE.
- Record harness build, DataForge commit, adapter version, protocol version and tested Oracle versions in a compatibility matrix. The inspected commit is the initial test candidate, not a certified baseline.

Planning allowance: reserve approximately 3-4 weeks for the DataForge adapter, editor prerequisites, copilot workflow and integration validation within the larger MVP schedule. Re-estimate after the joint contract/editor spike; this is not additive to an already implemented DataForge chat feature.
