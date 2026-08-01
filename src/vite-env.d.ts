/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_WS_URL?: string;
  readonly VITE_API_URL?: string;
  readonly VITE_MICROSOFT_AUTH_ENABLED?: string;
  readonly VITE_MICROSOFT_SPA_CLIENT_ID?: string;
  readonly VITE_MICROSOFT_AUTHORITY?: string;
  readonly VITE_MICROSOFT_API_SCOPE?: string;
  readonly VITE_MICROSOFT_REDIRECT_URI?: string;
  readonly VITE_MICROSOFT_POST_LOGOUT_REDIRECT_URI?: string;
}
