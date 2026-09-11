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

## Checking a pilot's own provider

This suite relies on Keycloak's login form and admin API, so it cannot be pointed at
Entra ID, Okta or Auth0. For the pilot's registration, deploy with
`HARNESS_AUTH_MODE=oidc` and check, in a browser:

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
