import { useEffect, useRef } from "react";
import { Circle, Pause, Play, Terminal } from "lucide-react";
import { LOG_LEVEL_COLORS, SERVICE_TEXT_COLORS } from "../../constants/statusConfig";
import { useLiveLogs } from "../../hooks/useLiveLogs";
import type { LogEntry } from "../../types/monitoring";

const LOG_FILTERS = ["ALL", "ERROR", "WARN", "INFO", "DEBUG"] as const;

function LogRow({ entry, isNew }: { entry: LogEntry; isNew: boolean }) {
  const levelConfig = LOG_LEVEL_COLORS[entry.level];
  const serviceColor = SERVICE_TEXT_COLORS[entry.service] ?? "text-[#e6edf3]";

  return (
    <div
      className={`flex items-start gap-2.5 px-3 py-2 rounded-md transition-all duration-300 ${
        isNew ? "bg-[#1f6feb]/5 border border-[#1f6feb]/20" : "hover:bg-[#21262d]/60"
      }`}
    >
      <span className="text-[#484f58] shrink-0 mt-0.5" style={{ fontSize: "10px", fontFamily: "monospace", whiteSpace: "nowrap" }}>
        {entry.timestamp}
      </span>
      <span
        className="shrink-0 px-1.5 py-0.5 rounded mt-0.5"
        style={{ fontSize: "10px", fontWeight: 700, fontFamily: "monospace", color: levelConfig.color, background: levelConfig.bg }}
      >
        {entry.level}
      </span>
      <span className={`shrink-0 ${serviceColor} mt-0.5`} style={{ fontSize: "10px", fontFamily: "monospace" }}>
        {entry.service}
      </span>
      <span className="text-[#c9d1d9]" style={{ fontSize: "11px", fontFamily: "monospace", lineHeight: 1.5 }}>
        {entry.message}
      </span>
    </div>
  );
}

export function LogStream() {
  const { filter, filteredLogs, newIds, paused, setFilter, setPaused } = useLiveLogs();
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!paused && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [filteredLogs, paused]);

  return (
    <div className="flex flex-col h-full rounded-xl bg-[#161b22] border border-[#21262d] overflow-hidden">
      <div className="flex items-center justify-between px-4 py-3 border-b border-[#21262d] shrink-0">
        <div className="flex items-center gap-2">
          <Terminal className="w-4 h-4 text-[#388bfd]" />
          <span className="text-[#e6edf3]" style={{ fontSize: "13px", fontWeight: 600 }}>Live Log Stream</span>
          <span className="flex items-center gap-1">
            <Circle className="w-2 h-2 text-[#3fb950] fill-[#3fb950]" />
            <span className="text-[#3fb950]" style={{ fontSize: "10px", fontWeight: 500 }}>LIVE</span>
          </span>
        </div>
        <div className="flex items-center gap-2" role="toolbar" aria-label="Log stream controls">
          {LOG_FILTERS.map((level) => (
            <button
              key={level}
              type="button"
              onClick={() => setFilter(level)}
              aria-pressed={filter === level}
              className={`px-2 py-0.5 rounded transition-colors ${
                filter === level
                  ? "bg-[#21262d] text-[#e6edf3]"
                  : "text-[#7d8590] hover:text-[#e6edf3]"
              }`}
              style={{ fontSize: "10px", fontWeight: 600 }}
            >
              {level}
            </button>
          ))}
          <button
            type="button"
            onClick={() => setPaused((value) => !value)}
            className="ml-2 flex items-center gap-1.5 px-2.5 py-1 rounded-md bg-[#21262d] text-[#7d8590] hover:text-[#e6edf3] transition-colors"
            aria-label={paused ? "Resume live log stream" : "Pause live log stream"}
          >
            {paused ? <Play className="w-3 h-3" /> : <Pause className="w-3 h-3" />}
            <span style={{ fontSize: "10px", fontWeight: 500 }}>{paused ? "Resume" : "Pause"}</span>
          </button>
        </div>
      </div>

      <div
        ref={scrollRef}
        className="flex-1 overflow-y-auto px-2 py-2 space-y-0.5 font-mono"
        style={{ scrollBehavior: "smooth" }}
        aria-live={paused ? "off" : "polite"}
      >
        {filteredLogs.map((entry) => (
          <LogRow key={entry.id} entry={entry} isNew={newIds.has(entry.id)} />
        ))}
      </div>

      <div className="flex items-center justify-between px-4 py-2 border-t border-[#21262d] shrink-0">
        <span className="text-[#484f58]" style={{ fontSize: "10px" }}>
          {filteredLogs.length} entries - auto-scroll {paused ? "paused" : "enabled"}
        </span>
        <span className="text-[#484f58]" style={{ fontSize: "10px" }}>2,450 logs/min ingestion rate</span>
      </div>
    </div>
  );
}
