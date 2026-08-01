const AUTH_TOKEN_KEY = "authToken";
const LEGACY_LOGIN_FLAG_KEY = "isLoggedIn";

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
