export default function Metric({ label, value, accent }: { label: string; value: string | number; accent?: string }) {
  return (
    <div className="min-w-0">
      <div className="text-[11px] uppercase tracking-normal text-slate-500">{label}</div>
      <div className={`truncate text-sm font-semibold ${accent ?? "text-slate-100"}`}>{value}</div>
    </div>
  );
}
