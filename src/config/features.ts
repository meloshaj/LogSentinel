export interface FeatureFlags {
  ENABLE_SETTINGS_EDIT: boolean;
  ENABLE_GITHUB_AUTH: boolean;
  ENABLE_PASSWORD_RESET: boolean;
  ENABLE_GOOGLE_AUTH: boolean;
}

export const FEATURE_FLAGS: Readonly<FeatureFlags> = Object.freeze({
  ENABLE_SETTINGS_EDIT:
    import.meta.env.VITE_FEATURE_ENABLE_SETTINGS_EDIT === 'true' || false,
  ENABLE_GITHUB_AUTH:
    import.meta.env.VITE_FEATURE_ENABLE_GITHUB_AUTH === 'true' || false,
  ENABLE_PASSWORD_RESET:
    import.meta.env.VITE_FEATURE_ENABLE_PASSWORD_RESET === 'true' || false,
  ENABLE_GOOGLE_AUTH:
    import.meta.env.VITE_FEATURE_ENABLE_GOOGLE_AUTH === 'true' ||
    Boolean(import.meta.env.VITE_GOOGLE_CLIENT_ID),
});
