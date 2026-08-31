import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router";
import { VerifyEmailPage } from "../../src/pages/VerifyEmailPage";

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status });
}

describe("VerifyEmailPage", () => {
  it("submits the canonical email and six-digit code without exposing it in the URL", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({ access_token: "jwt" })));
    window.localStorage.clear();
    render(
      <MemoryRouter initialEntries={["/verify-email?email=Alice%40Example.COM"]}>
        <VerifyEmailPage />
      </MemoryRouter>,
    );

    fireEvent.change(screen.getByLabelText("Verification code"), {
      target: { value: "123456" },
    });
    fireEvent.click(screen.getByRole("button", { name: /verify email/i }));

    await waitFor(() => expect(screen.getByText("Email verified")).toBeInTheDocument());
    expect(fetch).toHaveBeenCalledWith(
      "http://localhost:8000/api/auth/verify-email",
      expect.objectContaining({
        body: JSON.stringify({ email: "alice@example.com", code: "123456" }),
      }),
    );
    expect(window.location.search).not.toContain("123456");
  });

  it("rejects malformed codes before making a request", () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    render(
      <MemoryRouter>
        <VerifyEmailPage />
      </MemoryRouter>,
    );
    fireEvent.change(screen.getByLabelText("Email address"), {
      target: { value: "user@example.com" },
    });
    fireEvent.change(screen.getByLabelText("Verification code"), {
      target: { value: "12" },
    });
    fireEvent.click(screen.getByRole("button", { name: /verify email/i }));
    expect(screen.getByRole("alert")).toHaveTextContent("6-digit");
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
