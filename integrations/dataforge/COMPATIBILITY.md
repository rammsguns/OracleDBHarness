# Compatibility matrix

Nothing in this table is a certification. It records what has actually been run.

## Tested

| Component | Version | How it was tested |
| --- | --- | --- |
| Adapter | 0.1.0 | `node --test test/adapter.test.ts` against a stubbed harness |
| Protocol | 1.0 | Negotiation, and refusal of major version 2 |
| Harness API | 0.1.0 | End-to-end through the harness test suite |
| Node | 24.x | Native TypeScript execution, no build step |

## Not tested

| Component | Status |
| --- | --- |
| OracleDataForge | **Not integrated.** No DataForge commit has been run against this adapter. The inspected commit in `ORACLEDATAFORGE_INTEGRATION.md` is the first test candidate, not a baseline. |
| Oracle 19c | Not exercised through a real DataForge integration. The harness itself has passing evidence on one 19.9 non-CDB/thin instance; see `docs/compatibility.md`. |
| A real model provider | The harness ships an Anthropic adapter. The tests here run against a stubbed harness and the fixture provider; no provider call has been made. |
| Streaming through a proxy | Untested. Compression and buffering proxies are the usual cause of a stream arriving all at once; the routes set `X-Accel-Buffering: no`, which is not sufficient on its own. |
| Ten-minute setup target | Not measured. It is a target for the pilot, not a claim. |

## Release gates still open

From `ORACLEDATAFORGE_INTEGRATION.md`, these are unmet:

- Setup measured on an already-configured DataForge and a running harness.
- Explaining a selected statement without registering a second Oracle connection,
  demonstrated on a real installation.
- Verification that existing DataForge tests, typecheck and build still pass.
- Verification that worksheet commit/rollback, compilation, role restrictions and
  confirmation dialogs are unchanged by the integration.
- End-to-end context, stream and diff tests inside DataForge, including outage,
  cancellation and partial-response handling.

## Recording a run

When the integration is first exercised against a real DataForge, add a row here
with: harness build, DataForge commit, adapter version, protocol version, Oracle
version, and what was and was not exercised. Do not replace this file with a summary;
append.
