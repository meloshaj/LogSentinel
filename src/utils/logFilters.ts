import type { LogEntry } from "../types/monitoring";

/** Apply the same service and text semantics to live and historical views. */
export function filterLogEntries(
  logs: LogEntry[],
  serviceFilter = "All Services",
  searchQuery = "",
): LogEntry[] {
  const query = searchQuery.trim().toLowerCase();
  return logs.filter((entry) => {
    if (
      serviceFilter &&
      serviceFilter !== "All Services" &&
      entry.service !== serviceFilter
    ) {
      return false;
    }
    if (!query) return true;
    return [entry.message, entry.service, entry.level].some((value) =>
      value.toLowerCase().includes(query),
    );
  });
}
