import type { ElementType, ReactNode } from "react";

interface EmptyStateProps {
  title: string;
  description: string;
  icon: ElementType;
  action?: ReactNode;
}

export function EmptyState({ title, description, icon: Icon, action }: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center justify-center p-12 text-center border border-dashed border-[#30363d] rounded-xl bg-[#0d1117]/50">
      <div className="flex items-center justify-center w-12 h-12 mb-4 rounded-full bg-[#161b22] border border-[#21262d]">
        <Icon className="w-6 h-6 text-[#7d8590]" />
      </div>
      <h3 className="text-[#e6edf3]" style={{ fontSize: "16px", fontWeight: 600 }}>
        {title}
      </h3>
      <p className="mt-2 mb-6 text-[#7d8590] max-w-md" style={{ fontSize: "13px", lineHeight: 1.5 }}>
        {description}
      </p>
      {action && <div>{action}</div>}
    </div>
  );
}
