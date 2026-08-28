import type { ReactNode } from "react";
import type { LucideIcon } from "lucide-react";

interface PanelProps {
  children: ReactNode;
  className?: string;
}

interface PanelHeaderProps {
  icon?: LucideIcon;
  iconClassName?: string;
  title: string;
  children?: ReactNode;
}

export function Panel({ children, className = "" }: PanelProps) {
  return (
    <section className={`rounded-xl bg-[#161b22] border border-[#21262d] overflow-hidden ${className}`}>
      {children}
    </section>
  );
}

export function PanelHeader({ icon: Icon, iconClassName = "text-[#7d8590]", title, children }: PanelHeaderProps) {
  return (
    <div className="flex items-center gap-2 px-4 py-3 border-b border-[#21262d]">
      {Icon && <Icon className={`w-4 h-4 ${iconClassName}`} />}
      <h2 className="text-[#e6edf3]" style={{ fontSize: "13px", fontWeight: 600 }}>{title}</h2>
      {children}
    </div>
  );
}
