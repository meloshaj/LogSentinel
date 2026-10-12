const AUTH_TOKEN_KEY = "authToken";
const LEGACY_LOGIN_FLAG_KEY = "isLoggedIn";
let accessToken: string | null = null;
let refreshInFlight: Promise<string | null> | null = null;

export class AuthenticationError extends Error {
  constructor(message = "Authentication expired or invalid") {
    super(message);
    this.name = "AuthenticationError";
  }
}

export function getAuthToken(): string | null {
  return accessToken;
}

export function setAuthToken(token: string, persistent: boolean = true): void {
  accessToken = token;
  // Remove credentials written by older releases. Access JWTs are memory-only;
  // the durable session secret exists only in the HttpOnly refresh cookie.
  window.localStorage.removeItem(AUTH_TOKEN_KEY);
  window.sessionStorage.removeItem(AUTH_TOKEN_KEY);
  window.localStorage.removeItem(LEGACY_LOGIN_FLAG_KEY);
  void persistent;
}

export function clearAuthToken(): void {
  accessToken = null;
  window.localStorage.removeItem(AUTH_TOKEN_KEY);
  window.sessionStorage.removeItem(AUTH_TOKEN_KEY);
  window.localStorage.removeItem(LEGACY_LOGIN_FLAG_KEY);
}

/** Remove sensitive OAuth/reset query and fragment values from the current URL. */
export function sanitizeAuthCallbackUrl(): void {
  window.history.replaceState({}, document.title, window.location.pathname);
}

function readCookie(name: string): string | null {
  const prefix = `${encodeURIComponent(name)}=`;
  for (const part of document.cookie.split(";")) {
    const value = part.trim();
    if (value.startsWith(prefix)) return decodeURIComponent(value.slice(prefix.length));
  }
  return null;
}

export async function refreshSession(): Promise<string | null> {
  if (refreshInFlight) return refreshInFlight;
  refreshInFlight = (async () => {
    const csrf = readCookie("logsentinel_csrf");
    if (!csrf) return null;
    const response = await fetch("/api/auth/refresh", {
      method: "POST",
      credentials: "include",
      headers: { "X-CSRF-Token": csrf },
    });
    if (!response.ok) {
      clearAuthToken();
      return null;
    }
    const payload: unknown = await response.json();
    if (typeof payload !== "object" || payload === null || !("access_token" in payload) || typeof payload.access_token !== "string") {
      clearAuthToken();
      return null;
    }
    setAuthToken(payload.access_token, false);
    return payload.access_token;
  })().finally(() => { refreshInFlight = null; });
  return refreshInFlight;
}

export async function logoutSession(): Promise<void> {
  const csrf = readCookie("logsentinel_csrf");
  try {
    if (csrf) {
      await fetch("/api/auth/logout", {
        method: "POST",
        credentials: "include",
        headers: { "X-CSRF-Token": csrf },
      });
    }
  } finally {
    clearAuthToken();
  }
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
    if (pageOrigin) {
      allowedOrigins.add(pageOrigin);
      try {
        const parsed = new URL(window.location.origin);
        if (parsed.hostname === "localhost" || parsed.hostname === "127.0.0.1") {
          allowedOrigins.add("http://localhost:8000");
          allowedOrigins.add("http://127.0.0.1:8000");
          allowedOrigins.add("http://localhost:3000");
          allowedOrigins.add("http://127.0.0.1:3000");
          allowedOrigins.add("http://localhost:5173");
          allowedOrigins.add("http://127.0.0.1:5173");
        }
      } catch {
        /* ignore parsing error */
      }
    }
  }

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
  const requestInit = authenticatedRequestInit(url, { ...init, credentials: "include" });
  let response = await fetch(input, requestInit);
  if (response.status === 401) {
    const token = await refreshSession();
    if (token) response = await fetch(input, authenticatedRequestInit(url, { ...init, credentials: "include" }));
  }
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
  const safeMessage = (candidate: string): string => {
    const normalized = candidate.replace(/\s+/g, " ").trim();
    if (
      !normalized ||
      normalized.length > 240 ||
      /(postgres(?:ql)?(?:\+\w+)?|rediss?|mongodb(?:\+\w+)?):\/\/|bearer\s+\S+|(?:password|secret|token|access[_ -]?key)\s*[:=]/i.test(normalized)
    ) {
      return fallback;
    }
    return normalized;
  };

  try {
    const data = await response.json();
    if (typeof data?.detail === "string") return safeMessage(data.detail);
    if (Array.isArray(data?.detail)) {
      const message = data.detail
        .map((item: unknown) =>
          typeof item === "object" &&
          item !== null &&
          "msg" in item &&
          typeof item.msg === "string"
            ? item.msg
            : null,
        )
        .filter(Boolean)
        .join("; ");
      return message ? safeMessage(message) : fallback;
    }
  } catch {
    // Keep the original fallback for empty or non-JSON error responses.
  }

  return fallback;
}
