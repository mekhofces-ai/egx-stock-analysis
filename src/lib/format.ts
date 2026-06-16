export const egyptFormatter = new Intl.DateTimeFormat("en-GB", {
  timeZone: "Africa/Cairo",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
});

export function egyptNow(): string {
  return egyptFormatter.format(new Date()).replace(",", "");
}

export function money(value: number): string {
  return value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

export function compact(value: number): string {
  return value.toLocaleString("en-US", { maximumFractionDigits: 0 });
}

export function pct(value: number): string {
  return `${value.toFixed(1)}%`;
}
