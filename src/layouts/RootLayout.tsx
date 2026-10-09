import { Outlet, useLocation } from "react-router";
import { Bell, Moon, RefreshCw, Search, Sun, Zap } from "lucide-react";
import { DEFAULT_PAGE_META, PAGE_META } from "../constants/pageMeta";
import { useClock } from "../hooks/useClock";
import { useThemeMode } from "../hooks/useThemeMode";
import { Sidebar } from "./Sidebar";
import { TelemetryProvider } from "../providers/TelemetryProvider";
import { AddDataSourceButton } from "../components/integrations/DataSourceModal";
import { displayOperationalStatus, operationalStatusLabel } from "../components/common/OperationalState";
import { useTelemetryContext } from "../providers/TelemetryProvider";
import { useWorkerHealth } from "../hooks/useWorkerHealth";

function Header() {
  const location = useLocation();
  const meta = PAGE_META[location.pathname] ?? DEFAULT_PAGE_META;
  const time = useClock();
  const { themeMode, toggleTheme } = useThemeMode();
  const isDarkMode = themeMode === "dark";
  const { connectionState, logDataState, trackingLoopsDataState } = useTelemetryContext();
  const workerState = useWorkerHealth();
  const logStatus = displayOperationalStatus(logDataState, connectionState);
  const trackingStatus = displayOperationalStatus(trackingLoopsDataState, connectionState);
  const workerStatus = displayOperationalStatus(workerState);
  const globalLabel = connectionState === "auth_required"
    ? "Session expired"
    : connectionState === "reconnecting"
      ? "Reconnecting"
      : connectionState === "offline" || connectionState === "stale" || connectionState === "failed"
        ? "Telemetry disconnected"
        : logStatus === "error" || logStatus === "unavailable" || trackingStatus === "error" || trackingStatus === "unavailable" || workerStatus === "error" || workerStatus === "unavailable"
          ? "Operational data unavailable"
          : logStatus === "stale" || trackingStatus === "stale" || workerStatus === "stale"
            ? "Operational data stale"
            : logStatus === "loading" || trackingStatus === "loading" || workerStatus === "loading"
              ? "Loading operational data"
              : logStatus === "empty" || trackingStatus === "empty" || workerStatus === "empty"
                ? "No operational data"
                : logStatus === "available" && trackingStatus === "available" && workerStatus === "available"
                  ? "Telemetry current"
                  : "Operational data unavailable";
  const globalIsHealthy = globalLabel === "Telemetry current";

  return (
    <header className="flex items-center justify-between px-5 py-3 border-b border-[#21262d] bg-[#0d1117] shrink-0">
      <div className="flex items-center gap-3">
        <div>
          <div className="text-[#e6edf3]" style={{ fontSize: "14px", fontWeight: 600 }}>{meta.title}</div>
          <div className="text-[#7d8590]" style={{ fontSize: "11px" }}>Production - us-east-1 - {time}</div>
        </div>
        <div className={`flex items-center gap-1.5 rounded-full border px-2.5 py-1 ${globalIsHealthy ? "border-[#3fb950]/20 bg-[#3fb950]/10 text-[#3fb950]" : "border-[#d29922]/30 bg-[#d29922]/15 text-[#d29922]"}`} role={globalIsHealthy ? "status" : "alert"}>
          <span className="h-1.5 w-1.5 rounded-full bg-current" />
          <span style={{ fontSize: "10px", fontWeight: 600 }}>{globalLabel}</span>
        </div>
      </div>
      <div className="flex items-center gap-2">
        <div className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-[#161b22] border border-[#21262d]">
          <Search className="w-3.5 h-3.5 text-[#484f58]" />
          <input
            type="text"
            placeholder="Search logs, services..."
            aria-label="Search logs and services"
            className="bg-transparent text-[#7d8590] placeholder:text-[#484f58] outline-none w-40"
            style={{ fontSize: "12px" }}
          />
        </div>
        <AddDataSourceButton variant="compact" />
        <button aria-label="Refresh dashboard" className="flex items-center justify-center w-8 h-8 rounded-lg bg-[#161b22] border border-[#21262d] text-[#7d8590] hover:text-[#e6edf3] transition-colors">
          <RefreshCw className="w-4 h-4" />
        </button>
        <button
          type="button"
          aria-label={`Switch to ${isDarkMode ? "light" : "dark"} mode`}
          title={`Switch to ${isDarkMode ? "light" : "dark"} mode`}
          onClick={toggleTheme}
          className="flex items-center justify-center w-8 h-8 rounded-lg bg-[#161b22] border border-[#21262d] text-[#7d8590] hover:text-[#e6edf3] transition-colors"
        >
          {isDarkMode ? <Sun className="w-4 h-4" /> : <Moon className="w-4 h-4" />}
        </button>
        <div className="w-px h-5 bg-[#21262d]" />
        <div className="flex items-center gap-1 px-2.5 py-1.5 rounded-lg bg-[#161b22] border border-[#21262d]">
          <Zap className="h-3 w-3 text-[#388bfd]" />
          <span className="text-[#7d8590]" style={{ fontSize: "11px" }}>Operational state</span>
          <span className="text-[10px] font-semibold text-[#c9d1d9]">{operationalStatusLabel(logStatus)}</span>
        </div>
      </div>
    </header>
  );
}

export function RootLayout() {
  return (
    <TelemetryProvider>
      <a href="#main-content" className="sr-only focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-[10000] focus:bg-white focus:text-black focus:px-3 focus:py-2">Skip to main content</a>
      <div className="flex h-screen w-full bg-[#0d1117] text-[#e6edf3] overflow-hidden">
        <Sidebar />
        <div className="flex flex-col flex-1 min-w-0 overflow-hidden">
          <Header />
          <main id="main-content" tabIndex={-1} className="flex-1 overflow-y-auto p-5 focus:outline focus:outline-2 focus:outline-sky-400">
            <Outlet />
          </main>
        </div>
      </div>
    </TelemetryProvider>
  );
}
