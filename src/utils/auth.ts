const AUTH_TOKEN_KEY = "authToken";
const LEGACY_LOGIN_FLAG_KEY = "isLoggedIn";

export function getAuthToken(): string | null {
  // Deterministic precedence: localStorage wins if both are present somehow
  return window.localStorage.getItem(AUTH_TOKEN_KEY) || window.sessionStorage.getItem(AUTH_TOKEN_KEY);
}

export function setAuthToken(token: string, persistent: boolean = false): void {
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

export async function getAuthErrorMessage(
  response: Response,
  fallback: string,
): Promise<string> {
  try {
    const data = await response.json();
    if (typeof data?.detail === "string") return data.detail;
    if (Array.isArray(data?.detail)) {
      return data.detail
        .map((item: any) => item?.msg)
        .filter(Boolean)
        .join("; ") || fallback;
    }
  } catch {
    // Keep the original fallback for empty or non-JSON error responses.
  }

  return fallback;
}
