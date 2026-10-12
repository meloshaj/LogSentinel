export function getGoogleClientId(): string {
  return import.meta.env.VITE_GOOGLE_CLIENT_ID?.trim() ?? "";
}

export function isGoogleAuthEnabled(): boolean {
  return (
    getGoogleClientId().length > 0 &&
    import.meta.env.VITE_GOOGLE_AUTH_ENABLED !== "false" &&
    import.meta.env.VITE_FEATURE_ENABLE_GOOGLE_AUTH !== "false"
  );
}
