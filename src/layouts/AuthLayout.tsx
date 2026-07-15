import { Outlet } from "react-router";
import { BackgroundIllustration } from "../pages/AuthShared";

export function AuthLayout() {
  return (
    <div className="relative min-h-screen w-full flex flex-col items-center justify-center overflow-hidden px-4 py-12 font-[Inter,system-ui,sans-serif]">
      {/* Base canvas */}
      <div className="fixed inset-0 bg-[#060c18]" />

      {/* Layered gradients */}
      <div className="fixed inset-0 bg-gradient-to-br from-blue-950/20 via-transparent to-indigo-950/15" />
      <div className="fixed inset-0 bg-[radial-gradient(ellipse_80%_60%_at_50%_50%,rgba(14,165,233,0.05)_0%,transparent_70%)]" />

      {/* Network background illustration */}
      <BackgroundIllustration />

      {/* Page content */}
      <div className="relative z-10 w-full flex flex-col items-center gap-6">
        {/* Tagline pill */}
        <div className="flex items-center gap-2 px-3.5 py-1.5 rounded-full border border-slate-700/40 bg-slate-900/50 backdrop-blur-sm">
          <div className="w-1.5 h-1.5 rounded-full bg-sky-400 animate-pulse" />
          <span className="text-[11px] font-mono tracking-[0.14em] text-slate-500 uppercase select-none">
            Real-Time Log Intelligence &amp; Root Cause Analysis
          </span>
        </div>

        {/* Card and Form */}
        <Outlet />

        {/* Footer trust signals */}
        <div className="flex items-center gap-4 text-[11px] text-slate-700 font-mono select-none">
          <span>© 2025 LogSentinel Inc.</span>
          <span className="w-px h-3 bg-slate-800" />
          <span>SOC 2 Type II</span>
          <span className="w-px h-3 bg-slate-800" />
          <span>99.99% SLA</span>
          <span className="w-px h-3 bg-slate-800" />
          <span>GDPR</span>
        </div>
      </div>
    </div>
  );
}
