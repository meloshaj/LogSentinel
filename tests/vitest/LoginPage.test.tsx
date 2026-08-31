import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router";
import { LoginPage } from "../../src/pages/LoginPage";

const testState = vi.hoisted(() => ({
  navigate: vi.fn(),
  microsoftLogin: vi.fn(),
  microsoftStatus: "disabled" as
    | "disabled"
    | "initializing"
    | "ready"
    | "error",
  microsoftLoading: false,
}));

const useMicrosoftAuthMock = vi.hoisted(() => vi.fn());

vi.mock("react-router", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router")>();
  return { ...actual, useNavigate: () => testState.navigate };
});

vi.mock("../../src/providers/MsalProviderWrapper", () => ({
  useMicrosoftAuthStatus: () => testState.microsoftStatus,
}));

vi.mock("../../src/hooks/useMicrosoftAuth", () => ({
  useMicrosoftAuth: useMicrosoftAuthMock,
}));

vi.mock("@react-oauth/google", () => ({
  useGoogleLogin: (options: any) => {
    return () => {
      if (options.onSuccess) {
        options.onSuccess({ access_token: "google-credential" });
      }
    };
  },
  GoogleOAuthProvider: ({ children }: { children: React.ReactNode }) => children,
}));

function renderLogin() {
  return render(
    <MemoryRouter>
      <LoginPage />
    </MemoryRouter>,
  );
}

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function enterCredentials(
  email = "  User@Example.COM  ",
  password = " exact password ",
) {
  fireEvent.change(screen.getByLabelText("Email address"), {
    target: { value: email },
  });
  fireEvent.change(screen.getByLabelText("Password"), {
    target: { value: password },
  });
}

describe("LoginPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.unstubAllEnvs();
    vi.stubEnv("VITE_API_URL", "http://localhost:8000");
    window.localStorage.clear();
    window.sessionStorage.clear();
    testState.microsoftStatus = "disabled";
    testState.microsoftLoading = false;
    testState.microsoftLogin.mockResolvedValue({ success: false });
    useMicrosoftAuthMock.mockImplementation(() => ({
      login: testState.microsoftLogin,
      loading: testState.microsoftLoading,
      error: null,
    }));
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllEnvs();
  });

  it("renders a disabled Microsoft entry without invoking an MSAL hook", () => {
    renderLogin();

    expect(
      screen.getByRole("button", { name: "Continue with Microsoft" }),
    ).toBeDisabled();
    expect(
      screen.getByText("Microsoft sign-in is not configured."),
    ).toBeInTheDocument();
    expect(useMicrosoftAuthMock).not.toHaveBeenCalled();
    expect(screen.getByLabelText("Email address")).toBeEnabled();
  });

  it("turns an initialization failure into controlled unavailable UI", () => {
    testState.microsoftStatus = "error";
    renderLogin();

    expect(
      screen.getByText("Microsoft sign-in is temporarily unavailable."),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Continue with Microsoft" }),
    ).toBeDisabled();
    expect(useMicrosoftAuthMock).not.toHaveBeenCalled();
  });

  it("associates labels, validation errors, and stable input metadata", async () => {
    renderLogin();

    const email = screen.getByLabelText("Email address");
    const password = screen.getByLabelText("Password");
    expect(email).toHaveAttribute("id", "email-address");
    expect(email).toHaveAttribute("name", "email");
    expect(email).toHaveAttribute("autocomplete", "username");
    expect(email).toBeRequired();
    expect(password).toHaveAttribute("id", "password");
    expect(password).toHaveAttribute("autocomplete", "current-password");

    fireEvent.click(screen.getByRole("button", { name: "Sign In" }));

    await waitFor(() => expect(email).toHaveFocus());
    expect(email).toHaveAttribute("aria-invalid", "true");
    expect(email).toHaveAttribute("aria-describedby", "email-address-error");
    expect(password).toHaveAttribute("aria-describedby", "password-error");
    expect(screen.getByText("Email address is required")).toHaveAttribute(
      "id",
      "email-address-error",
    );
  });

  it("normalizes only email and stores a checked email login persistently", async () => {
    vi.stubEnv("VITE_API_URL", "http://api.example.test/");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({ access_token: "internal-jwt" })));
    renderLogin();
    enterCredentials();
    fireEvent.click(screen.getByRole("checkbox", { name: "Remember me" }));

    fireEvent.click(screen.getByRole("button", { name: "Sign In" }));

    await screen.findByText("Signed in successfully");
    expect(fetch).toHaveBeenCalledWith(
      "http://api.example.test/api/auth/login",
      expect.objectContaining({
        body: JSON.stringify({
          email: "user@example.com",
          password: " exact password ",
        }),
      }),
    );
    expect(JSON.parse(String(vi.mocked(fetch).mock.calls[0][1]?.body))).not.toHaveProperty(
      "remember_me",
    );
    expect(localStorage.getItem("authToken")).toBe("internal-jwt");
    expect(sessionStorage.getItem("authToken")).toBeNull();
  });

  it("stores an unchecked email login in sessionStorage", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({ access_token: "session-jwt" })));
    renderLogin();
    enterCredentials("user@example.com", "password1");

    fireEvent.submit(screen.getByRole("button", { name: "Sign In" }).closest("form")!);

    await screen.findByText("Signed in successfully");
    expect(sessionStorage.getItem("authToken")).toBe("session-jwt");
    expect(localStorage.getItem("authToken")).toBeNull();
  });

  it("prevents navigation and focuses a failed email-login summary", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(response({ detail: "Invalid credentials" }, 401)),
    );
    renderLogin();
    enterCredentials("user@example.com", "password1");

    fireEvent.click(screen.getByRole("button", { name: "Sign In" }));

    const message = await screen.findByText("Invalid credentials");
    const alert = message.closest('[role="alert"]');
    expect(alert).not.toBeNull();
    await waitFor(() => expect(alert).toHaveFocus());
    expect(testState.navigate).not.toHaveBeenCalled();
    expect(localStorage.getItem("authToken")).toBeNull();
    expect(sessionStorage.getItem("authToken")).toBeNull();
    expect(screen.getByRole("button", { name: "Sign In" })).toBeEnabled();
  });

  it("locks every provider while an email request is in flight", async () => {
    let resolveFetch!: (value: Response) => void;
    vi.stubGlobal(
      "fetch",
      vi.fn().mockReturnValue(
        new Promise<Response>((resolve) => {
          resolveFetch = resolve;
        }),
      ),
    );
    testState.microsoftStatus = "ready";
    vi.stubEnv("VITE_GOOGLE_CLIENT_ID", "google-client-id");
    renderLogin();
    enterCredentials("user@example.com", "password1");

    fireEvent.click(screen.getByRole("button", { name: "Sign In" }));

    expect(screen.getByRole("button", { name: "Continue with Microsoft" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Continue with Google" })).toBeDisabled();
    expect(screen.getByLabelText("Email address")).toBeDisabled();
    expect(fetch).toHaveBeenCalledTimes(1);
    fireEvent.submit(screen.getByRole("button", { name: /Signing in/ }).closest("form")!);
    expect(fetch).toHaveBeenCalledTimes(1);

    resolveFetch(response({ access_token: "internal-jwt" }));
    await screen.findByText("Signed in successfully");
  });

  it.each([true, false])(
    "passes Microsoft Remember Me=%s and completes only after hook success",
    async (rememberMe) => {
      testState.microsoftStatus = "ready";
      testState.microsoftLogin.mockResolvedValue({ success: true });
      renderLogin();
      if (rememberMe) {
        fireEvent.click(screen.getByRole("checkbox", { name: "Remember me" }));
      }

      fireEvent.click(
        screen.getByRole("button", { name: "Continue with Microsoft" }),
      );

      await screen.findByText("Signed in successfully");
      expect(testState.microsoftLogin).toHaveBeenCalledWith(rememberMe);
    },
  );

  it("recovers from Microsoft failure without navigating", async () => {
    testState.microsoftStatus = "ready";
    testState.microsoftLogin.mockResolvedValue({
      success: false,
      error: "Microsoft sign-in could not be completed. Please try again.",
    });
    renderLogin();

    fireEvent.click(
      screen.getByRole("button", { name: "Continue with Microsoft" }),
    );

    const message = await screen.findByText(
      "Microsoft sign-in could not be completed. Please try again.",
    );
    await waitFor(() => expect(message.closest("div")).toHaveFocus());
    expect(testState.navigate).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Sign In" })).toBeEnabled();
  });

  it.each([
    [true, "localStorage"],
    [false, "sessionStorage"],
  ] as const)(
    "stores a Google login with Remember Me=%s in %s",
    async (rememberMe, storageName) => {
      vi.stubEnv("VITE_GOOGLE_CLIENT_ID", "google-client-id");
      vi.stubGlobal(
        "fetch",
        vi.fn().mockResolvedValue(response({ access_token: "google-internal-jwt" })),
      );
      renderLogin();
      if (rememberMe) {
        fireEvent.click(screen.getByRole("checkbox", { name: "Remember me" }));
      }

      fireEvent.click(screen.getByRole("button", { name: "Continue with Google" }));

      await screen.findByText("Signed in successfully");
      expect(fetch).toHaveBeenCalledWith(
        "http://localhost:8000/api/auth/google",
        expect.objectContaining({
          body: JSON.stringify({ credential: "google-credential" }),
        }),
      );
      const selectedStorage =
        storageName === "localStorage" ? localStorage : sessionStorage;
      expect(selectedStorage.getItem("authToken")).toBe("google-internal-jwt");
    },
  );

  it("navigates only after a successful login completes", async () => {
    vi.useFakeTimers();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({ access_token: "internal-jwt" })));
    renderLogin();
    enterCredentials("user@example.com", "password1");

    fireEvent.click(screen.getByRole("button", { name: "Sign In" }));
    await vi.waitFor(() =>
      expect(screen.getByText("Signed in successfully")).toBeInTheDocument(),
    );
    expect(testState.navigate).not.toHaveBeenCalled();

    await vi.advanceTimersByTimeAsync(1000);
    expect(testState.navigate).toHaveBeenCalledWith("/");
  });

  it("exposes an accessible password toggle and keyboard-focus treatment", async () => {
    const user = userEvent.setup();
    renderLogin();

    const password = screen.getByLabelText("Password");
    const toggle = screen.getByRole("button", { name: "Show password" });
    expect(toggle).toHaveAttribute("aria-pressed", "false");
    await user.click(toggle);
    expect(toggle).toHaveAttribute("aria-pressed", "true");
    expect(password).toHaveAttribute("type", "text");

    const checkbox = screen.getByRole("checkbox", { name: "Remember me" });
    expect(checkbox.nextElementSibling).toHaveClass(
      "peer-focus-visible:ring-2",
      "peer-focus-visible:ring-sky-500",
    );
  });
});
