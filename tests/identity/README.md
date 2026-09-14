# Sign-in qualification

`tests/unit/test_oidc.py` signs its own tokens and stubs the provider. The tests here
run against a real identity provider instead, and skip unless it is configured. The
provider is Keycloak, in a disposable container. Its realm registers the console the
way docs/setup.md tells an operator to.

## Running it

```bash
docker compose -f tests/identity/keycloak/compose.yaml up -d --wait
export HARNESS_TEST_OIDC_ISSUER=http://127.0.0.1:8180/realms/engineering
uv run pytest tests/identity -v
npm --prefix apps/web test -- src/oidc.keycloak.test.ts
docker compose -f tests/identity/keycloak/compose.yaml down
```

CI runs the same thing in the `identity` job. Use `127.0.0.1` rather than
`localhost`: Keycloak names its issuer after the host it was reached on, and the API
compares issuers exactly.

| File | Runs | Checks |
| --- | --- | --- |
| `test_keycloak.py` | The API's discovery and token verification | Discovery and exact issuer. PKCE required. CORS on the token endpoint. The access token accepted for a registered account. Other applications' tokens and ID tokens refused. Key rotation followed without a restart |
| `apps/web/src/oidc.keycloak.test.ts` | The console's `authorizationUrl` and `completeSignIn` | A complete sign-in using the console's own request parameters. A replayed code refused by the provider |

The realm's user (`alice` / `qualification-only`) and the container's admin account
(`admin` / `admin`) are fixtures of a throwaway container. Never reuse them.
`HARNESS_TEST_KEYCLOAK_ADMIN` and `HARNESS_TEST_KEYCLOAK_ADMIN_PASSWORD` override the
admin account for a Keycloak you run some other way.

## Browser qualification

Everything above runs under Python or Node; no browser has been involved. `tests/browser`
drives Chromium through the console's own sign-in and checks what a person actually gets:

| Area | Checked |
| --- | --- |
| Deployment | The origin reaches the API through its proxy; OIDC mode with no OIDC warning; the deployment serves the issuer and client ID the run was told to expect; the provider's discovery names that issuer; the proxy serves `/auth/callback` to the console; development tokens are refused |
| Refused identity | An account the provider authenticates but the harness has not registered is refused, told its subject, and keeps no token |
| Sign-in and callback | The console's authorization request (PKCE S256, state, `<origin>/auth/callback`); the login; the callback; the code gone from the address bar; nothing in `localStorage` or `sessionStorage` |
| API access | The console's token is accepted for the account the console shows; no token, an altered signature and a non-bearer header are refused; an administrator endpoint and an ungranted target refuse a developer |
| Sign-out | Back to the sign-in screen, no authenticated call after a reload. Recorded, not judged: the token stays valid at the API until it expires, and the provider's session survives |
| Expiry | The console returns to sign-in when the token expires, and the API refuses the expired token |

It has three modes, and **only `pilot` can qualify anything about the pilot**. The report
states the mode and a verdict that says so.

| Mode | Identity provider | API and console | Qualifies |
| --- | --- | --- | --- |
| `rehearsal` | A stand-in started by the run (`tests/browser/stub_provider.py`) | Started locally | Nothing but this tooling. For a machine without Docker |
| `fixture` | The Keycloak realm in `keycloak/` | Started locally: API in OIDC mode over a throwaway store; the production console build under `vite preview` on `127.0.0.1:5173` | The console against a real provider registered as docs/setup.md says. Not the pilot's registration, origin or nginx proxy. CI runs this in the `identity` job |
| `pilot` | The pilot's own registration | The deployed console origin; nothing is started | The pilot registration, deployed origin and proxy, when every check passes and none is skipped |

```bash
uv sync --group browser
npm --prefix apps/web install          # rehearsal and fixture build and serve the console

# Rehearsal, on any machine:
HARNESS_BROWSER_MODE=rehearsal uv run --group browser python -m tests.browser run

# Fixture, with the realm running (see above):
HARNESS_BROWSER_MODE=fixture uv run --group browser python -m tests.browser run
```

Chromium comes from `uv run --group browser playwright install chromium`, or set
`HARNESS_BROWSER_CHANNEL=chrome` or `msedge` to use an installed browser. Port 5173 must
be free for the local modes: the realm's redirect URIs fix it.

### Against the pilot

Pilot mode signs in **by hand**. A headed browser opens and the run waits while you log
in, so any provider, second factor or conditional-access policy works, and no pilot
password is given to the run; form login is refused in this mode. Before running:

1. Deploy with `HARNESS_AUTH_MODE=oidc`, the pilot's issuer and client ID, and the
   console's redirect URI registered as `<origin>/auth/callback`.
2. Have two provider accounts: one registered in the harness with the `developer` role and
   **not** `administrator`, and one the provider knows but the harness does not.
3. Note the ID of a target the registered account has no grant on.
4. Decide how long you will wait for expiry: at least the access token lifetime the
   provider issues to the console (often 60 minutes; a shorter lifetime for this client
   makes the run practical).

```bash
export HARNESS_BROWSER_MODE=pilot
export HARNESS_BROWSER_CONSOLE_ORIGIN=https://harness.pilot.example
export HARNESS_BROWSER_OIDC_ISSUER=https://login.example.com/<tenant>/v2.0
export HARNESS_BROWSER_CLIENT_ID=<console client id>
export HARNESS_BROWSER_CHECK_UNREGISTERED=1       # you will be asked to use that account first
export HARNESS_BROWSER_FORBIDDEN_TARGET_ID=<target id>
export HARNESS_BROWSER_EXPIRY_WAIT_SECONDS=3900
export HARNESS_BROWSER_REPORT=./browser-report/pilot.md
uv run --group browser python -m tests.browser run
```

`python -m tests.browser config` validates the variables and stops. Every missing or
contradictory setting is listed at once; pilot mode refuses the local realm's issuer.

Exit status: 0 when the run did what its mode asks (in pilot mode, `QUALIFIED`); 1 when a
check failed, or a pilot run skipped a check (`NOT QUALIFIED`; with
`HARNESS_BROWSER_ALLOW_PARTIAL=1` it exits 0 as `PARTIAL`, still not qualified); 2 when a
prerequisite such as the provider, the origin, Playwright or a browser is missing, named in
the output.

**Evidence without secrets.** The report is Markdown appended to `HARNESS_BROWSER_REPORT`
plus a JSON sidecar: harness commit, mode, verdict, origin, issuer, client ID, browser
version, platform and each check's outcome. Tokens are read from the console's own
requests and kept in memory; every token, code, PKCE value and fixture password the run
sees is registered, and writing refuses any text containing one, anything shaped like a
JWT, or a `code=` parameter. Subjects are not written either. Screenshots are not taken.
Record a pilot run in docs/compatibility.md, "Identity providers", with its report.

## Checking a pilot's own provider

This suite relies on Keycloak's login form and admin API, so it cannot be pointed at
Entra ID, Okta or Auth0. The browser run's pilot mode (above) covers these checks and
more, and records them; the list below is what to look at when it fails, or when it cannot
be run. Deploy with `HARNESS_AUTH_MODE=oidc` and check, in a browser:

1. `GET /api/v1/system/info` lists no OIDC warning, and `GET /api/v1/auth/oidc`
   returns the provider's endpoints rather than `identity_provider_unavailable`.
2. "Sign in with your identity provider" reaches the provider's login page. A
   `redirect_uri` error there means the registered redirect URI is not
   `<console origin>/auth/callback`.
3. After login the console does not report a CORS error. If it does, the provider
   does not allow the console's origin at its token endpoint.
4. The console says the account is not registered, and names a subject. Register that
   subject (`POST /api/v1/admin/users`), then sign in again. If the console says the
   token is not valid instead, the access token's audience is wrong; see the
   provider-specific notes in docs/setup.md.
5. The account signs in and sees its own name.
