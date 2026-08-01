import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router";
import { Sidebar } from "../../src/layouts/Sidebar";

const logoutMocks = vi.hoisted(() => ({
  navigate: vi.fn(),
  clearMicrosoftAuthCache: vi.fn(),
}));

vi.mock("react-router", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router")>();
  return { ...actual, useNavigate: () => logoutMocks.navigate };
});

vi.mock("../../src/providers/MsalProviderWrapper", () => ({
  clearMicrosoftAuthCache: logoutMocks.clearMicrosoftAuthCache,
}));

describe("Sidebar logout", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    sessionStorage.clear();
    logoutMocks.clearMicrosoftAuthCache.mockResolvedValue(undefined);
  });

  it("clears both LogSentinel stores and MSAL cache before navigation", async () => {
    localStorage.setItem("authToken", "persistent-token");
    sessionStorage.setItem("authToken", "session-token");
    render(
      <MemoryRouter>
        <Sidebar />
      </MemoryRouter>,
    );

    fireEvent.click(
      screen.getByRole("button", { name: "Log out of LogSentinel" }),
    );

    expect(localStorage.getItem("authToken")).toBeNull();
    expect(sessionStorage.getItem("authToken")).toBeNull();
    expect(logoutMocks.clearMicrosoftAuthCache).toHaveBeenCalledTimes(1);
    await waitFor(() =>
      expect(logoutMocks.navigate).toHaveBeenCalledWith("/login", {
        replace: true,
      }),
    );
    expect(
      logoutMocks.clearMicrosoftAuthCache.mock.invocationCallOrder[0],
    ).toBeLessThan(logoutMocks.navigate.mock.invocationCallOrder[0]);
  });
});
