import { Configuration } from "@azure/msal-browser";

export const msalConfig = {
  enabled: import.meta.env.VITE_MICROSOFT_AUTH_ENABLED === "true",
  clientId: import.meta.env.VITE_MICROSOFT_SPA_CLIENT_ID || "",
  authority: import.meta.env.VITE_MICROSOFT_AUTHORITY || "https://login.microsoftonline.com/common",
  apiScope: import.meta.env.VITE_MICROSOFT_API_SCOPE || "",
  redirectUri: import.meta.env.VITE_MICROSOFT_REDIRECT_URI || `${window.location.origin}/redirect.html`,
  postLogoutRedirectUri: import.meta.env.VITE_MICROSOFT_POST_LOGOUT_REDIRECT_URI || window.location.origin,
};

export const msalInstanceConfig: Configuration = {
  auth: {
    clientId: msalConfig.clientId,
    authority: msalConfig.authority,
    redirectUri: msalConfig.redirectUri,
    postLogoutRedirectUri: msalConfig.postLogoutRedirectUri,
  },
  cache: {
    cacheLocation: "sessionStorage", // This configures where MSAL stores its own tokens
  },
  system: {},
};

export const loginRequest = {
  scopes: msalConfig.apiScope ? [msalConfig.apiScope] : [],
};

// Check if MSAL is fully and correctly configured
export const isMsalConfigured = (): boolean => {
  const scopeLower = msalConfig.apiScope.toLowerCase();
  const isGraphScope = scopeLower === "user.read" || scopeLower.includes("graph.microsoft.com");

  return (
    msalConfig.enabled &&
    Boolean(msalConfig.clientId) &&
    Boolean(msalConfig.apiScope) &&
    Boolean(msalConfig.redirectUri) &&
    !isGraphScope
  );
};
