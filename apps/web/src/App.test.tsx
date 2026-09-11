/**
 * Signing in to a pilot deployment, end to end through the console.
 *
 * With HARNESS_AUTH_MODE=oidc the console has to get a person from the sign-in
 * screen to the identity provider and back with an access token. These tests land on
 * the callback URL the way a browser would after the provider redirects, and check
 * that the console redeems the code, adopts the token, and scrubs the code from the
 * address bar - or, for a callback it did not ask for, refuses without signing anyone
 * in.
 */

import { StrictMode } from "react";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Me, SystemInfo } from "./api";
import { App } from "./App";
import { type OidcSignInConfig, authorizationUrl } from "./oidc";

const systemInfo = vi.fn();
const oidcSignInConfig = vi.fn();
const signInWithAccessToken = vi.fn();
const targets = vi.fn();

vi.mock("./api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api")>();
  return {
    ...actual,
    oidcSignInConfig: (...args: unknown[]) => oidcSignInConfig(...args),
    signInWithAccessToken: (...args: unknown[]) => signInWithAccessToken(...args),
    api: {
      ...actual.api,
      systemInfo: (...args: unknown[]) => systemInfo(...args),
      targets: (...args: unknown[]) => targets(...args),
    },
  };
});

const config: OidcSignInConfig = {
  issuer: "https://login.example.internal/realms/engineering",
  clientId: "oracledbharness-console",
  authorizationEndpoint: "https://login.example.internal/realms/engineering/auth",
  tokenEndpoint: "https://login.example.internal/realms/engineering/token",
  scopes: ["openid"],
  audience: null,
};

const info: SystemInfo = {
  version: "0.1.0",
  environment: "pilot",
  authMode: "oidc",
  oracleBackend: "oracledb",
  oracleDriverMode: "thin",
  metadataSchemaVersion: "3",
  catalogOperations: 20,
  copilotEnabled: false,
  warnings: [],
  limits: {},
};

const alice: Me = {
  subject: "alice@example.internal",
  displayName: "Alice Example",
  roles: ["developer"],
  userId: "usr_alice",
  targets: [],
};

const fetchMock = vi.fn();

/** Start a sign-in in this tab and come back on the callback the provider would send. */
async function returnFromProvider(overrideState?: string) {
  const url = new URL(await authorizationUrl(config));
  const state = overrideState ?? url.searchParams.get("state");
  window.history.replaceState(null, "", `/auth/callback?code=code_1&state=${state}`);
}

beforeEach(() => {
  systemInfo.mockResolvedValue(info);
  oidcSignInConfig.mockResolvedValue(config);
  signInWithAccessToken.mockResolvedValue(alice);
  targets.mockResolvedValue([]);
  fetchMock.mockResolvedValue(
    new Response(JSON.stringify({ access_token: "at_123", token_type: "Bearer", expires_in: 300 }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }),
  );
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
  sessionStorage.clear();
  window.history.replaceState(null, "", "/");
});

describe("the sign-in screen in OIDC mode", () => {
  it("offers to sign in through the identity provider", async () => {
    render(<App />);
    expect(
      await screen.findByRole("button", { name: /sign in with your identity provider/i }),
    ).toBeTruthy();
    // No development form, and no instruction to go and fetch a token by hand.
    expect(screen.queryByLabelText(/subject/i)).toBeNull();
  });
});

describe("returning from the identity provider", () => {
  it("redeems the code, adopts the access token and clears the callback URL", async () => {
    await returnFromProvider();
    // StrictMode runs effects twice, as main.tsx does in development. The code is
    // single use; a second redemption would fail and could replace the session.
    render(
      <StrictMode>
        <App />
      </StrictMode>,
    );

    expect(await screen.findByText(/Alice Example/)).toBeTruthy();
    expect(signInWithAccessToken).toHaveBeenCalledTimes(1);
    expect(signInWithAccessToken).toHaveBeenCalledWith("at_123");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toBe(config.tokenEndpoint);
    expect(window.location.pathname).toBe("/");
    expect(window.location.search).toBe("");
  });

  it("refuses a callback this tab did not start, and signs nobody in", async () => {
    await returnFromProvider("forged-state");
    render(<App />);

    expect(await screen.findByText(/does not match the sign-in this tab started/i)).toBeTruthy();
    expect(fetchMock).not.toHaveBeenCalled();
    expect(signInWithAccessToken).not.toHaveBeenCalled();
    expect(window.location.search).toBe("");
    // Still usable: the user can start again.
    await waitFor(() =>
      expect(
        screen
          .getByRole("button", { name: /sign in with your identity provider/i })
          .hasAttribute("disabled"),
      ).toBe(false),
    );
  });

  it("stays on the sign-in screen when the harness does not know the account", async () => {
    const { HarnessError } = await import("@contracts");
    signInWithAccessToken.mockRejectedValue(
      new HarnessError(401, {
        code: "authentication_required",
        message: "This account is authenticated but not registered in the harness.",
        detail: {},
        retryable: false,
      }),
    );
    await returnFromProvider();
    render(<App />);

    expect(await screen.findByText(/not registered in the harness/i)).toBeTruthy();
    expect(screen.queryByText(/Alice Example/)).toBeNull();
  });
});
