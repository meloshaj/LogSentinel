import { afterEach, describe, expect, it, vi } from "vitest";

const redirectBridge = vi.hoisted(() => ({
  broadcastResponseToMainFrame: vi.fn(),
}));

vi.mock("@azure/msal-browser/redirect-bridge", () => redirectBridge);

describe("MSAL redirect bridge entry", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.resetModules();
    redirectBridge.broadcastResponseToMainFrame.mockReset();
  });

  it("broadcasts the popup response without bootstrapping React", async () => {
    redirectBridge.broadcastResponseToMainFrame.mockResolvedValue(undefined);

    await import("../../src/msal-redirect-bridge");

    expect(redirectBridge.broadcastResponseToMainFrame).toHaveBeenCalledTimes(1);
  });

  it("does not log a redirect error message or token-bearing value", async () => {
    const consoleError = vi
      .spyOn(console, "error")
      .mockImplementation(() => undefined);
    redirectBridge.broadcastResponseToMainFrame.mockRejectedValue(
      new Error("secret-token-in-redirect-fragment"),
    );

    await import("../../src/msal-redirect-bridge");
    await vi.waitFor(() => expect(consoleError).toHaveBeenCalledTimes(1));

    expect(consoleError).toHaveBeenCalledWith(
      "Redirect bridge communication failed",
      "Error",
    );
    expect(JSON.stringify(consoleError.mock.calls)).not.toContain(
      "secret-token-in-redirect-fragment",
    );
  });
});
