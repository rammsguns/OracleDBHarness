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

Qualify this against your provider before the pilot: the tests exercise the flow and
token verification against a stubbed provider, not a real one.

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

```bash
HARNESS_COPILOT_ENABLED=true
HARNESS_COPILOT_PROVIDER=anthropic
HARNESS_COPILOT_MODEL=claude-opus-5
HARNESS_COPILOT_API_KEY_REF=provider-api-key
```

`HARNESS_COPILOT_API_KEY_REF` names a registered `SecretReference`, not a key. Register
it the same way as a database credential.

With `HARNESS_COPILOT_PROVIDER=fake` the harness answers with fixtures and calls no
provider. Everything says so - the capabilities endpoint, the stream's `start` event
and the console banner - so a demonstration is never mistaken for a model.

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

Restoring is `psql` into an empty database followed by starting the API, which creates
anything missing and records the schema version. Practise this before the pilot: one of
the release criteria is demonstrating a restore, not just a dump.
