# Setup

## Local development

Requirements: Python 3.11+ (via [uv](https://docs.astral.sh/uv/)) and Node 20+.

```bash
uv sync
cp .env.example .env
uv run python -m harness_api.seed
uv run uvicorn harness_api.app:create_app --factory --port 8000
```

In a second terminal:

```bash
npm --prefix apps/web install
npm --prefix apps/web run dev
```

Open http://localhost:5173 and sign in as `dev@example.internal`, `dba@example.internal`,
`viewer@example.internal` or `admin@example.internal`. In development identity mode the
harness signs its own tokens; roles come from the seeded account record, not from what
you type.

The seed creates three targets on the **local stand-in backend**, not a real database:

| Target | Environment | Worksheets | Mutating runbooks |
| --- | --- | --- | --- |
| development | development | yes | yes |
| test | test | yes | yes |
| production | production | no | no |

The console shows a standing warning while the stand-in is configured. Nothing you see
in this mode is evidence about Oracle.

### Talking to a real Oracle database locally

For a disposable real-database metadata store, use the reviewed manifest workflow:

```bash
uv sync --extra oracle
cp .env.real.example .env
cp databases.example/targets.json.example databases/targets.json
# Put each named password file in databases/secrets/ and chmod 600 each file.
# Review host, service, wallet directory, users, and environment in the manifest.
uv run python scripts/bootstrap_targets.py --manifest databases/targets.json --probe
```

Run `oracle/grants/verify_harness_grants.sql` as each Oracle account before onboarding,
for example `sqlplus /nolog` followed by a credential-safe `CONNECT` method and
`@oracle/grants/verify_harness_grants.sql`. It only reads `USER_ROLE_PRIVS`,
`SESSION_PRIVS`, `ALL_TAB_PRIVS`, and `USER_TS_QUOTAS`; review missing diagnostic
grants and elevated-privilege warnings with a DBA. Never place a password in a
shell command line.

The manifest names D1, D2, D3, D4 and read-only lab profiles. D3 uses a wallet and
is observation-only. Every lab profile is marked `production` with worksheets and
mutating runbooks disabled. The command refuses missing or permissive secret files,
endpoint or secret drift, and incompatible auth mode. In `oidc` mode, replace the
sample users with each provider's exact token `sub` in both `subject` and `oidcSub`.
The probe prints version, container, database character set, and diagnostic
capability results. The script reads character set separately from the target
identity model through `NLS_DATABASE_PARAMETERS`.
Never run fixture setup or teardown on lab profiles.

```bash
HARNESS_ORACLE_BACKEND=oracledb
HARNESS_ORACLE_DRIVER_MODE=thin
HARNESS_ALLOWED_ENDPOINTS=oracle-dev.internal:1521
```

Then register the credential and the target:

```bash
curl -X POST localhost:8000/api/v1/admin/secrets -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"name":"oracle-app","provider":"file","locator":"oracle_app.password"}'

curl -X POST localhost:8000/api/v1/admin/targets -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"name":"dev19c","environment":"development","host":"oracle-dev.internal",
       "port":1521,"serviceName":"ORCLPDB1","username":"harness_app",
       "defaultSchema":"HARNESS_APP","secretReference":"oracle-app",
       "worksheetsEnabled":true}'
```

Run the reviewed grants first - `oracle/grants/harness_roles.sql` - and read them
before you do. Then `POST /api/v1/targets/{id}/test` to prove identity and discover
capabilities.

Thick mode needs Oracle Client libraries and a separately deployed worker, because the
driver mode is process wide. See ADR-0002 and the driver's
[initialization documentation](https://python-oracledb.readthedocs.io/en/stable/user_guide/initialization.html).

## Pilot deployment

```bash
cd deploy
cp .env.example .env          # issuer, JWKS URL, endpoint allowlist
# create the secret files - see deploy/secrets/README.md
docker compose up -d --build
```

The console is on `127.0.0.1:8080` and proxies `/api` to the API container, so the two
share an origin and no CORS configuration is needed. Both ports are bound to loopback;
put your own TLS terminator in front.

### Signing in through your identity provider

The console signs people in with the OIDC authorization code flow and PKCE, as a
public client. Register it with your provider as:

- **Client type:** public (a single-page application). It has no client secret; the
  console could not keep one.
- **Redirect URI:** `https://<console origin>/auth/callback` - for example
  `http://127.0.0.1:8080/auth/callback` before a TLS terminator is in front.
- **Grant:** authorization code, with PKCE required (`S256`).
- **Web origin / CORS:** the console origin. The browser redeems the code at the
  token endpoint directly, so the provider must allow that origin there.
- **Access token audience:** `oracledbharness` (`HARNESS_OIDC_AUDIENCE`). The API
  verifies the access token's `iss`, `aud`, signature and expiry against
  `HARNESS_OIDC_ISSUER` and `HARNESS_OIDC_JWKS_URL`. Keycloak needs an audience
  mapper; Entra ID needs the API's scope in `HARNESS_OIDC_SCOPES`; Auth0 needs
  `HARNESS_OIDC_REQUEST_AUDIENCE=true`.

Set `HARNESS_OIDC_CLIENT_ID`. The authorization and token endpoints are read from the
issuer's discovery document, whose `issuer` must equal `HARNESS_OIDC_ISSUER` exactly;
set `HARNESS_OIDC_AUTHORIZATION_ENDPOINT` and `HARNESS_OIDC_TOKEN_ENDPOINT` to skip
discovery.

Signing in proves who someone is; it does not give them anything. The account has to
exist in the harness (`POST /api/v1/admin/users`) with its roles and target grants, and
roles in the token are ignored.

What to expect:

- The access token lives in the tab's memory only. A reload means signing in again,
  which the provider's own session usually turns into a single redirect.
- There is no refresh. When the token expires the console returns to the sign-in
  screen and says so.
- Signing out ends the console session, not the provider's.

Sign-in is qualified against Keycloak (docs/compatibility.md, "Identity providers"),
not against other providers or your registration. Before the pilot, sign in through
the console with your provider, using the checklist in tests/identity/README.md.
For autonomous agent qualification of the compose stack behind nginx using the Keycloak
fixture as an IdP stand-in, see `deploy/identity-standin-drill.sh` and `tests/identity/README.md`
("Stand-in qualification"); plainly stated, that is an AI agent test stand-in, not the
pilot registration.

Providers report the subject (`sub`) as whatever they choose. Keycloak uses a UUID;
Entra ID uses an id that differs for each application. Register that value, not a
user name or email. When an unregistered account signs in, the console names its
subject.

Before the pilot, confirm:

- `HARNESS_AUTH_MODE=oidc` with a real issuer, and `HARNESS_OIDC_CLIENT_ID` set. The
  development mode refuses to start outside `HARNESS_ENV=development` unless you
  explicitly override it.
- `HARNESS_ALLOWED_ENDPOINTS` is set. An empty allowlist lets an operator register any
  reachable database.
- `HARNESS_ORACLE_BACKEND=oracledb`.
- The secret files exist and are `chmod 600`.

`GET /api/v1/system/info` lists the configuration warnings the deployment currently
has. It is the fastest way to check all of the above at once.

## Enabling the copilot

The copilot is off until an administrator turns it on. Before you do, decide and
record your data-sharing policy: `docs/copilot.md` describes exactly what leaves the
harness and what never does.

The copilot uses a model served on the harness's own host. `HARNESS_COPILOT_LOCAL_ONLY`
(default `true`) refuses anything else; see "Where it goes" in `docs/copilot.md`.

```bash
HARNESS_COPILOT_ENABLED=true
HARNESS_COPILOT_PROVIDER=local
HARNESS_COPILOT_BASE_URL=http://127.0.0.1:8080/v1
HARNESS_COPILOT_MODEL=<the id the local server lists under /v1/models>
HARNESS_COPILOT_API_KEY_REF=local-model-key
```

`HARNESS_COPILOT_API_KEY_REF` names a registered `SecretReference`, not a key. Register
it the same way as a database credential. Leave it empty for a local server that asks
for no key. The base URL must be `http` to 127.0.0.0/8 or `::1`; anything else stops
the API at startup. A local server is called once per request, with no retries.

Under Compose (`deploy/compose.yaml`), 127.0.0.1 inside the API container is the
container, not the host, so a model served on the host is out of reach from the
default bridge network. Either run the model in a container that shares the API's
network namespace (`network_mode: "service:api"`), or give the API the host's network
(`network_mode: host`, which also means its published port no longer applies). Do not
point the base URL at the bridge gateway: that is not loopback, and it is refused.

The local server itself is outside the harness. If it listens on every interface
(`0.0.0.0`), other hosts can reach it directly; bind it to `127.0.0.1` unless that is
intended.

A cloud provider (`HARNESS_COPILOT_PROVIDER=anthropic`, with the `copilot` extra) is
still in the code but refused until `HARNESS_COPILOT_LOCAL_ONLY=false`. Turning that off
sends prompts off the host and is a data-sharing decision; the API warns while it is off.

Each provider call is bounded by `HARNESS_COPILOT_MAX_OUTPUT_TOKENS` (default 8000;
thinking counts against it), `HARNESS_COPILOT_REQUEST_TIMEOUT_SECONDS` (600) and
`HARNESS_COPILOT_PROVIDER_MAX_RETRIES` (2). A retried attempt can be billed as well as
the one that answers, so lower the retries if spend per request has to be predictable.
The provider package is the `copilot` extra: `uv sync --extra copilot`.

With `HARNESS_COPILOT_PROVIDER=fake` the harness answers with fixtures and calls no
provider. Everything says so - the capabilities endpoint, the stream's `start` event
and the console banner - so a demonstration is never mistaken for a model.

### Kiwi's read-only lookups

`HARNESS_KIWI_ENABLED=true` (off by default, and only with the copilot on) lets Kiwi
run reviewed catalog operations marked `-- @kiwi: allowed` while it answers: object
status, errors, source, dependencies, columns, cursor statistics, scheduler history
and the like. Each lookup runs as the requesting user on the target the request
names (`profileId`), through the same authorisation, limits and audit as the console.
What Kiwi fetches is sent to the provider, so the data-sharing approval has to cover
it; the startup log warns while the flag is on. See `docs/copilot.md`.

One request is bounded by `HARNESS_KIWI_MAX_STEPS` (8 model turns),
`HARNESS_KIWI_MAX_TOOL_CALLS` (12), `HARNESS_KIWI_MAX_TOKENS` (200000, input and
output together), `HARNESS_KIWI_MAX_WALL_SECONDS` (180), `HARNESS_KIWI_MAX_ROWS_PER_TOOL`
(200), `HARNESS_KIWI_MAX_RESULT_BYTES` (16384 per lookup) and
`HARNESS_KIWI_MAX_TOOL_BYTES` (98304 per request). A request that reaches one of them
stops asking the model for lookups and ends with what it has, marked as partial.

`HARNESS_KIWI_MAX_REVISIONS` (1) is how many times the harness may send a diagnosis back
to the model when its claim checks find a statement the lookups or Oracle's semantics
contradict (see [copilot.md](copilot.md#claim-checks)). A revision is one more model turn
with no tools; it is not counted in `HARNESS_KIWI_MAX_STEPS`, but it is held to the token
bound and to its own time bound, `HARNESS_KIWI_REVISION_MAX_WALL_SECONDS` (120): a long
diagnosis ends close to `HARNESS_KIWI_MAX_WALL_SECONDS`, which would leave no time to
revise it. `0` turns revision off and still reports the findings.

`cursor_search` and `cursor_plan` can return SQL text from the shared pool, which may
contain literals. Remove their `@kiwi: allowed` header if that is not acceptable.
`explain_plan_rows` is not offered: its PLAN_TABLE belongs to one session.

`HARNESS_KIWI_STANDARDS_FILE` (empty by default) points at a JSON file of team PL/SQL
standards that Kiwi follows and cites. See [copilot.md](copilot.md#playbooks-and-standards)
for the accepted keys. A file that cannot be read or parsed shows as an error in the
capabilities endpoint and fails each Kiwi request with `configuration_error`.

`HARNESS_KIWI_CAPTURE_EVIDENCE` (off by default) is for evaluations. When on, each lookup
also streams an `evidence` event holding what the model was shown, cut to
`HARNESS_KIWI_EVIDENCE_MAX_BYTES` with the cut stated, and the explainer adds its
intermediate summaries beside the lookups they summarise. The content is the catalog
result already sent to the model, so it holds no application rows or bind values, but it
does hold source text: keep it off outside an evaluation. The evaluation runner turns it
on for its own throwaway harness and stores the events in the report.

## Connecting OracleDataForge

See `integrations/dataforge/README.md`. In short: issue an integration credential with
the `copilot:assist` scope, configure `DATAFORGE_HARNESS_ENABLED`,
`DATAFORGE_HARNESS_URL` and `DATAFORGE_HARNESS_TOKEN` on the DataForge server, and use
its Test connection action.

## Backing up

The metadata store holds profiles, grants, scripts, execution history, audit events
and copilot records. It holds no database password and no result rows.

```bash
docker compose exec metadata pg_dump -U harness harness > harness-metadata.sql
```

Restoring is `psql` into an empty database followed by starting the API against it:

```bash
docker compose exec -T metadata psql -U harness -d postgres -c 'CREATE DATABASE harness_restored'
docker compose exec -T metadata psql -U harness -d harness_restored < harness-metadata.sql
HARNESS_METADATA_URL=postgresql+psycopg://harness@metadata:5432/harness_restored   docker compose up -d --force-recreate api
```

The API brings the restored store to the schema version it expects, and reconciles any work
that was in flight when the dump was taken — a restored store is a restarted deployment as
far as `docs/operations.md` is concerned, so check
`GET /api/v1/admin/reconciliation` afterwards.

Do not take this on trust. `deploy/backup-restore-drill.sh` performs the whole drill —
clean install, seed, dump, restore into a fresh database, cut the API over, and check that
nothing was lost — and prints its timings. It runs on every CI build as the
"Compose install and restore" job, and you can run it yourself:

```bash
deploy/backup-restore-drill.sh          # tears the stack down afterwards
deploy/backup-restore-drill.sh --keep   # leaves it running to poke at
```

It is a drill, not a rehearsal of your pilot: it uses throwaway secrets, a placeholder
OIDC issuer and no Oracle target, because what it is testing is the metadata store's
continuity. Identity belongs to the sign-in setup above, and the dump deliberately
contains no password — only the secret *references*, which the drill asserts.

### Upgrade drill

To qualify that metadata store upgrades retain existing history, profiles, grants and
executions without data loss, `deploy/upgrade-drill.sh` exercises an end-to-end upgrade drill
against a disposable PostgreSQL instance:

1. Starts a throwaway `postgres:16-alpine` container on a free loopback port.
2. Checks out the release baseline (defaulting to the initial commit `c31c97e` at schema version 3,
   falling back to `530e9c2` at schema version 4 if the oldest commit cannot run against Postgres)
   into a detached temporary worktree.
3. Initializes the store and seeds representative data (targets, users, grants, executions,
   worksheet sessions, saved scripts, audit records) using the baseline code.
4. Takes a snapshot of table row counts and identifying record values, and confirms the baseline
   schema version.
5. Runs `initialize_schema` using the current worktree's code to migrate the store to schema
   version 5.
6. Verifies that all pre-upgrade rows are retained with unchanged identifying values, new tables
   (`store_owner`, `execution_runtimes`, `copilot_tool_calls`, `proposed_edit_parts`) exist with
   valid defaults, and new columns carry expected default values.
7. Re-runs `initialize_schema` to verify that upgrade execution is idempotent.

```bash
deploy/upgrade-drill.sh                  # tears down container and worktree afterwards
deploy/upgrade-drill.sh --keep           # leaves container running and preserves snapshots
deploy/upgrade-drill.sh --from <commit>  # overrides the baseline commit
```

Temporary files go under `$TMPDIR` (default `/tmp`). The container bind-mounts the password
file from there, so on a host where the docker daemon does not see the caller's `/tmp` (a
sandbox with a private `/tmp`, for example), point `TMPDIR` at a directory under your home.

What it does not cover:
* Identity: uses throwaway local developer/DBA accounts rather than an external OIDC provider.
* Oracle: no live target is contacted; store continuity across schema migrations is what is under test.
* Secret provisioning: uses throwaway passwords and secret references.
