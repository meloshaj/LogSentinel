const AUTH_TOKEN_KEY = "authToken";
const LEGACY_LOGIN_FLAG_KEY = "isLoggedIn";

export function getAuthToken(): string | null {
  return window.localStorage.getItem(AUTH_TOKEN_KEY);
}

export function setAuthToken(token: string): void {
  window.localStorage.setItem(AUTH_TOKEN_KEY, token);
  window.localStorage.removeItem(LEGACY_LOGIN_FLAG_KEY);
}

export function clearAuthToken(): void {
  window.localStorage.removeItem(AUTH_TOKEN_KEY);
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
        .map((item) => item?.msg)
        .filter(Boolean)
        .join("; ") || fallback;
    }
  } catch {
    // Keep the original fallback for empty or non-JSON error responses.
  }

  return fallback;
}
