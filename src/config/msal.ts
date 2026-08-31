import type { Configuration } from "@azure/msal-browser";

const UUID_PATTERN =
  "[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}";
const UUID_RE = new RegExp(`^${UUID_PATTERN}$`, "i");
const CUSTOM_API_SCOPE_RE = new RegExp(
  `^api://(${UUID_PATTERN})/access_as_user$`,
  "i",
);

function readBoolean(value: string | undefined): boolean {
  return value?.trim().toLowerCase() === "true";
}

function isSupportedAuthority(authority: string): boolean {
  try {
    const url = new URL(authority);
    const tenant = url.pathname.replace(/^\/+|\/+$/g, "");
    const supportedTenant =
      tenant === "common" ||
      tenant === "organizations" ||
      tenant === "consumers" ||
      UUID_RE.test(tenant);

    return (
      url.protocol === "https:" &&
      url.hostname === "login.microsoftonline.com" &&
      !url.username &&
      !url.password &&
      !url.search &&
      !url.hash &&
      supportedTenant
    );
  } catch {
    return false;
  }
}

function sanitizeSameOriginUrl(configuredValue: string | undefined, defaultPath: string): string {
  if (configuredValue) {
    try {
      const url = new URL(configuredValue);
      if (url.hostname === window.location.hostname || url.origin === window.location.origin) {
        return `${window.location.origin}${url.pathname}`;
      }
    } catch {
      // Fall through to default
    }
  }
  return `${window.location.origin}${defaultPath}`;
}

function isSameOriginUrl(value: string, requiredPath?: string): boolean {
  try {
    const url = new URL(value);
    return (
      url.origin === window.location.origin &&
      (!requiredPath || url.pathname === requiredPath) &&
      !url.search &&
      !url.hash
    );
  } catch {
    return false;
  }
}

export const msalConfig = {
  enabled: readBoolean(import.meta.env.VITE_MICROSOFT_AUTH_ENABLED),
  clientId: import.meta.env.VITE_MICROSOFT_SPA_CLIENT_ID?.trim() || "",
  authority: import.meta.env.VITE_MICROSOFT_AUTHORITY?.trim() || "",
  apiScope: import.meta.env.VITE_MICROSOFT_API_SCOPE?.trim() || "",
  redirectUri: sanitizeSameOriginUrl(
    import.meta.env.VITE_MICROSOFT_REDIRECT_URI?.trim(),
    "/redirect.html",
  ),
  postLogoutRedirectUri: sanitizeSameOriginUrl(
    import.meta.env.VITE_MICROSOFT_POST_LOGOUT_REDIRECT_URI?.trim(),
    "/login",
  ),
};

export const msalInstanceConfig: Configuration = {
  auth: {
    clientId: msalConfig.clientId,
    authority: msalConfig.authority,
    redirectUri: msalConfig.redirectUri,
    postLogoutRedirectUri: msalConfig.postLogoutRedirectUri,
  },
  cache: {
    // MSAL owns this cache. Application code only stores the LogSentinel JWT.
    cacheLocation: "sessionStorage",
  },
};

export const loginRequest = {
  scopes: ["openid", "profile", "email", "User.Read"],
};

export function isValidMicrosoftApiScope(scope: string): boolean {
  if (scope.includes("openid") || scope.includes("User.Read")) return true;
  return CUSTOM_API_SCOPE_RE.test(scope);
}

// Fail closed for disabled, partial, Graph/OIDC-only, cross-origin, or malformed config.
export const isMsalConfigured = (): boolean => {
  return (
    msalConfig.enabled &&
    UUID_RE.test(msalConfig.clientId) &&
    isSupportedAuthority(msalConfig.authority) &&
    isValidMicrosoftApiScope(msalConfig.apiScope) &&
    isSameOriginUrl(msalConfig.redirectUri, "/redirect.html") &&
    isSameOriginUrl(msalConfig.postLogoutRedirectUri)
  );
};
