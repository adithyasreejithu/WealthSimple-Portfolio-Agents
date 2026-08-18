export const dynamic = "force-dynamic";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { CalendarDays } from "lucide-react";
import { KpiCard } from "@/components/kpi-card";
import { DividendBars, type DividendMonth, type DividendPayer } from "@/components/charts/dividend-bars";
import { DividendByHoldingTable } from "@/components/tables/dividend-by-holding-table";
import { getReport, getUpcomingIncome } from "@/lib/api";
import { categoryColor, OTHER_COLOR } from "@/lib/derive";
import { fmtCad, fmtCcy, fmtDate, fmtDateTime, fmtPct } from "@/lib/format";

const TOP_PAYERS = 6;
const UPCOMING_HORIZON_DAYS = 90;

export default async function IncomePage() {
  const [{ report }, upcoming] = await Promise.all([
    getReport(),
    getUpcomingIncome(UPCOMING_HORIZON_DAYS).catch(() => null),
  ]);
  const income = report.income;

  const months = Object.keys(income.by_month).sort();

  // Top payers by trailing-12-month income get their own stacked-bar colour;
  // the rest fold into "Other" so the chart never runs out of palette slots,
  // mirroring topWeights' donut convention in lib/derive.ts.
  const rankedTickers = Object.entries(income.by_ticker)
    .map(([symbol, r]) => [symbol, Object.values(r.trailing_12_month_by_currency).reduce((s, v) => s + v, 0)] as const)
    .sort((a, b) => b[1] - a[1])
    .map(([symbol]) => symbol);
  const topTickers = rankedTickers.slice(0, TOP_PAYERS);
  const payers: DividendPayer[] = topTickers.map((symbol, i) => ({
    key: symbol,
    label: symbol,
    color: categoryColor(i),
  }));
  const hasOther = rankedTickers.length > TOP_PAYERS;
  if (hasOther) payers.push({ key: "Other", label: "Other", color: OTHER_COLOR });

  const series: DividendMonth[] = months.map((month, i) => {
    const window = months.slice(Math.max(0, i - 11), i + 1);
    const avg = window.reduce((s, m) => s + income.by_month[m], 0) / window.length;
    const byTicker = income.by_ticker_month[month] ?? {};
    const row: DividendMonth = { month, amount: income.by_month[month], rolling: i >= 11 ? avg : null };
    let other = 0;
    for (const [symbol, amount] of Object.entries(byTicker)) {
      if (topTickers.includes(symbol)) row[symbol] = amount;
      else other += amount;
    }
    if (hasOther) row.Other = other;
    return row;
  });

  const ttm = income.trailing_12_month;
  const ttmParts = Object.entries(ttm.totals_by_currency)
    .map(([ccy, v]) => `${fmtCad(v)} ${ccy}`)
    .join(" · ");

  const nextDividend = upcoming?.dividends[0] ?? null;
  const nextEarnings = upcoming?.earnings[0] ?? null;
  const expectedValue =
    upcoming && upcoming.dividends.length
      ? Object.entries(upcoming.totals_by_currency)
          .map(([ccy, v]) => `${fmtCad(v)} ${ccy}`)
          .join(" · ")
      : "—";

  return (
    <div className="space-y-4">
      <Card className="gap-3 border-sky-500/25 bg-sky-500/5 py-3 dark:bg-sky-400/5">
        <CardHeader className="flex-row items-center gap-2 px-4">
          <CalendarDays className="size-4 text-sky-600 dark:text-sky-400" />
          <CardTitle className="text-sm">Income calendar · next {UPCOMING_HORIZON_DAYS} days</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2 px-4">
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <CalendarMetric label="Next dividend" value={nextDividend ? nextDividend.ticker_symbol : "—"} detail={nextDividend ? `${fmtCcy(nextDividend.expected_cash, nextDividend.currency)} · ${fmtDate(nextDividend.ex_dividend_date)}` : upcoming ? "None scheduled" : "Unavailable"} />
            <CalendarMetric label="Expected income" value={expectedValue} detail={upcoming ? `${upcoming.dividends.length} dividend(s) scheduled` : "Unavailable"} />
            <CalendarMetric label="Next earnings" value={nextEarnings ? nextEarnings.ticker_symbol : "—"} detail={nextEarnings ? fmtDate(nextEarnings.report_date) : upcoming ? "None scheduled" : "Unavailable"} />
            <CalendarMetric label="Earnings in window" value={upcoming ? String(upcoming.earnings.length) : "—"} detail={`Next ${UPCOMING_HORIZON_DAYS} days`} />
          </div>
          <UpcomingFreshnessNote upcoming={upcoming} />
        </CardContent>
      </Card>

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
          <DividendBars data={series} payers={payers.length ? payers : undefined} />
          {payers.length ? (
            <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1" aria-label="Dividend payer legend">
              {payers.map((payer) => <span key={payer.key} className="text-muted-foreground flex items-center gap-1.5 text-xs"><span className="size-2 rounded-sm" style={{ background: payer.color }} />{payer.label}</span>)}
              <span className="text-muted-foreground flex items-center gap-1.5 text-xs"><span className="h-0.5 w-3 bg-[var(--chart-1)]" />12-mo avg</span>
            </div>
          ) : null}
          <p className="text-muted-foreground mt-2 text-xs">
            Monthly dividends received (source: {income.source}), stacked by payer{hasOther ? ` (top ${TOP_PAYERS} shown individually)` : ""};
            the line is the trailing 12-month average. Amounts are summed across currencies without FX normalization.
          </p>
        </CardContent>
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Dividends by holding</CardTitle>
          </CardHeader>
          <CardContent>
            <DividendByHoldingTable byTicker={income.by_ticker} />
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
    </div>
  );
}

function CalendarMetric({ label, value, detail }: { label: string; value: string; detail: string }) {
  return <div className="min-w-0"><p className="text-muted-foreground text-xs">{label}</p><p className="truncate text-base font-semibold tabular-nums">{value}</p><p className="text-muted-foreground truncate text-xs">{detail}</p></div>;
}

function UpcomingFreshnessNote({ upcoming }: { upcoming: Awaited<ReturnType<typeof getUpcomingIncome>> | null }) {
  if (!upcoming) {
    return (
      <p className="text-muted-foreground text-xs">
        Upcoming income data unavailable -- the dashboard API may be unreachable.
      </p>
    );
  }
  const { dividends, earnings } = upcoming.synced_at;
  if (!dividends && !earnings) {
    return (
      <p className="text-muted-foreground text-xs">
        No upcoming dividend/earnings sync has run yet. Run{" "}
        <code className="bg-muted rounded px-1 py-0.5">python src/app.py earnings-dividends-sync</code> to populate this.
      </p>
    );
  }
  return (
    <p className="text-muted-foreground text-xs">
      Upcoming dividends as of {fmtDateTime(dividends)} · Upcoming earnings as of {fmtDateTime(earnings)} — synced on
      demand, not part of the automatic refresh.
    </p>
  );
}
