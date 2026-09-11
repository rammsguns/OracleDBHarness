// @vitest-environment node
/**
 * The console's own sign-in code, run against a real identity provider.
 *
 * oidc.test.ts checks this module against a stubbed token endpoint. Here the
 * authorization URL it builds goes to a running Keycloak, a person's login is
 * submitted, and the callback is redeemed by completeSignIn over the network. A
 * parameter the provider does not accept fails here, not in the pilot.
 *
 * Skips unless HARNESS_TEST_OIDC_ISSUER names the realm; see tests/identity/README.md.
 * The Node environment is deliberate: jsdom has no fetch of its own, and the module
 * takes its storage and origin as arguments.
 */

import { describe, expect, it } from "vitest";

import { type OidcSignInConfig, authorizationUrl, completeSignIn } from "./oidc";

const ISSUER = process.env.HARNESS_TEST_OIDC_ISSUER ?? "";
// Fixtures of the disposable realm in tests/identity/keycloak/realm.json.
const USERNAME = "alice";
const PASSWORD = "qualification-only";
const ORIGIN = "http://127.0.0.1:5173";

class MemoryStorage implements Storage {
  private items = new Map<string, string>();
  get length() {
    return this.items.size;
  }
  clear() {
    this.items.clear();
  }
  getItem(key: string) {
    return this.items.get(key) ?? null;
  }
  key(index: number) {
    return [...this.items.keys()][index] ?? null;
  }
  removeItem(key: string) {
    this.items.delete(key);
  }
  setItem(key: string, value: string) {
    this.items.set(key, value);
  }
}

async function consoleConfig(): Promise<OidcSignInConfig> {
  // What GET /api/v1/auth/oidc serves for this realm with the documented settings.
  const discovery = (await (
    await fetch(`${ISSUER}/.well-known/openid-configuration`)
  ).json()) as Record<string, string>;
  return {
    issuer: ISSUER,
    clientId: "oracledbharness-console",
    authorizationEndpoint: discovery.authorization_endpoint,
    tokenEndpoint: discovery.token_endpoint,
    scopes: ["openid", "profile", "email"],
    audience: null,
  };
}

/** Follow the authorization URL as a browser would, log in, and return the callback. */
async function logIn(url: string): Promise<URL> {
  const page = await fetch(url, { redirect: "manual" });
  expect(page.status).toBe(200);
  const cookies = page.headers
    .getSetCookie()
    .map((cookie) => cookie.split(";")[0])
    .join("; ");
  const action = /action="([^"]*login-actions\/authenticate[^"]*)"/.exec(await page.text());
  expect(action, "Keycloak did not serve its login form").not.toBeNull();

  const submitted = await fetch(action![1].replace(/&amp;/g, "&"), {
    method: "POST",
    redirect: "manual",
    headers: { "Content-Type": "application/x-www-form-urlencoded", Cookie: cookies },
    body: new URLSearchParams({ username: USERNAME, password: PASSWORD, credentialId: "" }),
  });
  expect(submitted.status).toBe(302);
  return new URL(submitted.headers.get("location")!);
}

function claims(token: string): Record<string, unknown> {
  return JSON.parse(Buffer.from(token.split(".")[1], "base64url").toString("utf8"));
}

describe.skipIf(!ISSUER)("console sign-in against Keycloak", () => {
  it("builds a URL the provider accepts and redeems the code it returns", async () => {
    const config = await consoleConfig();
    const storage = new MemoryStorage();

    const callback = await logIn(await authorizationUrl(config, storage, ORIGIN));
    expect(`${callback.origin}${callback.pathname}`).toBe(`${ORIGIN}/auth/callback`);

    const tokens = await completeSignIn(config, callback.search, storage);
    const accessToken = claims(tokens.accessToken);
    expect(accessToken.iss).toBe(ISSUER);
    expect(accessToken.azp).toBe("oracledbharness-console");
    expect([accessToken.aud].flat()).toContain("oracledbharness");
    expect(tokens.expiresIn).toBeGreaterThan(0);
    // The pending sign-in is spent, whatever happened.
    expect(storage.length).toBe(0);
  });

  it("cannot redeem the same callback twice", async () => {
    const config = await consoleConfig();
    const storage = new MemoryStorage();
    const callback = await logIn(await authorizationUrl(config, storage, ORIGIN));
    await completeSignIn(config, callback.search, storage);

    await expect(completeSignIn(config, callback.search, storage)).rejects.toThrow(
      /no sign-in in progress/,
    );
  });

  it("reports the provider's refusal of a code that was already used", async () => {
    const config = await consoleConfig();
    const first = new MemoryStorage();
    const callback = await logIn(await authorizationUrl(config, first, ORIGIN));
    const pending = first.getItem("oracledbharness.oidc.pending")!;
    await completeSignIn(config, callback.search, first);

    // Replay with the pending state restored: only the provider now stands between
    // an intercepted code and a second session.
    const replay = new MemoryStorage();
    replay.setItem("oracledbharness.oidc.pending", pending);
    await expect(completeSignIn(config, callback.search, replay)).rejects.toThrow(
      /refused the code exchange/,
    );
  });
});
