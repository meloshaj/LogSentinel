import { Outlet, useLocation } from "react-router";
import { Bell, RefreshCw, Search, Zap } from "lucide-react";
import { DEFAULT_PAGE_META, PAGE_META } from "../constants/pageMeta";
import { useClock } from "../hooks/useClock";
import { Sidebar } from "./Sidebar";

function Header() {
  const location = useLocation();
  const meta = PAGE_META[location.pathname] ?? DEFAULT_PAGE_META;
  const time = useClock();

  return (
    <header className="flex items-center justify-between px-5 py-3 border-b border-[#21262d] bg-[#0d1117] shrink-0">
      <div className="flex items-center gap-3">
        <div>
          <div className="text-[#e6edf3]" style={{ fontSize: "14px", fontWeight: 600 }}>{meta.title}</div>
          <div className="text-[#7d8590]" style={{ fontSize: "11px" }}>Production - us-east-1 - {time}</div>
        </div>
        <div className="flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-[#da3633]/15 border border-[#da3633]/30">
          <span className="w-1.5 h-1.5 rounded-full bg-[#f85149] animate-pulse" />
          <span className="text-[#f85149]" style={{ fontSize: "10px", fontWeight: 600 }}>INCIDENT ACTIVE</span>
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
        <button aria-label="View notifications" className="relative flex items-center justify-center w-8 h-8 rounded-lg bg-[#161b22] border border-[#21262d] text-[#7d8590] hover:text-[#e6edf3] transition-colors">
          <Bell className="w-4 h-4" />
          <span className="absolute top-1 right-1 w-1.5 h-1.5 rounded-full bg-[#f85149]" />
        </button>
        <button aria-label="Refresh dashboard" className="flex items-center justify-center w-8 h-8 rounded-lg bg-[#161b22] border border-[#21262d] text-[#7d8590] hover:text-[#e6edf3] transition-colors">
          <RefreshCw className="w-4 h-4" />
        </button>
        <div className="w-px h-5 bg-[#21262d]" />
        <div className="flex items-center gap-1 px-2.5 py-1.5 rounded-lg bg-[#161b22] border border-[#21262d]">
          <Zap className="w-3 h-3 text-[#388bfd]" />
          <span className="text-[#7d8590]" style={{ fontSize: "11px" }}>ML Engine</span>
          <span className="w-1.5 h-1.5 rounded-full bg-[#3fb950]" />
        </div>
      </div>
    </header>
  );
}

export function RootLayout() {
  return (
    <div className="flex h-screen w-full bg-[#0d1117] text-[#e6edf3] overflow-hidden">
      <Sidebar />
      <div className="flex flex-col flex-1 min-w-0 overflow-hidden">
        <Header />
        <main className="flex-1 overflow-y-auto p-5">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
