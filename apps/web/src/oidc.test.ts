/**
 * The console's half of the authorization code flow with PKCE.
 *
 * The provider is stubbed: these check what the console sends and what it will accept
 * back, not that any particular provider interoperates. That is the qualification
 * step in docs/setup.md.
 */

import { afterEach, describe, expect, it, vi } from "vitest";

import { type OidcSignInConfig, SignInError, authorizationUrl, completeSignIn } from "./oidc";

const config: OidcSignInConfig = {
  issuer: "https://login.example.internal/realms/engineering",
  clientId: "oracledbharness-console",
  authorizationEndpoint: "https://login.example.internal/realms/engineering/auth",
  tokenEndpoint: "https://login.example.internal/realms/engineering/token",
  scopes: ["openid", "profile", "email"],
  audience: null,
};

const ORIGIN = "https://harness.example.internal";

function tokenResponse(body: Record<string, unknown>, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

async function start(storage: Storage = sessionStorage) {
  const url = new URL(await authorizationUrl(config, storage, ORIGIN));
  return { url, state: url.searchParams.get("state") ?? "" };
}

async function sha256base64url(text: string): Promise<string> {
  const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text)));
  let binary = "";
  for (const byte of digest) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

afterEach(() => {
  sessionStorage.clear();
  vi.useRealTimers();
});

describe("starting a sign-in", () => {
  it("asks for an authorization code with an S256 challenge and a fresh state", async () => {
    const { url, state } = await start();

    expect(url.origin + url.pathname).toBe(config.authorizationEndpoint);
    expect(url.searchParams.get("response_type")).toBe("code");
    expect(url.searchParams.get("client_id")).toBe("oracledbharness-console");
    expect(url.searchParams.get("redirect_uri")).toBe(`${ORIGIN}/auth/callback`);
    expect(url.searchParams.get("scope")).toBe("openid profile email");
    expect(url.searchParams.get("code_challenge_method")).toBe("S256");
    expect(url.searchParams.has("audience")).toBe(false);
    expect(state.length).toBeGreaterThanOrEqual(43);

    const second = await start();
    expect(second.state).not.toBe(state);
  });

  it("sends the challenge of the verifier it later redeems the code with", async () => {
    const { url, state } = await start();
    const fetchImpl = vi.fn().mockResolvedValue(tokenResponse({ access_token: "at", token_type: "Bearer" }));

    await completeSignIn(config, `?code=c1&state=${state}`, sessionStorage, fetchImpl);
    const body = new URLSearchParams(fetchImpl.mock.calls[0][1].body as URLSearchParams);
    const verifier = body.get("code_verifier") ?? "";

    expect(verifier.length).toBeGreaterThanOrEqual(43);
    expect(verifier.length).toBeLessThanOrEqual(128);
    expect(await sha256base64url(verifier)).toBe(url.searchParams.get("code_challenge"));
  });

  it("names the audience only when the deployment asks it to", async () => {
    const url = new URL(
      await authorizationUrl({ ...config, audience: "oracledbharness" }, sessionStorage, ORIGIN),
    );
    expect(url.searchParams.get("audience")).toBe("oracledbharness");
  });
});

describe("completing a sign-in", () => {
  it("redeems the code as a public client and returns the access token", async () => {
    const { state } = await start();
    const fetchImpl = vi
      .fn()
      .mockResolvedValue(tokenResponse({ access_token: "at_123", token_type: "Bearer", expires_in: 300 }));

    const tokens = await completeSignIn(config, `?code=c1&state=${state}`, sessionStorage, fetchImpl);

    expect(tokens).toEqual({ accessToken: "at_123", expiresIn: 300 });
    const [endpoint, init] = fetchImpl.mock.calls[0];
    expect(endpoint).toBe(config.tokenEndpoint);
    expect(init.method).toBe("POST");
    const body = new URLSearchParams(init.body as URLSearchParams);
    expect(body.get("grant_type")).toBe("authorization_code");
    expect(body.get("code")).toBe("c1");
    expect(body.get("client_id")).toBe("oracledbharness-console");
    expect(body.get("redirect_uri")).toBe(`${ORIGIN}/auth/callback`);
    // A public client: nothing secret goes over the wire.
    expect(body.has("client_secret")).toBe(false);
  });

  it("refuses a callback whose state this tab did not issue", async () => {
    await start();
    const fetchImpl = vi.fn();
    await expect(
      completeSignIn(config, "?code=stolen&state=someone-elses", sessionStorage, fetchImpl),
    ).rejects.toThrow(/does not match/);
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("refuses a callback when no sign-in was started in this tab", async () => {
    const fetchImpl = vi.fn();
    await expect(
      completeSignIn(config, "?code=c1&state=anything", sessionStorage, fetchImpl),
    ).rejects.toThrow(/no sign-in in progress/);
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("uses the pending sign-in once, so a replayed callback is refused", async () => {
    const { state } = await start();
    const fetchImpl = vi.fn().mockResolvedValue(tokenResponse({ access_token: "at" }));
    await completeSignIn(config, `?code=c1&state=${state}`, sessionStorage, fetchImpl);

    await expect(
      completeSignIn(config, `?code=c1&state=${state}`, sessionStorage, fetchImpl),
    ).rejects.toThrow(SignInError);
    expect(fetchImpl).toHaveBeenCalledTimes(1);
  });

  it("clears the pending sign-in even when the callback is refused", async () => {
    await start();
    await expect(
      completeSignIn(config, "?code=c1&state=wrong", sessionStorage, vi.fn()),
    ).rejects.toThrow();
    expect(sessionStorage.length).toBe(0);
  });

  it("refuses an attempt that was started too long ago", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    const { state } = await start();
    vi.setSystemTime(Date.now() + 11 * 60 * 1000);
    const fetchImpl = vi.fn();
    await expect(
      completeSignIn(config, `?code=c1&state=${state}`, sessionStorage, fetchImpl),
    ).rejects.toThrow(/expired/);
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("reports an error the provider sent back instead of a code", async () => {
    await start();
    await expect(
      completeSignIn(
        config,
        "?error=access_denied&error_description=User+is+not+assigned+to+this+application",
        sessionStorage,
        vi.fn(),
      ),
    ).rejects.toThrow(/not assigned to this application/);
  });

  it("reports a refused code exchange with the provider's reason", async () => {
    const { state } = await start();
    const fetchImpl = vi
      .fn()
      .mockResolvedValue(tokenResponse({ error: "invalid_grant", error_description: "PKCE verification failed" }, 400));
    await expect(
      completeSignIn(config, `?code=c1&state=${state}`, sessionStorage, fetchImpl),
    ).rejects.toThrow(/PKCE verification failed/);
  });

  it("refuses a token response without an access token", async () => {
    const { state } = await start();
    const fetchImpl = vi.fn().mockResolvedValue(tokenResponse({ id_token: "only-an-id-token" }));
    await expect(
      completeSignIn(config, `?code=c1&state=${state}`, sessionStorage, fetchImpl),
    ).rejects.toThrow(/no access token/);
  });

  it("explains a token endpoint the browser could not reach", async () => {
    const { state } = await start();
    const fetchImpl = vi.fn().mockRejectedValue(new TypeError("Failed to fetch"));
    await expect(
      completeSignIn(config, `?code=c1&state=${state}`, sessionStorage, fetchImpl),
    ).rejects.toThrow(/CORS/);
  });
});
