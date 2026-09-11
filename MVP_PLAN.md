# OracleDBHarness MVP plan

Status: implemented through milestone 5; only console sign-in is qualified. The plan is kept as
it was written and annotated in place, so the difference between what was planned and
what was built stays visible.

Two words are used throughout, and they mean different things:

- **Implemented** - the code exists and the automated suite covers it. That suite runs
  against the local stand-in Oracle backend, a fixture copilot provider and development
  tokens. It exercises the harness logic; it demonstrates nothing about Oracle.
- **Qualified** - verified against the real dependency: Oracle 19c, an OIDC provider, a
  real OracleDataForge installation, a real model provider, or measured load.

**Almost nothing here is qualified yet.** Console sign-in has been qualified against
Keycloak. Every other acceptance claim below still rests on the stand-in. [docs/compatibility.md](docs/compatibility.md) records what has and has not
actually been run; it is the file to update when that changes, not this one.

## Product direction

OracleDBHarness is a dedicated workspace for developers and DBAs to connect to Oracle databases, inspect schemas, develop SQL and PL/SQL, investigate performance, and execute repeatable operational procedures with clear permissions and execution history.

The harness is the shared execution and AI context layer: every supported operation has typed inputs, a target database, capability checks, execution limits, and structured results. The web console and IDE adapters reuse this layer so developers can receive Oracle-aware copilot assistance inside their existing editor.

Confirmed product direction: a web console for developers and DBAs, with connections to Oracle IDEs for AI copilot features. Ship one IDE adapter in the MVP; broaden IDE coverage afterward.

OracleDataForge is the first supported integration target. Its existing Oracle IDE supplies editor context and retains its connection/session ownership; OracleDBHarness provides the shared copilot service. See [OracleDataForge integration plan](ORACLEDATAFORGE_INTEGRATION.md) for inspected source, proposed contracts, setup and compatibility gates.

Working assumptions: self-hosted application for one organization; small internal pilot; development and test databases support changes; production starts with observation only. Oracle 19c is the initial compatibility target. A current Oracle Database Free instance can support development, but release qualification also requires a real 19c environment. Additional versions and deployment models require explicit testing.

## MVP outcome

A developer or DBA can complete these workflows without switching tools:

1. Register a database connection, verify identity and permissions, and inspect its schemas.
2. Run parameterized SQL, inspect bounded results, and explicitly commit or roll back DML.
3. Edit and compile a PL/SQL package, inspect line-level errors, and execute a test block.
4. Investigate a slow statement using available cursor information and an execution plan.
5. Inspect sessions, blocking, storage usage, invalid objects, and scheduler failures; run a small set of reviewed maintenance procedures in development or test.
6. In a supported IDE, select SQL or PL/SQL, request an explanation or proposed fix using authorized schema context, and review a diff before applying it to the editor.

Success means these workflows are reliable and auditable. Supporting every Oracle feature is a longer-term product goal.

## Scope and acceptance criteria

| Area | MVP deliverable | Acceptance evidence | Status |
| --- | --- | --- | --- |
| Connections | Named profiles, environment badges, service-name connections, credential references, connection test, version/container identity, capability discovery | Two databases can be used concurrently without mixing identity, credentials, or session state; missing privileges show actionable diagnostics | Implemented. Identity and capability probing are unqualified: no Oracle has answered them |
| Schema explorer | Tables, columns, keys, indexes, views, sequences, synonyms, packages, procedures, functions, triggers; source and dependency inspection | Large object lists load in pages; restricted accounts see only accessible metadata | Implemented. The dictionary views behind it are seeded tables in the stand-in, not Oracle's |
| SQL worksheet | Execute one selected statement or complete PL/SQL block, bind parameters, bounded result grid, cancellation, elapsed time, saved scripts, explicit transaction controls | DML remains uncommitted until requested; rollback works; cancellation does not affect another user's execution | Implemented. Cancellation, DDL-commit and connection-loss behaviour are unqualified - see the gap table in docs/compatibility.md |
| PL/SQL workspace | Source editor, source diff, compile package specification/body and standalone routines, compiler errors, bounded DBMS_OUTPUT, anonymous test blocks | Seeded invalid package shows correct errors, can be repaired and compiled, and produces expected test output | Implemented, least qualified area. The stand-in has no PL/SQL engine, so compile, line-level errors and DBMS_OUTPUT are exercised only against a stub driver |
| Tuning workbench | Explain plan; cached cursor plan when accessible; SQL ID/child cursor selection; current SQL statistics; saved before/after observations | A seeded slow query can be examined and compared under equivalent test conditions; estimated and measured values are clearly distinguished | Implemented. There is no optimizer behind the stand-in, so no real plan has been produced or read |
| DBA overview | Sessions and blockers, tablespace usage, invalid objects, scheduler job status/failures, connection health | Each panel shows collection time and permission/error state; unavailable data is never displayed as healthy | Implemented. Panels, permission states and collection times are covered; the underlying queries are unqualified |
| Controlled runbooks | Collect health report, recompile one selected object, gather statistics for one selected table | Mutations show exact target and parameters, require an authorized execution action, and preserve outcome and verification evidence | Implemented. All three runbooks exist with verification evidence; none has run against Oracle |
| Access and history | OIDC login, application roles, per-target access, credential isolation, execution/audit records | Direct API calls enforce the same permissions as the UI; users cannot access another user's session or restricted results | Implemented. Roles, per-target grants, revocation and audit are covered. The console signs in through the provider with authorization code and PKCE. Sign-in is qualified against Keycloak 26.3, including key rotation; not yet against a pilot's own provider or in a real browser |
| IDE copilot | OracleDataForge integration, explicit target selection, explain SQL/PLSQL, diagnose supplied errors, propose changes and test blocks, explain supplied plans | Existing DataForge connection works without credential duplication; selected source and authorized context produce a reviewable answer/diff; database execution remains a separate authorized action | Implemented against a stubbed harness and a fixture provider. Not integrated with a DataForge installation, and no model provider has been called |

Production mutation support, session termination, user/role provisioning, and arbitrary privileged scripts are outside the initial release. Development SQL is still a powerful capability and must use appropriately constrained Oracle accounts.

## IDE integration and AI copilot

The web console manages harness connections, access, diagnostics, and history. IDE adapters contribute editor context and present answers or proposed edits. Both call the same authenticated copilot backend; no adapter receives privileged Oracle credentials. In DataForge mode, the DataForge backend retains ownership of its Oracle credentials and execution; only scoped context reaches the harness. Harness history covers these copilot requests, not all independent DataForge database operations.

First adapter: an optional OracleDataForge backend bridge and embedded copilot UI. Reuse DataForge's selection callbacks, source metadata and diff components. The adapter resolves context under the current DataForge user's permissions and calls a versioned harness API. This is proposed integration work, not existing compatibility. The adapter, its contracts and its fixtures are implemented; the integration itself is still proposed.

Next adapter: a VS Code companion extension used alongside Oracle SQL Developer for VS Code. Oracle offers an official [SQL Developer extension](https://www.oracle.com/database/sqldeveloper/vscode/), and VS Code documents an extensible [Chat Participant API](https://code.visualstudio.com/api/extension-guides/ai/chat). This supports a follow-on integration candidate; it does not establish access to Oracle's extension internals or saved connections.

Use standard editor selection/document APIs and an OracleDBHarness-owned target picker. Begin with extension commands and a chat panel calling the harness API. Native chat participation is optional and must be validated against the pilot's VS Code version and installed chat capabilities. Do not depend on undocumented connection-sharing APIs or extract another extension's saved credentials.

| Integration | Release scope | Connection method |
| --- | --- | --- |
| OracleDataForge | MVP integration; contract and editor spike in milestone 1 | Same-origin backend adapter sends authorized context; versioned harness API streams answers and proposed edits; existing DataForge execution stays local |
| VS Code with Oracle SQL Developer | Next adapter after DataForge | Companion extension sends selected source, document version, user request and explicit harness target ID |
| SQL Developer desktop | Follow-on adapter | Investigate supported extension SDK for the exact installed version; external-tool handoff if appropriate |
| PL/SQL Developer, Toad for Oracle, DBeaver, DataGrip | Follow-on candidates; no compatibility claim yet | Per-product spike to verify plugin/external-tool APIs, licensing, editor context and supported versions |
| MCP-capable AI clients | Follow-on shared adapter | Expose a narrow authenticated tool catalog through MCP; clients must enforce the same target permissions and execution policies |

MCP is an integration option for clients that support it, not a universal plugin interface for Oracle IDEs. Oracle also documents SQLcl MCP integration on its SQL Developer product page; evaluate overlap later without creating a second execution path that bypasses harness authorization or history.

Initial copilot actions:

- Explain selected SQL or PL/SQL with references to the submitted code and retrieved object metadata.
- Diagnose a supplied ORA/PLS error or compiler output and propose a concrete repair.
- Draft SQL, PL/SQL changes, or anonymous test blocks grounded in accessible schema objects.
- Explain an existing plan and propose tuning experiments, distinguishing evidence from hypotheses.
- Return a versioned editor diff for review; reject application if the document changed since the request.

Interaction: select code -> choose harness target/schema -> request assistance -> retrieve permitted context -> generate answer/diff -> accept editor change -> optionally invoke a separate authorized compile/test operation. Applying a file edit must never automatically execute it in Oracle. Harness execution uses its own session and cannot inspect or commit an IDE's existing transaction.

Add a provider-neutral AI service with one configured model provider for the pilot. Route credentials through server-side secret references. Record provider/model, latency, token usage and operation references without logging raw prompts by default. Set request limits and per-user budgets; preserve ordinary database workflows when the AI provider fails.

Context consists of explicitly selected source, optional user-supplied errors/plans, target database version, and permission-filtered metadata for relevant objects. No full-schema dump, result rows, bind values, or unrelated files are sent by default. Establish an administrator-configured provider/data-sharing policy before enabling AI; show users what context categories will be shared. Key cached context by target, schema and authorization scope, and invalidate it after permission or schema changes.

Treat source comments, database object comments, error text and retrieved documents as untrusted data. They cannot grant permissions or override tool policy. The model proposes actions; backend authorization validates each actual operation. Production remains limited to curated diagnostic operations. Generated SQL must pass review and Oracle-backed checks in an authorized test environment before any claim that it works.

Inline autocomplete, autonomous repair loops, automatic index creation, and simultaneous native support for several IDEs are deferred. The MVP delivers request-driven assistance and reviewable edits.

## Execution design

Use a common operation contract:

`operation ID + actor + target + parameters + capability requirements + risk class + limits -> execution record + structured result + verification evidence`

Separate fixed diagnostic operations from arbitrary SQL execution. A SQL keyword filter is not a read-only security boundary: queries can invoke functions with side effects. Production observation uses curated diagnostic operations and an Oracle account with reviewed grants and executable-package access. Free-form worksheets are enabled only for explicitly authorized development/test profiles.

Transaction ownership belongs to a worksheet session and its user. Lease a dedicated Oracle connection until commit, rollback, or idle expiry; serialize operations within that session. Do not return a connection with an open transaction or residual package/session state to another user. Session expiry attempts rollback and closes the connection. A broken connection must be discarded, with uncertain outcomes reported explicitly.

Oracle DDL commits an open transaction, and PL/SQL can perform its own commits. Block DDL in worksheets with pending DML; compile through a separate execution session with an explicit change preview. Never promise that rollback can undo arbitrary PL/SQL or DDL. These constraints follow Oracle's [transaction behavior](https://python-oracledb.readthedocs.io/en/stable/user_guide/txn_management.html).

Initial configurable defaults: 1,000 result rows, 10 MB response cap, 30-second interactive execution budget, five-minute worksheet idle expiry, and bounded DBMS_OUTPUT/LOB previews. Enforce a total operation deadline as well as driver round-trip limits. Cancellation is best effort: report whether it completed and verify session health before reuse.

Do not split scripts on every semicolon. MVP accepts one selected SQL statement or one complete PL/SQL block; SQL*Plus commands, substitution variables, and full script compatibility are deferred. Support common scalar binds first; complex PL/SQL record/collection arguments are deferred.

Never automatically retry writes after a network timeout. Execution states should include queued, running, succeeded, failed, cancellation requested, cancelled, and outcome unknown. Request deduplication prevents duplicate dispatch but does not imply exactly-once execution inside Oracle.

## Performance and DBA boundaries

Start with a documented inventory of approved current-state metadata queries. Record the privileges and supported database versions required by each query. Use schema-scoped dictionary views where possible, and request narrowly scoped grants for DBA panels.

Use `DBMS_XPLAN` for plan display, with `DISPLAY_CURSOR` only when its required views are accessible. Actual row statistics may be unavailable unless collected; show that explicitly. Fetching an existing plan must not silently rerun the user's query. See Oracle's [DBMS_XPLAN documentation](https://docs.oracle.com/en/database/oracle/oracle-database/19/arpls/DBMS_XPLAN.html).

Exclude AWR, ASH, ADDM, SQL Tuning Advisor, and Real-Time SQL Monitoring from MVP collectors and screens. These features have edition/service and management-pack conditions; a technical permission or parameter setting does not prove entitlement. Future integrations require a per-target entitlement configuration and review against the applicable Oracle terms. See Oracle's [Licensing Information](https://docs.oracle.com/en/database/oracle/oracle-database/19/dblic/Licensing-Information.html).

Do not automatically create indexes, change optimizer parameters, or accept tuning recommendations. Comparisons record SQL/binds policy, plan, data/statistics context, elapsed time, and available execution counters; improvement must be measured on representative test data.

## Architecture

Built as proposed, with two corrections marked below.

| Component | Choice | Purpose |
| --- | --- | --- |
| Web UI | React, TypeScript, Monaco editor | Schema navigation, worksheets, PL/SQL editor, diagnostics and runbook screens |
| API | Python, FastAPI, typed request/result models | Authentication, policy enforcement, operation catalog, execution lifecycle |
| IDE adapter | TypeScript DataForge backend bridge and UI integration | Existing connection selection, scoped context, streamed answers and reviewed diffs; preserve DataForge's Node runtime |
| AI/context service | Backend module with one provider adapter initially | Permission-filtered schema context, prompt assembly, generation limits and structured proposals |
| Oracle adapter | python-oracledb, synchronous operations on a bounded thread pool - *processes cannot own a leased connection; see ADR-0002* | Oracle connectivity, dedicated worksheet sessions, diagnostics, cancellation |
| Metadata | PostgreSQL (SQLite for development and the test suite) | Profiles, access grants, scripts, execution state, observations, audit metadata |
| Identity and secrets | OIDC provider; mounted or external secret references | Central login and credentials kept out of browser and application records |
| Packaging | Container images and Docker Compose pilot deployment | Repeatable application installation near the target databases |

Keep this a modular monolith with a separate worker process boundary. One execution service owns worksheet connections for the pilot; horizontal session routing is deferred. Persist execution intent before dispatch and reconcile interrupted work after restart. Do not introduce Kubernetes or a distributed queue for the first pilot.

Prefer python-oracledb Thin mode when compatible with the target network configuration. Validate TCPS/wallet requirements in the first milestone. Native Network Encryption and other Thick-mode requirements may need Oracle Client libraries; because driver mode is process-wide, use a separately configured worker deployment if both modes are required. See the driver's [initialization documentation](https://python-oracledb.readthedocs.io/en/stable/user_guide/initialization.html).

Keep Oracle credentials server-side. Encrypt network traffic, keep secret values out of logs, parameterize built-in queries, and validate/quote database identifiers separately from bind values. Restrict which database endpoints operators can register.

Application roles: Viewer runs permitted diagnostics; Developer uses authorized worksheets and PL/SQL editing; DBA runs permitted maintenance; Administrator manages application settings and access. These roles do not bypass Oracle privileges. Associate database credential references with the permitted actor/target/capability combination; do not give every user a shared DBA connection.

Audit records contain actor, target, operation, timestamps, status, statement fingerprint, policy decision, and relevant affected-object counts. Raw SQL can contain secrets; retain it only under explicit redaction/access rules. Do not persist result rows or bind values by default. Protect audit records from ordinary application edits, and define retention and backup during the pilot.

## Repository and core records

Directories, as built:

```text
apps/web/                    Web console
integrations/dataforge/      Adapter contracts, fixtures and integration guide
services/api/                API, policy, authentication
services/worker/             Oracle execution and session ownership
services/api/copilot/        AI provider adapter and context policies
packages/contracts/          API schemas and generated client
oracle/diagnostics/          Reviewed metadata queries
oracle/runbooks/             Typed maintenance operations
oracle/grants/               Reviewed role/grant setup scripts
tests/integration/           Oracle-backed execution tests
tests/e2e/                   Complete user workflows
tests/copilot/               Grounding, permissions, prompt injection and diff evaluation
deploy/                      Local/pilot containers and configuration
docs/                        Architecture, setup, operations, compatibility
```

Beyond the list above the tree also carries `tests/unit/` for the adapter, session,
policy and statement-parser tests, and `oracle/qualification/` for the Oracle 19c
fixtures and their teardown.

Core records: ConnectionProfile, SecretReference, TargetCapability, UserTargetGrant, WorksheetSession, SavedScript, Execution, AuditEvent, RunbookDefinition, DiagnosticObservation, CopilotRequest, and ProposedEdit. AI proposals reference the target, document version and context provenance. No database passwords belong in these records.

All of these exist. The metadata schema is created and upgraded by `initialize_schema`,
which applies registered migration steps in one transaction and refuses to start
against a store it cannot bring to the expected version (see docs/operations.md,
"Upgrading"). The PostgreSQL upgrade path has a test but has not been run against
PostgreSQL yet.

## Delivery sequence

Planning estimate: 12-14 weeks with two engineers and part-time Oracle DBA support, including the DataForge copilot integration and editor prerequisites. The earlier web-only estimate was 8-10 weeks. This estimate depends on database access, identity/secrets integration, network requirements, and the joint DataForge/model-provider spike. Re-estimate after milestone 1; a solo implementation should use the same sequence with a longer timeline. DataForge-side implementation is coordinated work in its own repository.

| Milestone | Target | Work and exit gate | Status |
| --- | --- | --- | --- |
| 1. Connectivity and design spike | Week 1 | Confirm pilot workflows and database versions; obtain test environments; validate driver/network mode, authentication, grants, cancellation and DDL behavior. Validate DataForge context/diff interfaces, worksheet buffer prerequisites, delegated identity, protocol handshake and one AI provider/data-sharing configuration. Exit: executable connection and DataForge-to-harness probes plus recorded architecture decisions | **Exit gate not met, and the work continued anyway.** Architecture decisions are recorded in docs/decisions.md and the DataForge probe runs against fixtures. No Oracle test environment was ever obtained, so driver mode, network mode, authentication, grants, cancellation and DDL behaviour are all unvalidated. This is the deferral every unqualified item below inherits |
| 2. Execution foundation | Weeks 2-3 | Scaffold UI/API/metadata; implement login, target grants, credential references, connection identity, operation contract, session ownership and audit. Exit: bounded query runs against two targets with isolation and no credential leakage | Implemented; exit gate met against the stand-in only |
| 3. Developer workflow | Weeks 4-5 | Schema explorer, SQL worksheet, binds/results, commit/rollback, cancellation, PL/SQL source/compile/errors/output. Exit: developer workflow passes on Oracle 19c | Implemented; exit gate **not met** - it names Oracle 19c explicitly |
| 4. DBA and tuning workflow | Weeks 6-7 | Capability-aware diagnostic panels, plan viewer, current SQL observations, comparison and three runbooks. Exit: seeded blocking and slow-query scenarios can be investigated; a test maintenance action is verified | Implemented; exit gate met against seeded stand-in rows, not a real blocking scenario or a real plan |
| 5. DataForge copilot | Weeks 8-11 | Optional adapter, simple setup, versioned contracts, independent editor buffers, scoped metadata context, explain/fix/test/plan actions, reviewed diffs and AI evaluations. Exit: DataForge workflow passes without duplicated Oracle credentials, automatic database changes or cross-target context leakage | Implemented against fixtures and a stubbed harness; exit gate **not met** - no DataForge installation has been connected |
| 6. Pilot and release | Weeks 12-14 | Failure recovery, access tests, load checks, both-project regression checks, compatibility matrix, install/upgrade instructions, metadata backup/restore, DBA review and user pilot. Exit: release criteria below are met | Not started |

Critical dependency: get a representative Oracle 19c test database and reviewed grants during week 1. Testing only against a newer Free database is insufficient to claim 19c support.

## First implementation backlog

Item 3 is the one that was never done, and items 1, 4, 5 and 7 are only as
trustworthy as the stand-in they were built against.

1. Record target versions, environments, authentication and network requirements. **Not done** - there is no target to record.
2. Create project skeleton, CI, formatting and configuration validation. Done.
3. Provision an isolated Oracle test schema with sample data, a package, invalid object, blocking scenario and slow-query fixture. **Not done against Oracle.** The equivalent fixtures exist twice: seeded into the stand-in by `harness_worker.backend.fake`, and as reviewed Oracle DDL under `oracle/qualification/` waiting for a database to run against.
4. Implement connection identity and capability probe with secret references. Implemented; unqualified.
5. Implement authenticated per-target authorization and reviewed Oracle grants. Implemented. The grant script in `oracle/grants/` is reviewed but has never been executed.
6. Implement execution records, dedicated worksheet sessions and bounded fetch. Implemented; unqualified.
7. Demonstrate commit, rollback, cancellation, idle expiry and network-failure behavior. Demonstrated against the stand-in and a stub driver. Each of these is a documented Oracle gap; the word in the backlog item is *demonstrate*, and against Oracle it has not been.
8. Add schema navigation and one usable SQL worksheet screen. Implemented.
9. Prototype DataForge selection -> authenticated backend bridge -> harness answer -> reviewed editor diff, using a fixture before connecting a model provider; prove a capabilities handshake without exporting database credentials. Implemented, and the fixture stage is exactly where it still is.

This first vertical slice must work before broadening the dashboard or adding advanced automation. It works against the stand-in. It has not been shown to work against Oracle, which is what the item was for.

## Validation and release criteria

No gate here is fully met. Each is marked with what is missing; the three marked
partly met are the ones that do not depend on Oracle, a provider or a pilot.

- **Not met.** Integration tests on Oracle 19c plus the selected development database version; document exact tested versions and configurations. One configuration switches the backend, the API suites and the qualification suite onto a real target, and `oracle/qualification/` holds the fixtures. Nothing has been run.
- **Not met.** Exercise restricted and developer credentials; ensure missing privileges degrade individual features clearly. Degradation is implemented and covered; the privileges it degrades on are simulated.
- **Not met.** Verify multi-user session isolation, commit/rollback behavior, DDL handling, cancellation, blocked queries, connection loss, worker restart and unknown write outcomes. All implemented and regression-tested; every one is listed as an open Oracle gap in docs/compatibility.md.
- **Not met.** Verify binds, quoted identifiers, Unicode, NUMBER precision, dates/time zones, nulls and bounded LOB handling. The stand-in cannot show any of these: it has SQLite's type system, not Oracle's.
- **Partly met.** Verify UI and direct API access controls, absence of secrets/result payloads in routine logs, and no default collection from excluded pack-dependent features. Access control and log content are covered by the suite and do not depend on Oracle. Pack-dependent collection is excluded by construction: no collector references AWR, ASH, ADDM or Real-Time SQL Monitoring.
- **Partly met.** Run end-to-end developer and DBA scenarios with fixtures whose expected results are known. `tests/e2e` runs the six MVP workflows against known fixtures. It can now be pointed at Oracle by configuration, but has only been run on the stand-in.
- **Not met.** Nothing has been measured. Load target for the pilot: ten concurrent users across three registered databases, with measured connection limits and no session cross-contamination. Establish metadata-panel latency targets after measuring the test network; report database execution time separately from application overhead.
- **Not met.** Demonstrate installation from documentation and restoration of application metadata from backup. The Compose deployment and the operations guide exist; neither has been installed from scratch by someone following them, and no metadata backup has been restored. In-place upgrades go through the registered migration steps in `initialize_schema` (docs/operations.md, "Upgrading"); the PostgreSQL path has a test that has not yet been run against PostgreSQL.
- **Not met.** No provider call has been made, so no case has been evaluated. Evaluate at least 30 representative copilot cases covering explanations, compile errors, SQL/PLSQL drafts, test blocks, tuning evidence, inaccessible objects, stale source and malicious embedded instructions. Require all authorization and no-automatic-execution cases to pass; target at least 90% DBA-reviewed correctness on explain/fix cases and disclose the remaining failure patterns.
- **Partly met.** Verify stale diffs are rejected, credentials and unauthorized metadata never enter model context, provider failures leave editing usable, and accepting an edit does not execute database code. All four are implemented and covered in `tests/copilot`, against the fixture provider and a stubbed editor.
- **Not met.** No DataForge installation has been connected. Meet the DataForge integration release gates: ten-minute setup target on running services, compatible protocol negotiation, unchanged DataForge role/transaction/confirmation behavior, disabled/outage operation and published tested-version matrix.
- **Not met.** No pilot has run. At least one developer and one DBA complete the six MVP workflows across the web console and supported IDE, with no unresolved defects that risk incorrect database changes, access leakage, or misleading outcomes.

## Deferred roadmap

| Area | Follow-on scope |
| --- | --- |
| Automation clients | CLI, scheduled runbooks, CI integration, structured agent/MCP tools using the same execution policies |
| Expanded IDE/AI assistance | VS Code companion extension, other Oracle IDE adapters, inline completions, multiple providers, richer test generation and evaluated multi-step assistance |
| Advanced development | utPLSQL integration, dependency-aware deployment, schema comparison, migrations, debugging and full script support |
| Licensed tuning | Entitlement-aware AWR/ASH/ADDM and advisor integrations |
| Fleet operations | RAC and Data Guard topology, CDB-wide administration, cross-database comparisons, alerts and historical metrics |
| Lifecycle management | RMAN backup/restore, Data Pump, patching, provisioning, cloning, upgrades and disaster recovery workflows |
| Enterprise operation | Approval workflows, external audit export, stronger identity mapping, HA, multi-tenancy and distributed workers |

The web console and first IDE copilot adapter share the MVP execution, context and policy foundation. Future IDE, automation and AI clients reuse those same contracts.
