const AUTH_TOKEN_KEY = "authToken";
const LEGACY_LOGIN_FLAG_KEY = "isLoggedIn";

export class AuthenticationError extends Error {
  constructor(message = "Authentication expired or invalid") {
    super(message);
    this.name = "AuthenticationError";
  }
}

export function getAuthToken(): string | null {
  // Deterministic precedence: localStorage wins if both are present somehow
  return window.localStorage.getItem(AUTH_TOKEN_KEY) || window.sessionStorage.getItem(AUTH_TOKEN_KEY);
}

export function setAuthToken(token: string, persistent: boolean = true): void {
  // Clear any existing duplicates
  window.localStorage.removeItem(AUTH_TOKEN_KEY);
  window.sessionStorage.removeItem(AUTH_TOKEN_KEY);
  window.localStorage.removeItem(LEGACY_LOGIN_FLAG_KEY);

  if (persistent) {
    window.localStorage.setItem(AUTH_TOKEN_KEY, token);
  } else {
    window.sessionStorage.setItem(AUTH_TOKEN_KEY, token);
  }
}

export function clearAuthToken(): void {
  window.localStorage.removeItem(AUTH_TOKEN_KEY);
  window.sessionStorage.removeItem(AUTH_TOKEN_KEY);
  window.localStorage.removeItem(LEGACY_LOGIN_FLAG_KEY);
}

function normalizedOrigin(value: string): string | null {
  try {
    const parsed = new URL(value, window.location.origin);
    const protocol =
      parsed.protocol === "ws:" ? "http:" : parsed.protocol === "wss:" ? "https:" : parsed.protocol;
    return `${protocol}//${parsed.host}`;
  } catch {
    return null;
  }
}

/**
 * Return whether a URL is an explicitly configured LogSentinel backend URL.
 * This prevents a bearer token from being attached to arbitrary external
 * origins when the dashboard has multiple fallback candidates.
 */
export function isTrustedBackendUrl(value: string | URL): boolean {
  const raw = value.toString();
  // A root-relative URL is same-origin. Protocol-relative URLs (//host/…)
  // are absolute cross-origin URLs and must still pass the allow-list.
  if (raw.startsWith("/") && !raw.startsWith("//")) return true;

  const targetOrigin = normalizedOrigin(raw);
  if (!targetOrigin) return false;

  const allowedOrigins = new Set<string>();
  if (typeof window !== "undefined") {
    const pageOrigin = normalizedOrigin(window.location.origin);
    if (pageOrigin) allowedOrigins.add(pageOrigin);
  }

  // The local fallback is part of the supported native-development contract.
  allowedOrigins.add("http://localhost:8000");

  for (const configured of [import.meta.env.VITE_API_URL, import.meta.env.VITE_WS_URL]) {
    if (configured) {
      const configuredOrigin = normalizedOrigin(configured);
      if (configuredOrigin) allowedOrigins.add(configuredOrigin);
    }
  }

  return allowedOrigins.has(targetOrigin);
}

export function authenticatedRequestInit(
  url: string | URL,
  init: RequestInit = {},
): RequestInit {
  const headers = new Headers(init.headers);
  const token = getAuthToken();
  if (token && isTrustedBackendUrl(url)) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  return { ...init, headers };
}

/** Fetch a protected backend resource and clear stale local auth uniformly. */
export async function fetchAuthenticated(
  input: RequestInfo | URL,
  init: RequestInit = {},
): Promise<Response> {
  const url =
    typeof input === "string"
      ? input
      : input instanceof URL
        ? input.toString()
        : typeof Request !== "undefined" && input instanceof Request
          ? input.url
          : String(input);
  const response = await fetch(input, authenticatedRequestInit(url, init));
  if (response.status === 401 || response.status === 403) {
    clearAuthToken();
    throw new AuthenticationError();
  }
  return response;
}

/** Build a tokenized WebSocket URL without exposing the token in UI state. */
export function authenticatedWebSocketUrl(
  candidate: string,
): string | null {
  if (!isTrustedBackendUrl(candidate)) return null;

  try {
    const url = new URL(candidate, window.location.href);
    return url.toString();
  } catch {
    return null;
  }
}

export function isAuthTokenValid(token: string | null): boolean {
  if (!token) return false;

  try {
    const segments = token.split(".");
    if (segments.length !== 3 || !segments[1]) return false;

    const base64 = segments[1].replace(/-/g, "+").replace(/_/g, "/");
    const padded = base64.padEnd(Math.ceil(base64.length / 4) * 4, "=");
    const jsonPayload = decodeURIComponent(
      window
        .atob(padded)
        .split("")
        .map(
          (character) =>
            `%${(`00${character.charCodeAt(0).toString(16)}`).slice(-2)}`,
        )
        .join(""),
    );
    const payload: unknown = JSON.parse(jsonPayload);
    if (
      typeof payload !== "object" ||
      payload === null ||
      !("exp" in payload) ||
      typeof payload.exp !== "number" ||
      !Number.isFinite(payload.exp)
    ) {
      return false;
    }

    return payload.exp > Math.floor(Date.now() / 1000);
  } catch {
    return false;
  }
}

export async function getAuthErrorMessage(
  response: Response,
  fallback: string,
): Promise<string> {
  try {
    const data = await response.json();
    if (typeof data?.detail === "string") return data.detail;
    if (Array.isArray(data?.detail)) {
      return data.detail
        .map((item: unknown) =>
          typeof item === "object" &&
          item !== null &&
          "msg" in item &&
          typeof item.msg === "string"
            ? item.msg
            : null,
        )
        .filter(Boolean)
        .join("; ") || fallback;
    }
  } catch {
    // Keep the original fallback for empty or non-JSON error responses.
  }

  return fallback;
}
