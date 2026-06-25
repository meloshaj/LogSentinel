import { Bell, Clock, Flame, XCircle } from "lucide-react";

export const LOG_LEVEL_COLORS = {
  INFO: { color: "#79c0ff", bg: "rgba(31,111,235,0.15)", text: "text-[#79c0ff]" },
  WARN: { color: "#d29922", bg: "rgba(210,153,34,0.15)", text: "text-[#d29922]" },
  ERROR: { color: "#f85149", bg: "rgba(218,54,51,0.15)", text: "text-[#f85149]" },
  DEBUG: { color: "#7d8590", bg: "rgba(125,133,144,0.1)", text: "text-[#7d8590]" },
} as const;

export const SERVICE_TEXT_COLORS: Record<string, string> = {
  "api-gateway": "text-[#a5d6ff]",
  "auth-service": "text-[#bc8cff]",
  "payment-service": "text-[#f85149]",
  "database-service": "text-[#f85149]",
  "cache-service": "text-[#3fb950]",
  "notification-svc": "text-[#ffa657]",
};

export const ANOMALY_STATUS_CONFIG = {
  Critical: { color: "#f85149", bg: "bg-[#da3633]/15", border: "border-[#da3633]/40", dot: "bg-[#f85149]", label: "text-[#f85149]" },
  Warning: { color: "#d29922", bg: "bg-[#d29922]/15", border: "border-[#d29922]/30", dot: "bg-[#d29922]", label: "text-[#e3b341]" },
  Normal: { color: "#3fb950", bg: "bg-[#3fb950]/10", border: "border-[#3fb950]/20", dot: "bg-[#3fb950]", label: "text-[#3fb950]" },
} as const;

export const INCIDENT_SEVERITY_CONFIG = {
  critical: { label: "CRITICAL", color: "#f85149", icon: Flame },
  high: { label: "HIGH", color: "#ffa657", icon: XCircle },
  medium: { label: "MEDIUM", color: "#d29922", icon: Bell },
  low: { label: "LOW", color: "#7d8590", icon: Clock },
} as const;

export const INCIDENT_STATUS_CONFIG = {
  investigating: { label: "Investigating", color: "#d29922", dot: "bg-[#d29922] animate-pulse" },
  open: { label: "Open", color: "#f85149", dot: "bg-[#f85149] animate-pulse" },
  resolved: { label: "Resolved", color: "#3fb950", dot: "bg-[#3fb950]" },
} as const;

export const DEPENDENCY_NODE_STATUS: Record<string, { fill: string; stroke: string }> = {
  "api-gateway": { fill: "#1c2128", stroke: "#d29922" },
  "auth-service": { fill: "#1c2128", stroke: "#bc8cff" },
  "payment-service": { fill: "#2d1a1a", stroke: "#f85149" },
  "database-service": { fill: "#2d1a1a", stroke: "#f85149" },
  "cache-service": { fill: "#1a2d1a", stroke: "#3fb950" },
  "notification-svc": { fill: "#1c2128", stroke: "#ffa657" },
};
