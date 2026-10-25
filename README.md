# OracleDBHarness

A workspace for developers and DBAs to connect to Oracle databases, inspect schemas,
develop SQL and PL/SQL, investigate performance, and run reviewed operational
procedures - with explicit permissions, bounded execution and an audit trail.

The harness is also the shared execution and AI-context layer: every operation has
typed inputs, a target, capability checks, limits and a structured result, and the web
console and IDE adapters use the same one.

## Status

The foundation and the first vertical slice of [PLAN.md](PLAN.md) are
implemented and tested end to end. The qualification suites ran on D1 19c
(AL32UTF8), D2 26ai Free PDB, D3 26ai ADB over TCPS and wallet, and D4 remote
19c (WE8MSWIN1252) on 2026-10-14. D1, D2 and D4 passed all six thin/thick
profiles again on 2026-10-24 (A3/B4), run by AI agent (acting DBA), not a human.
The current python-oracledb pin is `26.0.1` ([ADR-0015](docs/decisions.md#adr-0015-move-the-p4-python-oracledb-pin-to-2601));
D3 was requalified on this pin in BF1 (2026-10-25). Thin and thick driver runs, restricted-account
checks where available, process death, and two-target isolation are recorded in
[docs/compatibility.md](docs/compatibility.md). Development uses a local stand-in
backend that runs real SQL, binds and transactions over SQLite. Pilot qualification
still needs the human-owned gates. Capacity met the ADR-0013 agreed thresholds
on test stand-ins after A10/A11; human pilot acceptance and real fleet qualification remain open.
See the [gate status](docs/compatibility.md#gate-status-2026-10-24).
P1 is agent-qualified on a production-shaped stand-in on z4g5 (AI agent, not a human).
Section C: G3 canary is in progress (2026-10-25 → 2026-11-07; day 1 passed),
G4 waits on C4, and G5 has a C6 baseline PROMOTE. No human sign-off is claimed.
Section 6 production writes remain deferred.

| Area | State |
| --- | --- |
| Connections, identity proof, capability discovery | Implemented, tested |
| Access: OIDC or dev tokens, roles, per-target grants, credential references | Implemented, tested; deployed nginx/Keycloak stand-in browser drill passed 2026-10-24 (A5); pilot registration remains open |
| Schema explorer with paging and object detail | Implemented, tested |
| SQL worksheet: binds, bounded results, commit/rollback/cancel, session leases | Implemented, tested |
| PL/SQL workspace: source, compile, line-level errors, DBMS_OUTPUT | Implemented, tested |
| Tuning workbench: explain, cached cursors, observations and comparison | Implemented, tested |
| DBA overview: sessions, blockers, tablespaces, invalid objects, scheduler | Implemented, tested |
| Runbooks: health report, recompile, gather statistics, with verification | Implemented, tested |
| Execution records and append-only audit | Implemented, tested |
| Target disable/enable | Administrators can disable or enable a target with an audited reason; connection tests require read permission. B7 drill agent-rehearsed on D4; see [operations](docs/operations.md#production-operations). |
| Copilot: context policy, streaming, proposals, budgets, local provider | Implemented with fixture and local model providers; local-only by default. B1 exercised the real local provider through DataForge/nginx on test stand-ins; copilot remains opt-in. See [setup](docs/setup.md#enabling-the-copilot). |
| Kiwi agent: read-only catalog lookups ([PLAN.md](PLAN.md)) | **Implemented behind `HARNESS_KIWI_ENABLED`, off by default** (K-1 to K-7 as code; the K-7 run of 2026-10-16 failed AI DBA review). 31 reviewed read catalog lookups (K-4 qualified on Oracle 19.9 and 23.26, 2026-10-14), issue playbooks, all-or-nothing multi-part proposals for packages, a team standards file, and a package and ETL process explainer with evidence-labelled lineage and Mermaid export, run as the user, bounded and recorded; run against a local model in K-7, with the failed AI review and evidence limitations recorded in the [evaluation](docs/k7-dba-review-2026-10-16.md) (ADR-0010). The copilot calls only a model on the harness's own host unless `HARNESS_COPILOT_LOCAL_ONLY` is turned off (ADR-0012). Compile on development targets is only a proposed ADR-0011 (K-8), not built. Remediation code, prompt rules, evidence capture and Oracle 19c/26ai checks were added on 2026-10-16 ([plan](PLAN.md)). Seven pooled Q5 qualification attempts were evaluated across candidate fixes K8–K13 (2026-10-22 to 2026-10-23); on 2026-10-23 the user accepted the recommendation to close K-7 as **NOT QUALIFIED** for the local model (`qwen3.8-flash-next-iq3_s`). Kiwi is not qualified and stays off by default ([docs/k7-qualification-result.md](docs/k7-qualification-result.md)). |
| DataForge integration: opt-in Assist panel, bridge, routes, fixtures | Merged upstream in OracleDataForge as `431147a` ([PR #77](https://github.com/rammsguns/OracleDataForge/pull/77), B3). B1 16/16 Oracle/local-provider checks and B2 6/6 browser scenarios are agent-qualified on test stand-ins; final upstream fixes have unit coverage only. Human NP-05 acceptance remains open; see [compatibility](integrations/dataforge/COMPATIBILITY.md#release-gates-still-open). |
| Oracle qualification | D1–D4 evidence recorded on 2026-10-14 in thin and thick modes: 19c non-CDB, 26ai Free PDB, ADB TCPS/wallet, and remote WE8MSWIN1252. **D1/D2/D4 rerun on python-oracledb 26.0.1, 2026-10-24 (B4): agent-qualified on test stand-ins (AI agent, not a human)** across six thin/thick profiles; D3 retains its 2026-10-14 evidence. Process-death, restricted-account and independent-target cases ran where applicable. See [docs/compatibility.md](docs/compatibility.md). |
| Copilot answer-quality evaluation | **Case set `2026-10-15.4` (52 cases, incl. 13 for Kiwi) and opt-in runner built; run against the local model on 2026-10-16 (52 of 52 completed, automated safety checks passed); AI DBA review complete (39/39): NOT QUALIFIED, correctness 9/18 (50%), safety 15/19.** See the [evaluation](docs/k7-dba-review-2026-10-16.md). Seven subsequent Q5 pooled attempts (2026-10-22 to 2026-10-23) confirmed K-7 NOT QUALIFIED; Kiwi is not qualified and stays off by default ([docs/k7-qualification-result.md](docs/k7-qualification-result.md)). |
| Load testing | **agent-qualified on test stand-ins (AI agent, not a human)**: D1/D2/D4 ten-user main run and soak met every ADR-0013 agreed criterion on 2026-10-24 after the schema panel cache and Oracle keepalive fixes (A10/A11); pilot NP-07 remains open. |

`GET /api/v1/system/info` lists whatever caveats a running deployment currently has.

## Quick start

```bash
uv sync
cp .env.example .env
uv run python -m harness_api.seed
uv run uvicorn harness_api.app:create_app --factory --port 8000
```

```bash
npm --prefix apps/web install
npm --prefix apps/web run dev
```

Open http://localhost:5173 and sign in as `dev@example.internal` (developer),
`dba@example.internal` (DBA), `viewer@example.internal` (viewer) or
`admin@example.internal` (administrator).

Full instructions, including a real database and the Compose pilot deployment, are in
[docs/setup.md](docs/setup.md).

## Tests

```bash
uv run pytest
node --test integrations/dataforge/test/adapter.test.ts
npm --prefix apps/web run typecheck
uv run ruff check .
```

`tests/unit` is pure logic, `tests/integration` covers execution, transactions,
access and contracts, `tests/e2e` walks the six MVP workflows, and `tests/copilot`
covers the harness around the model.

## Layout

```
apps/web/                  React console
integrations/dataforge/    IDE adapter: bridge, routes, fixtures, setup guide
services/api/              API, policy, operation catalog, runbooks, copilot
services/worker/           Execution engine, session leases, Oracle adapter
packages/contracts/        OpenAPI, copilot protocol, shared TypeScript client
oracle/diagnostics/        Reviewed metadata queries, with their required privileges
oracle/runbooks/           Typed maintenance operations
oracle/grants/             Reviewed role and grant setup
tests/                     unit, integration, e2e, copilot
deploy/                    Container images and the Compose pilot deployment
docs/                      Architecture, decisions, setup, operations, compatibility
```

## Things worth knowing before reading the code

- **A keyword filter is not a read-only boundary.** A `SELECT` can call a function that
  writes. Free-form worksheets are enabled per profile, gated by grant and role, and
  constrained by the Oracle account itself. Production targets are observation only.
- **Oracle DDL commits.** A worksheet with pending DML refuses DDL rather than
  committing your work as a side effect, and compilation runs in its own session.
- **Rollback is not a general undo.** PL/SQL can commit on its own. The API says so
  instead of implying otherwise.
- **Cancellation is best effort**, and the result reports whether the break was
  delivered and whether the statement actually stopped.
- **`outcome_unknown` is a real state.** A write whose connection broke is never
  reported as a failure and never retried automatically.
- **An unavailable panel is never an empty one.** A missing privilege disables one
  feature and names what it needed.
- **Estimates and measurements stay apart.** Optimizer estimates are labelled as
  estimates and never differenced against measured values.
- **AWR, ASH, ADDM, advisors and Real-Time SQL Monitoring are not collected.** A
  technical privilege does not establish a management-pack entitlement.
- **Schema panels may be up to 60 seconds stale.** The console-only cache is configurable;
  copilot catalog evidence stays uncached (ADR-0014). Oracle connections use a one-minute
  keepalive by default (`HARNESS_ORACLE_EXPIRE_TIME_MINUTES`).
- **Applying a copilot proposal changes an editor buffer.** It never runs, compiles or
  commits anything.

The reasoning behind the less obvious of these is in [docs/decisions.md](docs/decisions.md).

## Plans

- [PLAN.md](PLAN.md) - current production, Kiwi and pilot qualification plan
- [ORACLEDATAFORGE_INTEGRATION.md](ORACLEDATAFORGE_INTEGRATION.md) - the first IDE
  integration

## License

[MIT](LICENSE), copyright (c) 2026 Angel Cervantes.
