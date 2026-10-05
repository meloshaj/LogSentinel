import React from "react";
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router";
import { LoginPage } from "../../src/pages/LoginPage";
import App from "../../src/App";

const testState = vi.hoisted(() => ({
  renderRoute: vi.fn(),
  googleProvider: vi.fn(),
  initTokenClient: vi.fn(),
}));

vi.mock("react-router", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router")>();
  return {
    ...actual,
    RouterProvider: () => testState.renderRoute(),
  };
});

vi.mock("../../src/routes", () => ({ router: {} }));

vi.mock("../../src/providers/MsalProviderWrapper", () => ({
  MsalProviderWrapper: ({ children }: { children: React.ReactNode }) => children,
  useMicrosoftAuthStatus: () => "ready",
}));

vi.mock("../../src/hooks/useMicrosoftAuth", () => ({
  useMicrosoftAuth: () => ({
    login: vi.fn().mockResolvedValue({ success: false }),
    loading: false,
    error: null,
  }),
}));

vi.mock("@react-oauth/google", () => ({
  GoogleOAuthProvider: ({
    clientId,
    children,
  }: {
    clientId: string;
    children: React.ReactNode;
  }) => {
    testState.googleProvider(clientId);
    testState.initTokenClient(clientId);
    return children;
  },
  useGoogleLogin: () => {
    testState.initTokenClient("hook");
    return vi.fn();
  },
}));

describe("App Google auth fail-closed guard", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.unstubAllEnvs();
    vi.stubEnv("VITE_GOOGLE_CLIENT_ID", "");
    vi.stubEnv("VITE_GOOGLE_AUTH_ENABLED", "true");
    testState.renderRoute.mockReturnValue(
      <MemoryRouter>
        <LoginPage />
      </MemoryRouter>,
    );
  });

  it("renders the login page with Google unavailable and no SDK initialization when unconfigured", () => {
    render(<App />);

    expect(screen.getByLabelText("Email address")).toBeEnabled();
    expect(screen.getByLabelText("Password")).toBeEnabled();
    expect(screen.getByRole("button", { name: "Sign In" })).toBeEnabled();
    expect(
      screen.getByRole("button", { name: "Continue with Microsoft" }),
    ).toBeEnabled();
    expect(
      screen.getByRole("button", { name: "Continue with Google" }),
    ).toBeDisabled();
    expect(screen.getByText("Google sign-in is not configured.")).toHaveAttribute(
      "role",
      "status",
    );
    expect(testState.googleProvider).not.toHaveBeenCalled();
    expect(testState.initTokenClient).not.toHaveBeenCalled();
  });
});
