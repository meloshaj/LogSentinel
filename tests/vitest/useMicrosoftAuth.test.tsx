import { act, renderHook } from "@testing-library/react";
import { InteractionRequiredAuthError } from "@azure/msal-browser";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useMicrosoftAuth } from "../../src/hooks/useMicrosoftAuth";

vi.mock("../../src/config/msal", () => ({
  isMsalConfigured: vi.fn(() => true),
  loginRequest: {
    scopes: ["api://00000000-0000-4000-8000-000000000002/access_as_user"],
  },
}));

vi.mock("@azure/msal-react", () => ({
  useMsal: vi.fn(),
}));

vi.mock("../../src/utils/auth", () => ({
  setAuthToken: vi.fn(),
}));

import { useMsal } from "@azure/msal-react";
import { setAuthToken } from "../../src/utils/auth";

const apiScope =
  "api://00000000-0000-4000-8000-000000000002/access_as_user";
const account = { homeAccountId: "account-1" };

const mockMsal = {
  instance: {
    getActiveAccount: vi.fn(),
    setActiveAccount: vi.fn(),
    acquireTokenSilent: vi.fn(),
    acquireTokenPopup: vi.fn(),
    loginPopup: vi.fn(),
  },
  accounts: [] as Array<{ homeAccountId: string }>,
};

function successfulResponse(token = "internal-jwt") {
  return {
    ok: true,
    status: 200,
    json: vi.fn().mockResolvedValue({ access_token: token }),
  };
}

describe("useMicrosoftAuth", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    vi.clearAllMocks();
    mockMsal.accounts = [];
    mockMsal.instance.getActiveAccount.mockReturnValue(null);
    mockMsal.instance.acquireTokenSilent.mockReset();
    mockMsal.instance.acquireTokenPopup.mockReset();
    mockMsal.instance.loginPopup.mockReset();
    (useMsal as ReturnType<typeof vi.fn>).mockReturnValue(mockMsal);
    fetchMock = vi.fn().mockResolvedValue(successfulResponse());
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("uses the active account, silent acquisition, exact API scope, and only the access token", async () => {
    mockMsal.instance.getActiveAccount.mockReturnValue(account);
    mockMsal.instance.acquireTokenSilent.mockResolvedValue({
      accessToken: "microsoft-access-token",
      idToken: "must-not-be-sent",
    });

    const { result } = renderHook(() => useMicrosoftAuth());
    let loginResult;
    await act(async () => {
      loginResult = await result.current.login(true);
    });

    expect(loginResult).toEqual({ success: true });
    expect(mockMsal.instance.setActiveAccount).toHaveBeenCalledWith(account);
    expect(mockMsal.instance.acquireTokenSilent).toHaveBeenCalledWith({
      scopes: [apiScope],
      account,
    });
    expect(mockMsal.instance.acquireTokenPopup).not.toHaveBeenCalled();
    expect(mockMsal.instance.loginPopup).not.toHaveBeenCalled();
    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8000/api/auth/microsoft",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ access_token: "microsoft-access-token" }),
      },
    );
    expect(fetchMock.mock.calls[0][1].body).not.toContain("must-not-be-sent");
    expect(setAuthToken).toHaveBeenCalledWith("internal-jwt", true);
    expect(result.current.loading).toBe(false);
  });

  it("falls back to accounts[0] and sets it active", async () => {
    mockMsal.accounts = [account];
    mockMsal.instance.acquireTokenSilent.mockResolvedValue({
      accessToken: "microsoft-access-token",
    });

    const { result } = renderHook(() => useMicrosoftAuth());
    await act(async () => {
      await result.current.login(false);
    });

    expect(mockMsal.instance.setActiveAccount).toHaveBeenCalledWith(account);
    expect(mockMsal.instance.acquireTokenSilent).toHaveBeenCalledWith({
      scopes: [apiScope],
      account,
    });
    expect(setAuthToken).toHaveBeenCalledWith("internal-jwt", false);
  });

  it("uses acquireTokenPopup only for InteractionRequiredAuthError", async () => {
    mockMsal.instance.getActiveAccount.mockReturnValue(account);
    mockMsal.instance.acquireTokenSilent.mockRejectedValue(
      new InteractionRequiredAuthError(
        "interaction_required",
        "interaction required",
      ),
    );
    mockMsal.instance.acquireTokenPopup.mockResolvedValue({
      accessToken: "interactive-access-token",
      account,
    });

    const { result } = renderHook(() => useMicrosoftAuth());
    await act(async () => {
      await result.current.login(false);
    });

    expect(mockMsal.instance.acquireTokenPopup).toHaveBeenCalledWith({
      scopes: [apiScope],
      account,
    });
    expect(mockMsal.instance.loginPopup).not.toHaveBeenCalled();
    expect(fetchMock.mock.calls[0][1].body).toBe(
      JSON.stringify({ access_token: "interactive-access-token" }),
    );
  });

  it("does not open another popup for a generic silent error", async () => {
    mockMsal.instance.getActiveAccount.mockReturnValue(account);
    mockMsal.instance.acquireTokenSilent.mockRejectedValue(
      new Error("network detail that must not render"),
    );

    const { result } = renderHook(() => useMicrosoftAuth());
    let loginResult;
    await act(async () => {
      loginResult = await result.current.login(false);
    });

    expect(mockMsal.instance.acquireTokenPopup).not.toHaveBeenCalled();
    expect(mockMsal.instance.loginPopup).not.toHaveBeenCalled();
    expect(fetchMock).not.toHaveBeenCalled();
    expect(loginResult).toEqual({
      success: false,
      error: "Microsoft sign-in could not be completed. Please try again.",
    });
  });

  it("starts the no-account path with loginPopup and prompt select_account", async () => {
    const newAccount = { homeAccountId: "account-2" };
    mockMsal.instance.loginPopup.mockResolvedValue({
      accessToken: "new-account-token",
      idToken: "never-send-id-token",
      account: newAccount,
    });

    const { result } = renderHook(() => useMicrosoftAuth());
    await act(async () => {
      await result.current.login(false);
    });

    expect(mockMsal.instance.loginPopup).toHaveBeenCalledWith({
      scopes: [apiScope],
      prompt: "select_account",
    });
    expect(mockMsal.instance.setActiveAccount).toHaveBeenCalledWith(newAccount);
    expect(fetchMock.mock.calls[0][1].body).toBe(
      JSON.stringify({ access_token: "new-account-token" }),
    );
    expect(fetchMock.mock.calls[0][1].body).not.toContain("never-send-id-token");
  });

  it("requires an account from loginPopup before backend exchange", async () => {
    mockMsal.instance.loginPopup.mockResolvedValue({
      accessToken: "token-without-account",
      account: null,
    });

    const { result } = renderHook(() => useMicrosoftAuth());
    let loginResult;
    await act(async () => {
      loginResult = await result.current.login(false);
    });

    expect(loginResult).toEqual({
      success: false,
      error: "Microsoft sign-in did not return an account. Please try again.",
    });
    expect(fetchMock).not.toHaveBeenCalled();
    expect(setAuthToken).not.toHaveBeenCalled();
  });

  it("falls back from an empty loginPopup token to silent then interactive acquisition", async () => {
    mockMsal.instance.loginPopup.mockResolvedValue({ accessToken: "", account });
    mockMsal.instance.acquireTokenSilent.mockRejectedValue(
      new InteractionRequiredAuthError(
        "interaction_required",
        "interaction required",
      ),
    );
    mockMsal.instance.acquireTokenPopup.mockResolvedValue({
      accessToken: "fallback-access-token",
      account,
    });

    const { result } = renderHook(() => useMicrosoftAuth());
    await act(async () => {
      await result.current.login(false);
    });

    expect(mockMsal.instance.acquireTokenSilent).toHaveBeenCalledWith({
      scopes: [apiScope],
      account,
    });
    expect(mockMsal.instance.acquireTokenPopup).toHaveBeenCalledWith({
      scopes: [apiScope],
      account,
    });
    expect(fetchMock.mock.calls[0][1].body).toBe(
      JSON.stringify({ access_token: "fallback-access-token" }),
    );
  });

  it("does not use a popup when the no-account silent fallback fails generically", async () => {
    mockMsal.instance.loginPopup.mockResolvedValue({ accessToken: "", account });
    mockMsal.instance.acquireTokenSilent.mockRejectedValue(new Error("failure"));

    const { result } = renderHook(() => useMicrosoftAuth());
    await act(async () => {
      await result.current.login(false);
    });

    expect(mockMsal.instance.acquireTokenPopup).not.toHaveBeenCalled();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("rejects an empty token after all acquisition attempts", async () => {
    mockMsal.instance.loginPopup.mockResolvedValue({ accessToken: "", account });
    mockMsal.instance.acquireTokenSilent.mockResolvedValue({ accessToken: "" });

    const { result } = renderHook(() => useMicrosoftAuth());
    let loginResult;
    await act(async () => {
      loginResult = await result.current.login(false);
    });

    expect(loginResult).toEqual({
      success: false,
      error:
        "Microsoft sign-in did not return an access token. Please try again.",
    });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("treats popup cancellation as neutral", async () => {
    mockMsal.instance.loginPopup.mockRejectedValue({
      name: "BrowserAuthError",
      errorCode: "user_cancelled",
      message: "raw cancellation detail",
    });

    const { result } = renderHook(() => useMicrosoftAuth());
    let loginResult;
    await act(async () => {
      loginResult = await result.current.login(false);
    });

    expect(loginResult).toEqual({ success: false, cancelled: true });
    expect(result.current.error).toBeNull();
  });

  it("maps popup blocking without exposing the raw exception", async () => {
    mockMsal.instance.loginPopup.mockRejectedValue({
      errorCode: "popup_window_error",
      message: "provider implementation detail",
    });

    const { result } = renderHook(() => useMicrosoftAuth());
    let loginResult;
    await act(async () => {
      loginResult = await result.current.login(false);
    });

    expect(loginResult).toEqual({
      success: false,
      error:
        "Your browser blocked the Microsoft sign-in window. Allow pop-ups for this site and try again.",
    });
    expect(JSON.stringify(loginResult)).not.toContain("implementation detail");
  });

  it("maps consent required", async () => {
    mockMsal.instance.loginPopup.mockRejectedValue({
      errorCode: "consent_required",
    });

    const { result } = renderHook(() => useMicrosoftAuth());
    let loginResult;
    await act(async () => {
      loginResult = await result.current.login(false);
    });

    expect(loginResult).toEqual({
      success: false,
      error:
        "LogSentinel API permission must be granted before Microsoft sign-in can continue.",
    });
  });

  it.each([
    [
      "microsoft_auth_disabled",
      "Microsoft sign-in is not currently configured.",
    ],
    [
      "invalid_microsoft_token",
      "Microsoft sign-in could not be verified. Please try again.",
    ],
    [
      "invalid_microsoft_tenant",
      "This Microsoft organization is not allowed to use LogSentinel.",
    ],
    [
      "missing_required_scope",
      "LogSentinel API permission is missing. Ask an administrator to grant access.",
    ],
    [
      "account_linking_required",
      "An existing LogSentinel account uses this email. Link accounts explicitly before using Microsoft sign-in.",
    ],
    [
      "microsoft_identity_conflict",
      "This Microsoft identity conflicts with an existing account mapping. Contact an administrator.",
    ],
    [
      "microsoft_onboarding_required",
      "Microsoft did not provide the account information required to create a LogSentinel user.",
    ],
    [
      "microsoft_jwks_unavailable",
      "Microsoft verification is temporarily unavailable. Please try again later.",
    ],
  ])("maps backend code %s", async (detail, expectedMessage) => {
    mockMsal.instance.loginPopup.mockResolvedValue({
      accessToken: "microsoft-token",
      account,
    });
    fetchMock.mockResolvedValue({
      ok: false,
      status: detail.includes("unavailable") || detail.includes("disabled") ? 503 : 409,
      json: vi.fn().mockResolvedValue({ detail }),
    });

    const { result } = renderHook(() => useMicrosoftAuth());
    let loginResult;
    await act(async () => {
      loginResult = await result.current.login(false);
    });

    expect(loginResult).toEqual({ success: false, error: expectedMessage });
    expect(setAuthToken).not.toHaveBeenCalled();
    expect(result.current.loading).toBe(false);
  });

  it("never renders an unknown raw backend detail", async () => {
    mockMsal.instance.loginPopup.mockResolvedValue({
      accessToken: "microsoft-token",
      account,
    });
    fetchMock.mockResolvedValue({
      ok: false,
      status: 403,
      json: vi.fn().mockResolvedValue({
        detail: "tenant-guid-and-jwks-validation-detail",
      }),
    });

    const { result } = renderHook(() => useMicrosoftAuth());
    let loginResult;
    await act(async () => {
      loginResult = await result.current.login(false);
    });

    expect(loginResult).toEqual({
      success: false,
      error: "This Microsoft account does not have permission to use LogSentinel.",
    });
    expect(JSON.stringify(loginResult)).not.toContain("tenant-guid");
  });

  it("handles a non-JSON backend error", async () => {
    mockMsal.instance.loginPopup.mockResolvedValue({
      accessToken: "microsoft-token",
      account,
    });
    fetchMock.mockResolvedValue({
      ok: false,
      status: 503,
      json: vi.fn().mockRejectedValue(new SyntaxError("not JSON")),
    });

    const { result } = renderHook(() => useMicrosoftAuth());
    let loginResult;
    await act(async () => {
      loginResult = await result.current.login(false);
    });

    expect(loginResult).toEqual({
      success: false,
      error: "Microsoft sign-in is temporarily unavailable. Please try again later.",
    });
  });

  it("rejects malformed successful backend JSON and does not navigate or store", async () => {
    mockMsal.instance.loginPopup.mockResolvedValue({
      accessToken: "microsoft-token",
      account,
    });
    fetchMock.mockResolvedValue({
      ok: true,
      status: 200,
      json: vi.fn().mockRejectedValue(new SyntaxError("not JSON")),
    });

    const { result } = renderHook(() => useMicrosoftAuth());
    let loginResult;
    await act(async () => {
      loginResult = await result.current.login(false);
    });

    expect(loginResult).toEqual({
      success: false,
      error:
        "The authentication service returned an invalid LogSentinel session. Please retry.",
    });
    expect(setAuthToken).not.toHaveBeenCalled();
  });

  it("guards against duplicate hook-level operations", async () => {
    let resolvePopup!: (value: unknown) => void;
    mockMsal.instance.loginPopup.mockReturnValue(
      new Promise((resolve) => {
        resolvePopup = resolve;
      }),
    );

    const { result } = renderHook(() => useMicrosoftAuth());
    let firstPromise!: Promise<unknown>;
    let duplicateResult;
    await act(async () => {
      firstPromise = result.current.login(false);
      duplicateResult = await result.current.login(false);
    });

    expect(duplicateResult).toEqual({ success: false });
    expect(mockMsal.instance.loginPopup).toHaveBeenCalledTimes(1);

    await act(async () => {
      resolvePopup({ accessToken: "microsoft-token", account });
      await firstPromise;
    });
    expect(setAuthToken).toHaveBeenCalledTimes(1);
  });
});
