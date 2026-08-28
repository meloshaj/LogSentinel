import { StrictMode } from "react";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const providerMocks = vi.hoisted(() => {
  const instance = {
    initialize: vi.fn(),
    clearCache: vi.fn(),
    getActiveAccount: vi.fn(),
    setActiveAccount: vi.fn(),
  };
  return {
    configured: false,
    instance,
    constructorMock: vi.fn(function MockPublicClientApplication() {
      return instance;
    }),
    msalProviderMock: vi.fn(({ children }) => children),
  };
});

vi.mock("../../src/config/msal", () => ({
  isMsalConfigured: () => providerMocks.configured,
  msalInstanceConfig: { auth: { clientId: "valid-client-id" } },
}));

vi.mock("@azure/msal-browser", () => ({
  PublicClientApplication: providerMocks.constructorMock,
}));

vi.mock("@azure/msal-react", () => ({
  MsalProvider: providerMocks.msalProviderMock,
}));

import {
  __resetMsalProviderStateForTests,
  clearMicrosoftAuthCache,
  MsalProviderWrapper,
  useMicrosoftAuthStatus,
} from "../../src/providers/MsalProviderWrapper";

function StatusProbe() {
  const status = useMicrosoftAuthStatus();
  return <span>Microsoft status: {status}</span>;
}

describe("MsalProviderWrapper", () => {
  let consoleError: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    cleanup();
    __resetMsalProviderStateForTests();
    vi.clearAllMocks();
    providerMocks.configured = false;
    providerMocks.instance.initialize.mockResolvedValue(undefined);
    providerMocks.instance.clearCache.mockResolvedValue(undefined);
    consoleError = vi.spyOn(console, "error").mockImplementation(() => undefined);
  });

  afterEach(() => {
    cleanup();
    vi.useRealTimers();
    consoleError.mockRestore();
  });

  it("does not construct or initialize MSAL when disabled", () => {
    render(
      <MsalProviderWrapper>
        <StatusProbe />
      </MsalProviderWrapper>,
    );

    expect(screen.getByText("Microsoft status: disabled")).toBeInTheDocument();
    expect(providerMocks.constructorMock).not.toHaveBeenCalled();
    expect(providerMocks.instance.initialize).not.toHaveBeenCalled();
    expect(providerMocks.msalProviderMock).not.toHaveBeenCalled();
  });

  it("constructs one instance and initializes exactly once in React Strict Mode", async () => {
    providerMocks.configured = true;

    render(
      <StrictMode>
        <MsalProviderWrapper>
          <StatusProbe />
        </MsalProviderWrapper>
      </StrictMode>,
    );

    expect(
      await screen.findByText("Microsoft status: ready"),
    ).toBeInTheDocument();
    expect(providerMocks.constructorMock).toHaveBeenCalledTimes(1);
    expect(providerMocks.instance.initialize).toHaveBeenCalledTimes(1);
    expect(providerMocks.msalProviderMock).toHaveBeenCalled();
  });

  it("renders children in a controlled error state after initialization failure", async () => {
    providerMocks.configured = true;
    providerMocks.instance.initialize.mockRejectedValue(
      new Error("raw initialization detail"),
    );

    render(
      <MsalProviderWrapper>
        <StatusProbe />
      </MsalProviderWrapper>,
    );

    expect(
      await screen.findByText("Microsoft status: error"),
    ).toBeInTheDocument();
    expect(providerMocks.msalProviderMock).not.toHaveBeenCalled();
    expect(consoleError).toHaveBeenCalledWith(
      "Microsoft authentication initialization failed",
    );
    expect(JSON.stringify(consoleError.mock.calls)).not.toContain(
      "raw initialization detail",
    );
  });

  it("times out to a controlled state instead of showing an indefinite blank screen", async () => {
    vi.useFakeTimers();
    providerMocks.configured = true;
    providerMocks.instance.initialize.mockReturnValue(new Promise(() => undefined));

    render(
      <MsalProviderWrapper>
        <StatusProbe />
      </MsalProviderWrapper>,
    );

    expect(screen.getByRole("status")).toHaveTextContent(
      "Initializing authentication...",
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });
    expect(screen.getByText("Microsoft status: error")).toBeInTheDocument();
  });

  it("clears MSAL-managed cache without touching application storage", async () => {
    providerMocks.configured = true;
    render(
      <MsalProviderWrapper>
        <StatusProbe />
      </MsalProviderWrapper>,
    );
    await waitFor(() => {
      expect(screen.getByText("Microsoft status: ready")).toBeInTheDocument();
    });

    await clearMicrosoftAuthCache();

    expect(providerMocks.instance.clearCache).toHaveBeenCalledWith();
    expect(providerMocks.instance.setActiveAccount).toHaveBeenCalledWith(null);
  });
});
