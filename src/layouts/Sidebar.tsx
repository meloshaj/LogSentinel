import { NavLink } from "react-router";
import { ChevronRight, Cpu, Terminal, Zap } from "lucide-react";
import { PRIMARY_NAV_ITEMS, SECONDARY_NAV_ITEMS } from "../constants/navigation";
import type { NavigationItem } from "../constants/navigation";

function NavigationLink({ item }: { item: NavigationItem }) {
  return (
    <NavLink
      to={item.to}
      end={item.to === "/"}
      className={({ isActive }) =>
        `flex items-center gap-3 px-3 py-2 rounded-lg transition-colors group ${
          isActive
            ? "bg-[#1f6feb]/20 text-[#388bfd]"
            : "text-[#7d8590] hover:bg-[#161b22] hover:text-[#e6edf3]"
        }`
      }
    >
      {({ isActive }) => (
        <>
          <item.icon className={`w-4 h-4 shrink-0 ${isActive ? "text-[#388bfd]" : ""}`} />
          <span style={{ fontSize: "13px", fontWeight: isActive ? 500 : 400 }}>{item.label}</span>
          {item.badge !== undefined && (
            <span className="ml-auto px-1.5 py-0.5 rounded-full bg-[#da3633] text-white" style={{ fontSize: "10px", fontWeight: 600, lineHeight: 1 }}>
              {item.badge}
            </span>
          )}
          {isActive && !item.badge && <ChevronRight className="ml-auto w-3 h-3" />}
        </>
      )}
    </NavLink>
  );
}

export function Sidebar() {
  return (
    <aside className="flex flex-col h-full w-56 bg-[#0d1117] border-r border-[#21262d] shrink-0">
      <div className="flex items-center gap-2.5 px-4 py-4 border-b border-[#21262d]">
        <div className="flex items-center justify-center w-8 h-8 rounded-lg bg-gradient-to-br from-[#1f6feb] to-[#388bfd]">
          <Zap className="w-4 h-4 text-white" />
        </div>
        <div>
          <div className="text-[#e6edf3] leading-none" style={{ fontSize: "13px", fontWeight: 600 }}>LogSentinel</div>
          <div className="text-[#7d8590]" style={{ fontSize: "10px", fontWeight: 400, marginTop: "2px" }}>v2.4.1 - Production</div>
        </div>
      </div>

      <div className="mx-3 mt-3 mb-1 px-3 py-2 rounded-lg bg-[#161b22] border border-[#21262d] flex items-center gap-2">
        <span className="w-2 h-2 rounded-full bg-[#3fb950] animate-pulse shrink-0" />
        <span className="text-[#3fb950]" style={{ fontSize: "11px", fontWeight: 500 }}>System monitoring active</span>
      </div>

      <nav className="flex-1 px-2 py-2 space-y-0.5 overflow-y-auto" aria-label="Primary navigation">
        <div className="px-2 py-1.5">
          <span className="text-[#7d8590] uppercase tracking-widest" style={{ fontSize: "10px", fontWeight: 600 }}>Navigation</span>
        </div>
        {PRIMARY_NAV_ITEMS.map((item) => <NavigationLink key={item.to} item={item} />)}
      </nav>

      <div className="px-2 py-2 border-t border-[#21262d] space-y-0.5">
        {SECONDARY_NAV_ITEMS.map((item) => <NavigationLink key={item.to} item={item} />)}
        <div className="flex items-center gap-2.5 px-3 py-2 mt-1 rounded-lg bg-[#161b22]">
          <div className="flex items-center justify-center w-6 h-6 rounded-full bg-gradient-to-br from-[#1f6feb] to-[#388bfd] shrink-0">
            <Cpu className="w-3 h-3 text-white" />
          </div>
          <div>
            <div className="text-[#e6edf3]" style={{ fontSize: "12px", fontWeight: 500 }}>SRE Team</div>
            <div className="text-[#7d8590]" style={{ fontSize: "10px" }}>on-call engineer</div>
          </div>
          <Terminal className="w-3.5 h-3.5 text-[#7d8590] ml-auto" />
        </div>
      </div>
    </aside>
  );
}
