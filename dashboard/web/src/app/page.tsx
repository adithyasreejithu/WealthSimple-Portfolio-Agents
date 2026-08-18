export const dynamic = "force-dynamic";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { KpiCard } from "@/components/kpi-card";
import { AllocationDonut } from "@/components/charts/allocation-donut";
import { TrendArea } from "@/components/charts/trend-area";
import { GroupCard, type GroupCardData } from "@/components/overview/group-card";
import { Notifications, type AttentionItem } from "@/components/notifications";
import { getClassifications, getReport } from "@/lib/api";
import { sectorPins, sortByGroupOrder, topWeights } from "@/lib/derive";
import { fmtCad, fmtSignedPct, signClass } from "@/lib/format";

export default async function OverviewPage() {
  const [{ report }, classifications] = await Promise.all([
    getReport(),
    getClassifications().catch(() => null),
  ]);
  const { summary, allocation, performance, targets } = report;

  // Per-group top holding: join holdings to their classified group.
  const groupByTicker = new Map<number, string>();
  classifications?.classifications.forEach((c) => groupByTicker.set(c.ticker_id, c.primary_group));
  const topHoldingByGroup = new Map<string, { symbol: string; value: number }>();
  for (const h of report.holdings) {
    const group = groupByTicker.get(h.ticker_id);
    if (!group) continue;
    const current = topHoldingByGroup.get(group);
    if (!current || h.market_value > current.value) {
      topHoldingByGroup.set(group, { symbol: h.ticker_symbol, value: h.market_value });
    }
  }

  const targetByGroup = new Map(targets?.groups.map((g) => [g.group, g]) ?? []);
  const groupCards: GroupCardData[] = sortByGroupOrder(
    Object.entries(allocation.by_group).map(([group, v]) => {
      const t = targetByGroup.get(group);
      return {
        group,
        market_value: v.market_value,
        weight: v.weight,
        targetPercent: t?.target_percent ?? null,
        driftPp: t?.drift_pp ?? null,
        rebalanceNeeded: t?.rebalance_needed ?? false,
        topHolding: topHoldingByGroup.get(group)?.symbol ?? null,
      };
    }),
    (card) => card.group,
  );

  // Pin sector hues so this donut matches the per-ETF stacks on /etfs.
  const sectorSlices = topWeights(
    allocation.by_sector,
    7,
    sectorPins(Object.keys(allocation.by_sector ?? {})),
  );
  const unrealized = summary.unrealized_gain;

  // Things the pipeline flagged but cannot resolve on its own. Counts come
  // straight from the API; the toast just points at /data-quality where the
  // matching action lives.
  const reviewCount = classifications?.review_count ?? 0;
  const flagCounts = report.data_quality.counts_by_code ?? {};
  const attention: AttentionItem[] = [];
  if (reviewCount > 0) {
    attention.push({
      code: "review_needed",
      message: `${reviewCount} holding${reviewCount === 1 ? "" : "s"} need classification`,
      severity: "warning",
    });
  }
  if (flagCounts.stale_price) {
    attention.push({
      code: "stale_price",
      message: `${flagCounts.stale_price} holding${flagCounts.stale_price === 1 ? "" : "s"} have stale prices`,
      severity: "warning",
    });
  }
  for (const code of ["missing_sector", "missing_cost_basis"] as const) {
    if (flagCounts[code]) {
      attention.push({
        code,
        message: `${flagCounts[code]} holding${flagCounts[code] === 1 ? "" : "s"} missing ${
          code === "missing_sector" ? "sector data" : "cost basis"
        }`,
        severity: "info",
      });
    }
  }

  return (
    <div className="space-y-4">
      <Notifications items={attention} />
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <KpiCard label="Portfolio Value" value={fmtCad(summary.portfolio_value)} />
        <KpiCard label="Book Cost" value={fmtCad(summary.book_cost)} />
        <KpiCard
          label="Unrealized Gain"
          value={<span className={signClass(unrealized.amount)}>{fmtCad(unrealized.amount)}</span>}
          sub={fmtSignedPct(unrealized.percent)}
          subClassName={signClass(unrealized.amount)}
        />
        <KpiCard
          label="Cash Balance"
          value={fmtCad(summary.cash.balance)}
          sub={
            summary.cash.provisional_adjustment !== 0
              ? `${summary.cash.source.replace(/_/g, " ")} · incl. ${fmtCad(summary.cash.provisional_adjustment)} from unreconciled email trades`
              : summary.cash.source.replace(/_/g, " ")
          }
          subClassName="text-muted-foreground"
        />
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle>Portfolio value over time</CardTitle>
          </CardHeader>
          <CardContent>
            <TrendArea
              data={performance.historical_values}
              overlays={performance.trend_overlays ?? null}
            />
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Sector breakdown</CardTitle>
          </CardHeader>
          <CardContent>
            <AllocationDonut data={sectorSlices} />
          </CardContent>
        </Card>
      </div>

      <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-5">
        {groupCards.map((g) => (
          <GroupCard key={g.group} data={g} />
        ))}
      </div>
    </div>
  );
}