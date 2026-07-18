import { categoryColor, OTHER_COLOR } from "@/lib/derive";
import { fmtPct } from "@/lib/format";

interface SectorStackProps {
  symbol: string;
  weights: Record<string, number> | null | undefined;
}

/** One ETF's underlying sector mix as a single stacked bar. Weights may be
 *  fractions (0.23) or percents (23) depending on the source; both normalize. */
export function SectorStack({ symbol, weights }: SectorStackProps) {
  const entries = Object.entries(weights ?? {})
    .filter(([, v]) => v > 0)
    .sort((a, b) => b[1] - a[1]);

  if (entries.length === 0) {
    return (
      <div className="flex items-center justify-between text-sm">
        <span className="font-medium">{symbol}</span>
        <span className="text-muted-foreground text-xs">No sector breakdown</span>
      </div>
    );
  }

  const total = entries.reduce((s, [, v]) => s + v, 0) || 1;
  const top = entries.slice(0, 5);
  const restWeight = entries.slice(5).reduce((s, [, v]) => s + v, 0);

  const segments = top.map(([label, v], i) => ({
    label,
    pct: v / total,
    color: categoryColor(i),
  }));
  if (restWeight > 0) segments.push({ label: "Other", pct: restWeight / total, color: OTHER_COLOR });

  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between text-sm">
        <span className="font-medium">{symbol}</span>
      </div>
      <div className="flex h-3 overflow-hidden rounded-full">
        {segments.map((s) => (
          <span
            key={s.label}
            className="h-full"
            style={{ width: `${s.pct * 100}%`, background: s.color }}
            title={`${s.label}: ${fmtPct(s.pct)}`}
          />
        ))}
      </div>
      <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-xs">
        {segments.map((s) => (
          <span key={s.label} className="flex items-center gap-1">
            <span className="size-2 rounded-[2px]" style={{ background: s.color }} />
            <span className="text-muted-foreground">
              {s.label} {fmtPct(s.pct)}
            </span>
          </span>
        ))}
      </div>
    </div>
  );
}
