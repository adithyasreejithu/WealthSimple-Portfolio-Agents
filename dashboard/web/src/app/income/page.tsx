export const dynamic = "force-dynamic";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { KpiCard } from "@/components/kpi-card";
import { DividendBars, type DividendMonth } from "@/components/charts/dividend-bars";
import { getReport } from "@/lib/api";
import { fmtCad, fmtPct } from "@/lib/format";

export default async function IncomePage() {
  const { report } = await getReport();
  const income = report.income;

  const months = Object.keys(income.by_month).sort();
  const series: DividendMonth[] = months.map((month, i) => {
    const window = months.slice(Math.max(0, i - 11), i + 1);
    const avg = window.reduce((s, m) => s + income.by_month[m], 0) / window.length;
    return {
      month,
      amount: income.by_month[month],
      rolling: i >= 11 ? avg : null,
    };
  });

  const ttm = income.trailing_12_month;
  const ttmParts = Object.entries(ttm.totals_by_currency)
    .map(([ccy, v]) => `${fmtCad(v)} ${ccy}`)
    .join(" · ");

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <KpiCard
          label="Trailing 12-mo income"
          value={fmtCad(Object.values(ttm.totals_by_currency).reduce((s, v) => s + v, 0))}
          sub={ttmParts}
          subClassName="text-muted-foreground"
        />
        <KpiCard
          label="Yield on value"
          value={ttm.yield_on_portfolio_value.available ? fmtPct(ttm.yield_on_portfolio_value.value) : "—"}
          sub="TTM income ÷ portfolio value"
          subClassName="text-muted-foreground"
        />
        <KpiCard
          label="Yield on cost"
          value={income.yield_on_cost.available ? fmtPct(income.yield_on_cost.value) : "—"}
          sub="TTM income ÷ book cost"
          subClassName="text-muted-foreground"
        />
        <KpiCard
          label="Dividend growth"
          value={income.dividend_growth.available ? fmtPct(income.dividend_growth.value) : "—"}
          sub={
            income.dividend_growth.available
              ? `${income.dividend_growth.from_year} → ${income.dividend_growth.to_year}`
              : income.dividend_growth.reason ?? undefined
          }
          subClassName="text-muted-foreground"
        />
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Dividend income by month</CardTitle>
        </CardHeader>
        <CardContent>
          <DividendBars data={series} />
          <p className="text-muted-foreground mt-2 text-xs">
            Monthly dividends received (source: {income.source}); the line is the trailing
            12-month average. Amounts are summed across currencies without FX normalization.
          </p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Total received by currency</CardTitle>
        </CardHeader>
        <CardContent>
          <ul className="space-y-1 text-sm">
            {Object.entries(income.totals_by_currency).map(([ccy, v]) => (
              <li key={ccy} className="flex justify-between">
                <span className="text-muted-foreground">{ccy}</span>
                <span className="tabular-nums">{fmtCad(v)}</span>
              </li>
            ))}
          </ul>
          <p className="text-muted-foreground mt-2 text-xs">
            {income.transaction_count} dividend transactions all-time.
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
