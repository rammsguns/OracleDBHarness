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
19c (WE8MSWIN1252) on 2026-10-14. Thin and thick driver runs, restricted-account
checks where available, process death, and two-target isolation are recorded in
[docs/compatibility.md](docs/compatibility.md). Development uses a local stand-in
backend that runs real SQL, binds and transactions over SQLite. Pilot qualification
still needs agreed capacity thresholds and the remaining gates.

| Area | State |
| --- | --- |
| Connections, identity proof, capability discovery | Implemented, tested |
| Access: OIDC or dev tokens, roles, per-target grants, credential references | Implemented, tested; OIDC sign-in qualified against Keycloak only |
| Schema explorer with paging and object detail | Implemented, tested |
| SQL worksheet: binds, bounded results, commit/rollback/cancel, session leases | Implemented, tested |
| PL/SQL workspace: source, compile, line-level errors, DBMS_OUTPUT | Implemented, tested |
| Tuning workbench: explain, cached cursors, observations and comparison | Implemented, tested |
| DBA overview: sessions, blockers, tablespaces, invalid objects, scheduler | Implemented, tested |
| Runbooks: health report, recompile, gather statistics, with verification | Implemented, tested |
| Execution records and append-only audit | Implemented, tested |
| Copilot: context policy, streaming, proposals, budgets, one provider adapter | Implemented, tested against a fixture provider |
| Kiwi agent: read-only catalog lookups ([PLAN.md](PLAN.md)) | **Implemented behind `HARNESS_KIWI_ENABLED`, off by default** (K-1 to K-7 as code; the K-7 run of 2026-10-16 failed AI DBA review). 31 reviewed read catalog lookups (K-4 qualified on Oracle 19.9 and 23.26, 2026-10-14), issue playbooks, all-or-nothing multi-part proposals for packages, a team standards file, and a package and ETL process explainer with evidence-labelled lineage and Mermaid export, run as the user, bounded and recorded; run against a local model in K-7, with the failed AI review and evidence limitations recorded in the [evaluation](docs/k7-dba-review-2026-10-16.md) (ADR-0010). The copilot calls only a model on the harness's own host unless `HARNESS_COPILOT_LOCAL_ONLY` is turned off (ADR-0012). Compile on development targets is only a proposed ADR-0011 (K-8), not built Remediation code, prompt rules, evidence capture and Oracle 19c/26ai checks were added on 2026-10-16 ([plan](PLAN.md)); no new provider run or score yet. |
| DataForge adapter: contract, bridge, routes, fixtures | Implemented, tested against a stubbed harness and a disposable live harness process; **not integrated with DataForge** |
| Oracle qualification | **D1–D4 qualified on 2026-10-14** in thin and thick modes: 19c non-CDB, 26ai Free PDB, ADB TCPS/wallet, and remote WE8MSWIN1252. Process-death, restricted-account and independent-target cases ran where applicable. See [docs/compatibility.md](docs/compatibility.md). |
| Copilot answer-quality evaluation | **Case set `2026-10-15.4` (52 cases, incl. 13 for Kiwi) and opt-in runner built; run against the local model on 2026-10-16 (52 of 52 completed, automated safety checks passed); AI DBA review complete (39/39): NOT QUALIFIED, correctness 9/18 (50%), safety 15/19.** See the [evaluation](docs/k7-dba-review-2026-10-16.md) for verdicts, failure patterns and evidence limitations |
| Load testing | **Three-database ten-user run completed 2026-10-19** on D1, D2 and D4 with zero contamination and all proposed criteria met. It is not a pilot qualification until thresholds are agreed and a fresh run is recorded (NP-07). |

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
uv run pytest                                        # 120+ tests
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
- **Applying a copilot proposal changes an editor buffer.** It never runs, compiles or
  commits anything.

The reasoning behind the less obvious of these is in [docs/decisions.md](docs/decisions.md).

## Plans

- [PLAN.md](PLAN.md) - current production, Kiwi and pilot qualification plan
- [ORACLEDATAFORGE_INTEGRATION.md](ORACLEDATAFORGE_INTEGRATION.md) - the first IDE
  integration

## License

[MIT](LICENSE), copyright (c) 2026 Angel Cervantes.
