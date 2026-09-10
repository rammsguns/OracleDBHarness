# OracleDBHarness

A workspace for developers and DBAs to connect to Oracle databases, inspect schemas,
develop SQL and PL/SQL, investigate performance, and run reviewed operational
procedures - with explicit permissions, bounded execution and an audit trail.

The harness is also the shared execution and AI-context layer: every operation has
typed inputs, a target, capability checks, limits and a structured result, and the web
console and IDE adapters use the same one.

## Status

The foundation and the first vertical slice of [MVP_PLAN.md](MVP_PLAN.md) are
implemented and tested end to end. **Nothing has run against a real Oracle database.**
Development uses a local stand-in backend that runs real SQL, binds and transactions
over SQLite; it is not Oracle, it says so everywhere it appears, and release
qualification still requires Oracle 19c. See [docs/compatibility.md](docs/compatibility.md).

| Area | State |
| --- | --- |
| Connections, identity proof, capability discovery | Implemented, tested |
| Access: OIDC or dev tokens, roles, per-target grants, credential references | Implemented, tested (OIDC path unexercised) |
| Schema explorer with paging and object detail | Implemented, tested |
| SQL worksheet: binds, bounded results, commit/rollback/cancel, session leases | Implemented, tested |
| PL/SQL workspace: source, compile, line-level errors, DBMS_OUTPUT | Implemented, tested |
| Tuning workbench: explain, cached cursors, observations and comparison | Implemented, tested |
| DBA overview: sessions, blockers, tablespaces, invalid objects, scheduler | Implemented, tested |
| Runbooks: health report, recompile, gather statistics, with verification | Implemented, tested |
| Execution records and append-only audit | Implemented, tested |
| Copilot: context policy, streaming, proposals, budgets, one provider adapter | Implemented, tested against a fixture provider |
| DataForge adapter: contract, bridge, routes, fixtures | Implemented, tested against a stubbed harness; **not integrated with DataForge** |
| Oracle 19c qualification | **Suite written, never run.** Fixtures and tests in [oracle/qualification](oracle/qualification/README.md); no database has been connected to |
| Copilot answer-quality evaluation | **Not started** |
| Load testing | **Not started** |

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

- [MVP_PLAN.md](MVP_PLAN.md) - product direction, scope, delivery sequence
- [ORACLEDATAFORGE_INTEGRATION.md](ORACLEDATAFORGE_INTEGRATION.md) - the first IDE
  integration

## License

[MIT](LICENSE), copyright (c) 2026 Angel Cervantes.
