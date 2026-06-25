export const PAGE_META: Record<string, { title: string; subtitle: string }> = {
  "/": { title: "Overview", subtitle: "Executive summary - system health at a glance" },
  "/logs": { title: "Live Logs", subtitle: "Real-time log stream across all services" },
  "/anomalies": { title: "Anomalies", subtitle: "ML-powered anomaly detection - updated in real-time" },
  "/ai": { title: "AI Analysis", subtitle: "Root cause analysis and suggested remediation" },
  "/incidents": { title: "Incidents", subtitle: "Active incidents, timeline and resolution status" },
  "/analytics": { title: "Analytics", subtitle: "Error trends, service performance and usage stats" },
  "/settings": { title: "Settings", subtitle: "Configure notifications, thresholds and team access" },
};

export const DEFAULT_PAGE_META = {
  title: "LogSentinel",
  subtitle: "Production - us-east-1",
};
