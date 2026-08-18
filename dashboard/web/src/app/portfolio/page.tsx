export const dynamic = "force-dynamic";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { StatTile } from "@/components/stat-tile";
import { AllocationDonut } from "@/components/charts/allocation-donut";
import { WeightBars } from "@/components/charts/weight-bars";
import { HoldingsTreemap, type TreemapDatum } from "@/components/charts/holdings-treemap";
import { TargetVsActual } from "@/components/portfolio/target-vs-actual";
import { ConcentrationCard } from "@/components/portfolio/concentration-card";
import { RealizedTable } from "@/components/tables/realized-table";
import { getClassifications, getReport } from "@/lib/api";
import { canonicalSector, CURRENCY_COLORS, sectorColor, topWeights } from "@/lib/derive";
import { fmtCad, fmtDate, fmtNumber, fmtPct, fmtSignedPct } from "@/lib/format";

function pct(a: { available: boolean; reason?: string | null }, value: number, signed = false) {
  if (!a.available) return "—";
  return signed ? fmtSignedPct(value) : fmtPct(value);
}

export default async function PortfolioPage() {
  const [{ report }, classifications] = await Promise.all([
    getReport(),
    getClassifications().catch(() => null),
  ]);
  const { allocation, performance, targets, fees, activity, realized_gains } = report;
  const ret = performance.adjusted_returns;
  const bench = performance.benchmark;
  const mwr = performance.money_weighted;

  const groupByTicker = new Map<number, string>();
  classifications?.classifications.forEach((c) => groupByTicker.set(c.ticker_id, c.primary_group));
  const treemap: TreemapDatum[] = report.holdings.map((h) => ({
    name: h.ticker_symbol,
    size: h.market_value,
    weight: h.weight,
    group: groupByTicker.get(h.ticker_id) ?? "Other",
  }));

  // Same sector -> same hue as the /etfs stacks and the overview donut.
  const lookThrough = Object.entries(allocation.look_through_sector.weights ?? {})
    .map(([label, weight]) => ({
      label: canonicalSector(label),
      weight,
      color: sectorColor(label),
    }))
    .sort((a, b) => b.weight - a.weight)
    .slice(0, 12);

  return (
    <Tabs defaultValue="allocation" className="space-y-4">
      <TabsList>
        <TabsTrigger value="allocation">Allocation</TabsTrigger>
        <TabsTrigger value="performance">Performance</TabsTrigger>
        <TabsTrigger value="costs">Costs &amp; Activity</TabsTrigger>
      </TabsList>

      {/* ---- Allocation ---- */}
      <TabsContent value="allocation" className="space-y-4">
        <div className="grid gap-4 lg:grid-cols-2">
          <Card>
            <CardHeader>
              <CardTitle>Target vs Actual</CardTitle>
            </CardHeader>
            <CardContent>
              {targets?.groups?.length ? (
                <TargetVsActual groups={targets.groups} />
              ) : (
                <p className="text-muted-foreground text-sm">No allocation targets configured.</p>
              )}
            </CardContent>
          </Card>
          <ConcentrationCard concentration={allocation.concentration} />
        </div>

        <Card>
          <CardHeader>
            <CardTitle>Holdings by market value</CardTitle>
          </CardHeader>
          <CardContent>
            <HoldingsTreemap data={treemap} />
          </CardContent>
        </Card>

        <div className="grid gap-4 lg:grid-cols-3">
          <Card>
            <CardHeader>
              <CardTitle>By currency</CardTitle>
            </CardHeader>
            <CardContent>
              <AllocationDonut data={topWeights(allocation.by_currency, 5, CURRENCY_COLORS)} />
            </CardContent>
          </Card>
          <Card>
            <CardHeader>
              <CardTitle>By geography</CardTitle>
            </CardHeader>
            <CardContent>
              {allocation.by_geography ? (
                <AllocationDonut data={topWeights(allocation.by_geography, 5)} />
              ) : (
                <p className="text-muted-foreground text-sm">Geography tags unavailable.</p>
              )}
            </CardContent>
          </Card>
          <Card>
            <CardHeader>
              <CardTitle>Look-through sectors</CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-muted-foreground mb-3 text-xs">
                ETF holdings unrolled to underlying sectors ·{" "}
                {fmtPct(allocation.look_through_sector.coverage_percent)} coverage
              </p>
              <WeightBars data={lookThrough} />
            </CardContent>
          </Card>
        </div>
      </TabsContent>

      {/* ---- Performance ---- */}
      <TabsContent value="performance" className="space-y-4">
        <div className="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-6">
          <StatTile
            label="Total return"
            value={pct(ret.total_return, ret.total_return.total_return, true)}
            hint="Time-weighted total return over the full history, removing the effect of deposits and withdrawals."
          />
          <StatTile
            label="Annualized"
            value={pct(ret.total_return, ret.total_return.annualized_return, true)}
            hint={`Time-weighted return annualized over ${ret.total_return.span_days ?? 0} days.`}
          />
          <StatTile
            label="Volatility"
            value={pct(ret.volatility, ret.volatility.volatility)}
            hint="Annualized standard deviation of daily returns."
          />
          <StatTile
            label="Sharpe"
            value={ret.sharpe_ratio.available ? fmtNumber(ret.sharpe_ratio.sharpe_ratio) : "—"}
            hint="Excess return per unit of total volatility. Higher is better."
          />
          <StatTile
            label="Sortino"
            value={ret.sortino_ratio.available ? fmtNumber(ret.sortino_ratio.sortino_ratio) : "—"}
            hint="Like Sharpe but penalizes only downside volatility."
          />
          <StatTile
            label="Money-weighted (XIRR)"
            value={pct(mwr.money_weighted_return, mwr.money_weighted_return.xirr, true)}
            hint="Internal rate of return accounting for the size and timing of your contributions."
          />
        </div>

        <div className="grid gap-4 lg:grid-cols-2">
          <Card>
            <CardHeader>
              <CardTitle>Drawdown</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="grid grid-cols-2 gap-3">
                <StatTile
                  label="Max drawdown"
                  value={pct(ret.drawdown, ret.drawdown.max_drawdown)}
                  valueClassName="text-loss"
                />
                <StatTile
                  label="Current drawdown"
                  value={pct(ret.drawdown, ret.drawdown.current_drawdown)}
                  valueClassName={ret.drawdown.current_drawdown < 0 ? "text-loss" : ""}
                />
              </div>
              <dl className="text-sm">
                <Row label="Peak" value={fmtDate(ret.drawdown.peak_date)} />
                <Row label="Trough" value={fmtDate(ret.drawdown.trough_date)} />
                <Row
                  label="Recovered"
                  value={ret.drawdown.recovery_date ? fmtDate(ret.drawdown.recovery_date) : "Not yet"}
                />
              </dl>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex-row items-center justify-between">
              <CardTitle>vs benchmark</CardTitle>
              <span className="text-muted-foreground text-sm">{bench.symbol}</span>
            </CardHeader>
            <CardContent>
              {bench.available ? (
                <div className="grid grid-cols-2 gap-3">
                  <StatTile label="Alpha" value={fmtSignedPct(bench.alpha)} hint="Return above what the benchmark's risk exposure would predict." />
                  <StatTile label="Beta" value={fmtNumber(bench.beta)} hint="Sensitivity to benchmark moves (1.0 = moves in line)." />
                  <StatTile label="Information ratio" value={fmtNumber(bench.information_ratio)} hint="Active return per unit of tracking error." />
                  <StatTile label="Active return" value={fmtSignedPct(bench.active_return)} hint="Return in excess of the benchmark." />
                  <StatTile label="Tracking error" value={fmtPct(bench.tracking_error)} hint="Volatility of the return difference vs the benchmark." />
                </div>
              ) : (
                <p className="text-muted-foreground text-sm">Benchmark comparison unavailable.</p>
              )}
            </CardContent>
          </Card>
        </div>
      </TabsContent>

      {/* ---- Costs & Activity ---- */}
      <TabsContent value="costs" className="space-y-4">
        <div className="grid gap-4 lg:grid-cols-3">
          <Card>
            <CardHeader>
              <CardTitle>Fees</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <dl className="text-sm">
                <Row label="Commissions" value={fmtCad(sumValues(fees.commissions.totals_by_currency))} />
                <Row
                  label="Est. FX fees"
                  value={fees.fx.available ? fmtCad(fees.fx.estimated_fx_fee_cad) : "—"}
                />
                <Row
                  label="FX fee rate"
                  value={fees.fx.available ? fmtPct(fees.fx.fee_rate) : "—"}
                />
                <Row
                  label="FX transactions"
                  value={fees.fx.available ? `${fees.fx.buy_count} buys · ${fees.fx.sell_count} sells` : "—"}
                />
              </dl>
              <div className="grid grid-cols-2 gap-3 border-t pt-3">
                <StatTile
                  label="Fee drag (value)"
                  value={fees.fee_drag.fx_fee_percent_of_value.available ? fmtPct(fees.fee_drag.fx_fee_percent_of_value.value) : "—"}
                  hint="Estimated FX fees as a share of current portfolio value."
                />
                <StatTile
                  label="Fee drag (gains)"
                  value={fees.fee_drag.fx_fee_percent_of_gains.available ? fmtPct(fees.fee_drag.fx_fee_percent_of_gains.value) : "—"}
                  hint="Estimated FX fees as a share of total gains."
                />
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Activity</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <StatTile
                label="Turnover ratio"
                value={activity.turnover.available ? fmtPct(activity.turnover.turnover_ratio) : "—"}
                hint={activity.turnover.method}
              />
              <div className="grid grid-cols-2 gap-3">
                <StatTile
                  label="Avg hold (open)"
                  value={
                    activity.average_holding_period.open.available
                      ? `${Math.round(activity.average_holding_period.open.days ?? 0)}d`
                      : "—"
                  }
                />
                <StatTile
                  label="Avg hold (closed)"
                  value={
                    activity.average_holding_period.closed.available
                      ? `${Math.round(activity.average_holding_period.closed.days ?? 0)}d`
                      : "—"
                  }
                />
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex-row items-center justify-between">
              <CardTitle>Realized gains</CardTitle>
              <span className="tabular-nums text-sm font-medium">
                {fmtCad(realized_gains.total_realized_gain)}
              </span>
            </CardHeader>
            <CardContent>
              <RealizedTable events={realized_gains.events} />
            </CardContent>
          </Card>
        </div>
      </TabsContent>
    </Tabs>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between py-0.5">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="tabular-nums">{value}</dd>
    </div>
  );
}

function sumValues(map: Record<string, number>): number {
  return Object.values(map).reduce((s, v) => s + v, 0);
}
