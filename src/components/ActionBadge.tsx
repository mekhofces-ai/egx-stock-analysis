import type { ActionNow } from "../types";
import { actionTone } from "../lib/selectors";

export default function ActionBadge({ action }: { action: ActionNow }) {
  const tone = actionTone(action);
  const classes = {
    buy: "border-emerald-400/40 bg-emerald-500/15 text-emerald-300",
    watch: "border-amber-400/40 bg-amber-500/15 text-amber-300",
    sell: "border-red-400/40 bg-red-500/15 text-red-300",
    neutral: "border-cyan-400/40 bg-cyan-500/15 text-cyan-300",
    wait: "border-slate-400/30 bg-slate-500/15 text-slate-300",
  }[tone];

  return <span className={`inline-flex whitespace-nowrap rounded px-2 py-1 text-[11px] font-semibold ${classes}`}>{action}</span>;
}
