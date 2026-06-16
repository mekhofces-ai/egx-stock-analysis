import type { ReactNode } from "react";

export default function FilterToolbar({ children }: { children: ReactNode }) {
  return <div className="sticky top-[86px] z-10 mb-3 flex flex-wrap gap-2 rounded-lg border border-terminal-border bg-[#0D1520]/95 p-3 backdrop-blur">{children}</div>;
}

export function FilterInput(props: React.InputHTMLAttributes<HTMLInputElement>) {
  return <input {...props} className="h-9 min-w-[180px] rounded border border-terminal-border bg-terminal-card px-3 text-sm text-slate-100 outline-none focus:border-teal-400" />;
}

export function FilterSelect(props: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return <select {...props} className="h-9 rounded border border-terminal-border bg-terminal-card px-3 text-sm text-slate-100 outline-none focus:border-teal-400" />;
}
