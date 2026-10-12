import { afterEach, describe, expect, it, vi } from "vitest";
import { getApiBaseUrl, getApiUrl } from "../api";

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("configured API URLs", () => {
  it("uses the configured production backend origin", () => {
    vi.stubEnv("VITE_API_URL", "https://138.2.152.189.sslip.io/");

    expect(getApiBaseUrl()).toBe("https://138.2.152.189.sslip.io");
    expect(getApiUrl("/api/auth/api-key")).toBe(
      "https://138.2.152.189.sslip.io/api/auth/api-key",
    );
  });

  it("fails closed when no backend origin is configured", () => {
    vi.stubEnv("VITE_API_URL", "");

    expect(() => getApiUrl("/api/auth/api-key")).toThrow("VITE_API_URL");
  });

  it("rejects protocol-relative paths", () => {
    vi.stubEnv("VITE_API_URL", "https://138.2.152.189.sslip.io");

    expect(() => getApiUrl("//untrusted.example/api/auth/api-key")).toThrow(
      "root-relative paths",
    );
  });
});
