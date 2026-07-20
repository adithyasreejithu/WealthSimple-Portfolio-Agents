export const dynamic = "force-dynamic";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { KpiCard } from "@/components/kpi-card";
import { EtfTable, type EtfRow } from "@/components/etfs/etf-table";
import { SectorStack } from "@/components/etfs/sector-stack";
import { OverlapCard } from "@/components/etfs/overlap-card";
import { getClassifications, getEtfOverlap, getReport } from "@/lib/api";
import { classificationsById, blendedMer } from "@/lib/derive";
import { fmtCad, fmtPct } from "@/lib/format";

function num(v: unknown): number | null {
  return v === null || v === undefined || Number.isNaN(Number(v)) ? null : Number(v);
}

export default async function EtfsPage() {
  const [{ report }, classifications, overlap] = await Promise.all([
    getReport(),
    getClassifications().catch(() => null),
    getEtfOverlap().catch(() => null),
  ]);

  const byTicker = classificationsById(classifications?.classifications ?? []);
  const etfs = report.holdings.filter((h) => h.security_type === "etf");

  const rows: EtfRow[] = etfs
    .map((h) => {
      const fields = byTicker.get(h.ticker_id)?.fields ?? {};
      return {
        symbol: h.ticker_symbol,
        name: h.security_name,
        category: (fields.etf_category as string) ?? null,
        fundFamily: (fields.fund_family as string) ?? null,
        mer: num(fields.expense_ratio),
        aum: num(fields.aum),
        weight: h.weight,
        marketValue: h.market_value,
        unrealized: h.unrealized_mkt,
        currency: h.currency,
      };
    })
    .sort((a, b) => b.weight - a.weight);

  const sleeveValue = etfs.reduce((s, h) => s + h.market_value, 0);
  const sleeveWeight = etfs.reduce((s, h) => s + h.weight, 0);
  const mer = blendedMer(rows.map((r) => ({ weight: r.weight, expense_ratio: r.mer })));
  const annualCost = mer.value !== null ? mer.value * sleeveValue : null;

  const sectorStacks = etfs
    .map((h) => ({
      symbol: h.ticker_symbol,
      weights: byTicker.get(h.ticker_id)?.fields?.sector_weights ?? null,
    }))
    .filter((e) => e.weights && Object.keys(e.weights).length > 0);

  if (etfs.length === 0) {
    return <p className="text-muted-foreground">No ETF holdings.</p>;
  }

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <KpiCard label="ETF sleeve value" value={fmtCad(sleeveValue)} sub={`${fmtPct(sleeveWeight)} of portfolio`} subClassName="text-muted-foreground" />
        <KpiCard
          label="Blended MER"
          value={mer.value !== null ? fmtPct(mer.value) : "—"}
          sub={mer.missingCount > 0 ? `${mer.missingCount} without a known ratio` : undefined}
          subClassName="text-muted-foreground"
        />
        <KpiCard
          label="Est. annual fund cost"
          value={annualCost !== null ? fmtCad(annualCost) : "—"}
          sub="MER × sleeve value"
          subClassName="text-muted-foreground"
        />
        <KpiCard label="ETF count" value={etfs.length} />
      </div>

      <Card>
        <CardHeader>
          <CardTitle>ETF holdings</CardTitle>
        </CardHeader>
        <CardContent>
          <EtfTable rows={rows} />
        </CardContent>
      </Card>

      {overlap ? <OverlapCard overlap={overlap} sleeveValue={sleeveValue} /> : null}

      {sectorStacks.length > 0 ? (
        <Card>
          <CardHeader>
            <CardTitle>Underlying sector mix</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            {sectorStacks.map((e) => (
              <SectorStack key={e.symbol} symbol={e.symbol} weights={e.weights} />
            ))}
          </CardContent>
        </Card>
      ) : null}
    </div>
  );
}
