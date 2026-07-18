import { fmtPct } from "@/lib/format";

export interface WeightBar {
  label: string;
  weight: number; // fraction
  color?: string;
}

/**
 * A compact list of labelled horizontal bars — better than a cramped chart for
 * many categories (e.g. 19 look-through sectors) and naturally phone-friendly.
 */
export function WeightBars({ data, max }: { data: WeightBar[]; max?: number }) {
  const ceiling = max ?? Math.max(...data.map((d) => d.weight), 0.0001);
  return (
    <ul className="space-y-2">
      {data.map((d) => (
        <li key={d.label} className="grid grid-cols-[8rem_1fr_3rem] items-center gap-2 text-sm">
          <span className="truncate capitalize" title={d.label}>
            {d.label}
          </span>
          <span className="h-2 rounded-full bg-muted">
            <span
              className="block h-full rounded-full"
              style={{
                width: `${Math.min(100, (d.weight / ceiling) * 100)}%`,
                background: d.color ?? "var(--chart-1)",
              }}
            />
          </span>
          <span className="text-muted-foreground text-right tabular-nums">{fmtPct(d.weight)}</span>
        </li>
      ))}
    </ul>
  );
}
