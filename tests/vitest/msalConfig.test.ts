import { afterEach, describe, expect, it, vi } from "vitest";

const validEnvironment = {
  VITE_MICROSOFT_AUTH_ENABLED: "true",
  VITE_MICROSOFT_SPA_CLIENT_ID: "00000000-0000-4000-8000-000000000001",
  VITE_MICROSOFT_AUTHORITY:
    "https://login.microsoftonline.com/11111111-1111-4111-8111-111111111111",
  VITE_MICROSOFT_API_SCOPE:
    "api://00000000-0000-4000-8000-000000000002/access_as_user",
  VITE_MICROSOFT_REDIRECT_URI: `${window.location.origin}/redirect.html`,
  VITE_MICROSOFT_POST_LOGOUT_REDIRECT_URI: window.location.origin,
};

async function loadConfig(overrides: Record<string, string> = {}) {
  vi.resetModules();
  for (const [name, value] of Object.entries({
    ...validEnvironment,
    ...overrides,
  })) {
    vi.stubEnv(name, value);
  }
  return import("../../src/config/msal");
}

afterEach(() => {
  vi.unstubAllEnvs();
  vi.resetModules();
});

describe("MSAL configuration validation", () => {
  it("is disabled without constructing a fake client identifier", async () => {
    const config = await loadConfig({
      VITE_MICROSOFT_AUTH_ENABLED: "false",
      VITE_MICROSOFT_SPA_CLIENT_ID: "",
      VITE_MICROSOFT_API_SCOPE: "",
    });

    expect(config.isMsalConfigured()).toBe(false);
    expect(config.msalConfig.clientId).toBe("");
    expect(config.loginRequest.scopes).toEqual([]);
  });

  it.each([
    ["missing client ID", { VITE_MICROSOFT_SPA_CLIENT_ID: "" }],
    ["invalid client ID", { VITE_MICROSOFT_SPA_CLIENT_ID: "not-a-guid" }],
    ["missing API scope", { VITE_MICROSOFT_API_SCOPE: "" }],
    ["missing authority", { VITE_MICROSOFT_AUTHORITY: "" }],
    ["missing authority tenant", { VITE_MICROSOFT_AUTHORITY: "https://login.microsoftonline.com" }],
  ])("fails closed for %s", async (_name, overrides) => {
    const config = await loadConfig(overrides);
    expect(config.isMsalConfigured()).toBe(false);
  });

  it("accepts exactly one custom LogSentinel API scope", async () => {
    const config = await loadConfig();

    expect(config.isMsalConfigured()).toBe(true);
    expect(config.loginRequest.scopes).toEqual([
      "api://00000000-0000-4000-8000-000000000002/access_as_user",
    ]);
  });

  it.each([
    "User.Read",
    "Mail.Read",
    "openid",
    "https://graph.microsoft.com/User.Read",
    "api://00000000-0000-4000-8000-000000000002/.default",
  ])("rejects Graph, OIDC-only, or nondelegated scope %s", async (scope) => {
    const config = await loadConfig({ VITE_MICROSOFT_API_SCOPE: scope });
    expect(config.isValidMicrosoftApiScope(scope)).toBe(false);
    expect(config.isMsalConfigured()).toBe(false);
  });

  it("rejects a cross-origin or routed redirect bridge", async () => {
    const crossOrigin = await loadConfig({
      VITE_MICROSOFT_REDIRECT_URI: "https://example.invalid/redirect.html",
    });
    expect(crossOrigin.isMsalConfigured()).toBe(false);

    const routed = await loadConfig({
      VITE_MICROSOFT_REDIRECT_URI: `${window.location.origin}/login`,
    });
    expect(routed.isMsalConfigured()).toBe(false);
  });

  it.each(["common", "organizations", "consumers"])(
    "accepts the supported %s authority",
    async (tenant) => {
      const config = await loadConfig({
        VITE_MICROSOFT_AUTHORITY: `https://login.microsoftonline.com/${tenant}`,
      });
      expect(config.isMsalConfigured()).toBe(true);
    },
  );

  it("rejects an unsupported authority host", async () => {
    const config = await loadConfig({
      VITE_MICROSOFT_AUTHORITY: "https://login.example.invalid/common",
    });
    expect(config.isMsalConfigured()).toBe(false);
  });
});
