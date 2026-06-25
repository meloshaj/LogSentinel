import { useEffect, useRef, useState } from "react";
import { mockLogs } from "../services/mockMonitoringData";
import type { LogEntry, LogLevel } from "../types/monitoring";

const liveLogTemplates: LogEntry[] = [
  { id: "n1", timestamp: "", level: "INFO", service: "api-gateway", message: "New request: POST /api/v2/payments" },
  { id: "n2", timestamp: "", level: "ERROR", service: "database-service", message: "Deadlock detected on table transactions - aborting" },
  { id: "n3", timestamp: "", level: "WARN", service: "payment-service", message: "Retry 3/3 failed - marking request as failed" },
  { id: "n4", timestamp: "", level: "INFO", service: "cache-service", message: "L2 cache warmed - 8,432 keys loaded" },
  { id: "n5", timestamp: "", level: "ERROR", service: "api-gateway", message: "Timeout on downstream call to payment-service (5s)" },
  { id: "n6", timestamp: "", level: "DEBUG", service: "auth-service", message: "Token refresh scheduled for 143 expiring sessions" },
];

function getTimestamp() {
  return `${new Date().toTimeString().slice(0, 8)}.${String(Date.now() % 1000).padStart(3, "0")}`;
}

export function useLiveLogs() {
  const [logs, setLogs] = useState<LogEntry[]>(mockLogs);
  const [paused, setPaused] = useState(false);
  const [filter, setFilter] = useState<LogLevel | "ALL">("ALL");
  const [newIds, setNewIds] = useState<Set<string>>(new Set());
  const counterRef = useRef(0);

  useEffect(() => {
    if (paused) return;

    const intervalId = window.setInterval(() => {
      const template = liveLogTemplates[counterRef.current % liveLogTemplates.length];
      counterRef.current += 1;
      const entry: LogEntry = {
        ...template,
        id: `live-${Date.now()}`,
        timestamp: getTimestamp(),
      };

      setLogs((previousLogs) => [...previousLogs.slice(-80), entry]);
      setNewIds((previousIds) => {
        const nextIds = new Set(previousIds);
        nextIds.add(entry.id);
        window.setTimeout(() => {
          setNewIds((ids) => {
            const updatedIds = new Set(ids);
            updatedIds.delete(entry.id);
            return updatedIds;
          });
        }, 2000);
        return nextIds;
      });
    }, 1500);

    return () => window.clearInterval(intervalId);
  }, [paused]);

  const filteredLogs = filter === "ALL" ? logs : logs.filter((log) => log.level === filter);

  return {
    filter,
    filteredLogs,
    newIds,
    paused,
    setFilter,
    setPaused,
  };
}
