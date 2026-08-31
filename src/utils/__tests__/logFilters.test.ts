import { describe, expect, it } from "vitest";
import { filterLogEntries } from "../logFilters";

const logs = [
  { id: "a", service: "api-gateway", level: "ERROR", message: "payment timeout", timestamp: "1" },
  { id: "b", service: "order-service", level: "INFO", message: "order accepted", timestamp: "2" },
  { id: "c", service: "payment-gateway", level: "WARN", message: "retrying charge", timestamp: "3" },
] as const;

describe("filterLogEntries", () => {
  it("filters service identities without collapsing a multi-service stream", () => {
    expect(filterLogEntries([...logs], "order-service").map((log) => log.id)).toEqual(["b"]);
    expect(filterLogEntries([...logs], "All Services")).toHaveLength(3);
  });

  it("applies the same search semantics to message, service, and level", () => {
    expect(filterLogEntries([...logs], "All Services", "timeout").map((log) => log.id)).toEqual(["a"]);
    expect(filterLogEntries([...logs], "All Services", "payment").map((log) => log.id)).toEqual(["a", "c"]);
    expect(filterLogEntries([...logs], "payment-gateway", "error")).toHaveLength(0);
  });
});
