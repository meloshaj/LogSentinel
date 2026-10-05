import { render } from "@testing-library/react";
import axe from "axe-core";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";
import { AnomalyDrawer } from "../AnomalyDrawer";

describe("AnomalyDrawer accessibility", () => {
  it("passes automated axe checks for the critical dialog", async () => {
    const { container } = render(<MemoryRouter><AnomalyDrawer isOpen onClose={vi.fn()} incident={{ id: "1", service: "api", severity: "critical", timestamp: "12:00", description: "Failure", status: "open" }} /></MemoryRouter>);
    const result = await axe.run(container);
    expect(result.violations).toEqual([]);
  });
});
