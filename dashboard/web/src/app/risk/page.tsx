export const dynamic = "force-dynamic";

import { RiskExplorer } from "@/components/risk/risk-explorer";
import { getClassifications, getReport } from "@/lib/api";
import type { TickerOption } from "@/components/risk/ticker-picker";

export default async function RiskPage() {
  const [{ report }, classifications] = await Promise.all([
    getReport(),
    getClassifications().catch(() => null),
  ]);

  const groupByTicker = new Map<number, string>();
  classifications?.classifications.forEach((c) => groupByTicker.set(c.ticker_id, c.primary_group));

  const options: TickerOption[] = [...report.holdings]
    .sort((a, b) => b.weight - a.weight)
    .map((h) => ({ symbol: h.ticker_symbol, group: groupByTicker.get(h.ticker_id) ?? null }));

  return (
    <div className="space-y-4">
      <p className="text-muted-foreground text-sm">
        Reads security-level price history directly -- independent of the portfolio&apos;s daily
        valuation series, so it stays reliable while that series is under repair.
      </p>
      <RiskExplorer options={options} />
    </div>
  );
}
