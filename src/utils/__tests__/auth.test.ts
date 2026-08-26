import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  authenticatedRequestInit,
  authenticatedWebSocketUrl,
  fetchAuthenticated,
  getAuthToken,
  setAuthToken,
} from "../auth";

describe("authenticated browser transport", () => {
  beforeEach(() => {
    window.localStorage.clear();
    window.sessionStorage.clear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("attaches the current JWT to same-origin protected REST requests", () => {
    setAuthToken("current-token");

    const init = authenticatedRequestInit("/api/v1/topology");

    expect(new Headers(init.headers).get("Authorization")).toBe("Bearer current-token");
  });

  it("does not attach the JWT to an unrelated external origin", () => {
    setAuthToken("current-token");

    const init = authenticatedRequestInit("https://external.example/logs");

    expect(new Headers(init.headers).has("Authorization")).toBe(false);
  });

  it("does not leak the current token in the WebSocket query", () => {
    const token = "jwt token/+?";
    setAuthToken(token);

    const candidate = authenticatedWebSocketUrl("ws://localhost:8000/ws/telemetry");
    expect(candidate).not.toBeNull();
    expect(new URL(candidate!).searchParams.has("token")).toBe(false);
  });

  it("clears an expired token when a protected request is rejected", async () => {
    setAuthToken("expired-token");
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response(null, { status: 401 })),
    );

    await expect(fetchAuthenticated("/api/v1/topology")).rejects.toMatchObject({
      name: "AuthenticationError",
    });
    expect(getAuthToken()).toBeNull();
  });
});
