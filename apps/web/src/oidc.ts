/**
 * Console sign-in through the identity provider: authorization code flow with PKCE.
 *
 * The console is a public client. It has no secret to protect the code exchange, so
 * PKCE does that job: only the tab that started the sign-in holds the verifier that
 * redeems the code. The `state` value ties the callback to that same tab, so a
 * callback URL somebody else crafted is refused rather than signing the user in as
 * whoever obtained the code.
 *
 * The verifier and state have to survive the round trip to the provider, so they sit
 * in sessionStorage - scoped to this tab, and removed the moment the callback is read,
 * whether or not it succeeds. The access token never goes there: it stays in memory
 * (see api.ts), and a reload means signing in again, which the provider's own session
 * usually makes a single redirect.
 */

export interface OidcSignInConfig {
  issuer: string;
  clientId: string;
  authorizationEndpoint: string;
  tokenEndpoint: string;
  scopes: string[];
  audience: string | null;
}

export interface OidcTokens {
  accessToken: string;
  /** Seconds, as the provider reported it; null if it did not say. */
  expiresIn: number | null;
}

interface PendingSignIn {
  state: string;
  verifier: string;
  redirectUri: string;
  startedAt: number;
}

export const CALLBACK_PATH = "/auth/callback";
const PENDING_KEY = "oracledbharness.oidc.pending";
// Long enough for a password and a second factor; short enough that a stale entry
// left by an abandoned attempt cannot be replayed much later.
const PENDING_TTL_MS = 10 * 60 * 1000;

export class SignInError extends Error {}

export function redirectUri(origin: string = window.location.origin): string {
  return `${origin}${CALLBACK_PATH}`;
}

export function isCallback(location: Pick<Location, "pathname"> = window.location): boolean {
  return location.pathname === CALLBACK_PATH;
}

function base64url(bytes: Uint8Array): string {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function randomToken(bytes: number): string {
  return base64url(crypto.getRandomValues(new Uint8Array(bytes)));
}

async function challengeFor(verifier: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier));
  return base64url(new Uint8Array(digest));
}

/** Build the provider's authorization URL and remember what the callback must match. */
export async function authorizationUrl(
  config: OidcSignInConfig,
  storage: Storage = sessionStorage,
  origin: string = window.location.origin,
): Promise<string> {
  const pending: PendingSignIn = {
    state: randomToken(32),
    // 64 base64url characters; RFC 7636 allows 43 to 128.
    verifier: randomToken(48),
    redirectUri: redirectUri(origin),
    startedAt: Date.now(),
  };
  storage.setItem(PENDING_KEY, JSON.stringify(pending));

  const url = new URL(config.authorizationEndpoint);
  url.searchParams.set("response_type", "code");
  url.searchParams.set("client_id", config.clientId);
  url.searchParams.set("redirect_uri", pending.redirectUri);
  url.searchParams.set("scope", config.scopes.join(" "));
  url.searchParams.set("state", pending.state);
  url.searchParams.set("code_challenge", await challengeFor(pending.verifier));
  url.searchParams.set("code_challenge_method", "S256");
  if (config.audience) url.searchParams.set("audience", config.audience);
  return url.toString();
}

function takePending(storage: Storage): PendingSignIn | null {
  const raw = storage.getItem(PENDING_KEY);
  // Single use, whatever happens next.
  storage.removeItem(PENDING_KEY);
  if (!raw) return null;
  try {
    return JSON.parse(raw) as PendingSignIn;
  } catch {
    return null;
  }
}

/**
 * Redeem the callback's authorization code for an access token.
 *
 * Throws SignInError for everything that is not a clean sign-in: an error from the
 * provider, a callback this tab did not start, an expired attempt, a failed exchange.
 */
export async function completeSignIn(
  config: OidcSignInConfig,
  search: string,
  storage: Storage = sessionStorage,
  fetchImpl: typeof fetch = fetch,
): Promise<OidcTokens> {
  const params = new URLSearchParams(search);
  const pending = takePending(storage);

  const refused = params.get("error");
  if (refused) {
    throw new SignInError(
      `The identity provider did not sign you in: ${params.get("error_description") || refused}.`,
    );
  }
  if (!pending) {
    throw new SignInError(
      "This tab has no sign-in in progress, so the callback cannot be trusted. Sign in again.",
    );
  }
  if (params.get("state") !== pending.state) {
    throw new SignInError(
      "The sign-in callback does not match the sign-in this tab started. Sign in again.",
    );
  }
  if (Date.now() - pending.startedAt > PENDING_TTL_MS) {
    throw new SignInError("The sign-in took too long and has expired. Sign in again.");
  }
  const code = params.get("code");
  if (!code) {
    throw new SignInError("The identity provider returned no authorization code.");
  }

  let response: Response;
  try {
    response = await fetchImpl(config.tokenEndpoint, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded", Accept: "application/json" },
      body: new URLSearchParams({
        grant_type: "authorization_code",
        code,
        redirect_uri: pending.redirectUri,
        client_id: config.clientId,
        code_verifier: pending.verifier,
      }),
    });
  } catch (cause) {
    throw new SignInError(
      `Could not reach the identity provider's token endpoint (${String(cause)}). If ` +
        "this is a CORS error, the provider has to allow this console's origin.",
    );
  }

  // JSON can be null, an array or a scalar; only an object has fields to read.
  const parsed: unknown = await response.json().catch(() => null);
  const body: Record<string, unknown> =
    parsed !== null && typeof parsed === "object" && !Array.isArray(parsed)
      ? (parsed as Record<string, unknown>)
      : {};
  if (!response.ok) {
    const reason = body.error_description || body.error || `HTTP ${response.status}`;
    throw new SignInError(`The identity provider refused the code exchange: ${String(reason)}.`);
  }
  if (typeof body.access_token !== "string" || !body.access_token) {
    throw new SignInError("The identity provider's token response carried no access token.");
  }
  if (typeof body.token_type === "string" && body.token_type.toLowerCase() !== "bearer") {
    throw new SignInError(`Expected a bearer token, got ${body.token_type}.`);
  }
  return {
    accessToken: body.access_token,
    expiresIn: typeof body.expires_in === "number" ? body.expires_in : null,
  };
}
