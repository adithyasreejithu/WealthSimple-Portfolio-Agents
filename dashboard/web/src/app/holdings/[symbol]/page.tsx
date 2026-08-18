export const dynamic = "force-dynamic";

import Link from "next/link";
import { notFound } from "next/navigation";

import { Badge } from "@/components/ui/badge";
import { Breadcrumb, BreadcrumbItem, BreadcrumbLink, BreadcrumbList, BreadcrumbPage, BreadcrumbSeparator } from "@/components/ui/breadcrumb";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { EtfExposure, type ExposureRow } from "@/components/etfs/etf-exposure";
import { KpiCard } from "@/components/kpi-card";
import { PriceExplorer } from "@/components/stocks/price-explorer";
import { getClassifications, getReport } from "@/lib/api";
import { fmtCad, fmtCcy, fmtCompact, fmtDate, fmtNumber, fmtPct, fmtSignedPct, signClass } from "@/lib/format";
import type { ClassificationDetail, ClassificationFields } from "@/lib/types";

export default async function HoldingDetailPage({ params }: { params: Promise<{ symbol: string }> }) {
  const { symbol: raw } = await params;
  const symbol = decodeURIComponent(raw).toUpperCase();
  const [{ report }, classifications] = await Promise.all([getReport(), getClassifications().catch(() => null)]);
  const holding = report.holdings.find((h) => h.ticker_symbol.toUpperCase() === symbol);
  if (!holding) notFound();

  const classification = classifications?.classifications.find((c) => c.ticker_symbol.toUpperCase() === symbol);
  const fields = classification?.fields ?? {};
  const isEtf = holding.security_type === "etf";
  const pnlPct = holding.cost_basis_mkt > 0 ? holding.unrealized_mkt / holding.cost_basis_mkt : null;
  const actualWeightPct = holding.weight * 100;

  return (
    <div className="space-y-4">
      <Breadcrumb><BreadcrumbList><BreadcrumbItem><BreadcrumbLink render={<Link href={isEtf ? "/etfs" : "/stocks"} />}>{isEtf ? "ETFs" : "Stocks"}</BreadcrumbLink></BreadcrumbItem><BreadcrumbSeparator /><BreadcrumbItem><BreadcrumbPage>{holding.ticker_symbol}</BreadcrumbPage></BreadcrumbItem></BreadcrumbList></Breadcrumb>

      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-xl font-semibold">{holding.security_name}</h2>
        <span className="text-muted-foreground">{holding.exchange}</span>
        <Badge variant="outline" className="uppercase">{holding.security_type}</Badge>
        <Badge variant="outline">{holding.currency}</Badge>
        {!isEtf && classification ? <Badge variant="secondary">{classification.primary_group}</Badge> : null}
        {!isEtf && classification?.confidence ? <Badge variant="outline" className="capitalize">{classification.confidence} confidence</Badge> : null}
        {holding.has_provisional_activity ? <Badge variant="outline" className="text-amber-600">Provisional activity</Badge> : null}
      </div>

      <div className="grid grid-cols-2 gap-4 md:grid-cols-3 lg:grid-cols-6">
        <KpiCard label="Quantity" value={fmtNumber(holding.quantity, 4)} />
        <KpiCard label="Last price" value={fmtCcy(holding.last_price, holding.currency)} sub={fmtDate(holding.last_price_date)} subClassName="text-muted-foreground" />
        <KpiCard label="Market value" value={fmtCad(holding.market_value)} sub={fmtCcy(holding.market_value_mkt, holding.currency)} subClassName="text-muted-foreground" />
        <KpiCard label="Unrealized" value={<span className={signClass(holding.unrealized_mkt)}>{fmtCcy(holding.unrealized_mkt, holding.currency)}</span>} sub={pnlPct === null ? undefined : fmtSignedPct(pnlPct)} subClassName={signClass(holding.unrealized_mkt)} />
        <KpiCard label="Realized (CAD)" value={<span className={signClass(holding.realized_gain_cad)}>{fmtCad(holding.realized_gain_cad)}</span>} />
        <KpiCard label="Weight" value={fmtPct(holding.weight)} />
      </div>

      <Card><CardHeader><CardTitle>Price</CardTitle></CardHeader><CardContent><PriceExplorer key={holding.ticker_symbol} initialSymbol={holding.ticker_symbol} /></CardContent></Card>

      {isEtf ? (
        <>
          <Card><CardHeader><CardTitle>ETF exposure</CardTitle></CardHeader><CardContent><EtfExposure sectors={sectorRows(fields.sector_weights)} companies={companyRows(fields.top_holdings)} /></CardContent></Card>
          <Card className="gap-2 py-4">
            <CardHeader className="px-4"><CardTitle className="text-sm">Facts</CardTitle></CardHeader>
            <CardContent className="grid gap-x-8 px-4 text-xs sm:grid-cols-2 lg:grid-cols-4">
              <FactRow label="Category" value={strOrDash(fields.etf_category)} />
              <FactRow label="Fund family" value={strOrDash(fields.fund_family)} />
              <FactRow label="Expense ratio (MER)" value={fields.expense_ratio != null ? fmtPct(Number(fields.expense_ratio)) : "—"} />
              <FactRow label="AUM" value={fields.aum != null ? fmtCompact(Number(fields.aum)) : "—"} />
              <FactRow label="Account" value={strOrDash(fields.account_type)} />
              <FactRow label="First purchase" value={fields.first_purchase_date ? fmtDate(String(fields.first_purchase_date)) : "—"} />
              <FactRow label="Buys / sells" value={`${numOrDash(fields.number_of_buys)} / ${numOrDash(fields.number_of_sells)}`} />
              <FactRow label="Dividends received" value={fields.dividends_received != null ? fmtCad(Number(fields.dividends_received)) : "—"} />
            </CardContent>
          </Card>
          <Card><CardHeader><CardTitle>Classification</CardTitle></CardHeader><CardContent><ClassificationContent classification={classification} fields={fields} actualWeightPct={actualWeightPct} /></CardContent></Card>
        </>
      ) : (
        <div className="grid gap-4 lg:grid-cols-2">
          <Card><CardHeader><CardTitle>Classification</CardTitle></CardHeader><CardContent><ClassificationContent classification={classification} fields={fields} actualWeightPct={actualWeightPct} /></CardContent></Card>
          <Card><CardHeader><CardTitle>Facts</CardTitle></CardHeader><CardContent className="space-y-1 text-sm">
            <FactRow label="Sector" value={strOrDash(fields.sector)} />
            <FactRow label="Industry" value={strOrDash(fields.industry)} />
            <FactRow label="Market cap" value={fields.market_cap != null ? fmtCompact(Number(fields.market_cap)) : "—"} />
            <FactRow label="Dividend yield" value={fields.dividend_yield != null ? fmtPct(Number(fields.dividend_yield)) : "—"} />
            <FactRow label="Account" value={strOrDash(fields.account_type)} />
            <FactRow label="First purchase" value={fields.first_purchase_date ? fmtDate(String(fields.first_purchase_date)) : "—"} />
            <FactRow label="Buys / sells" value={`${numOrDash(fields.number_of_buys)} / ${numOrDash(fields.number_of_sells)}`} />
            <FactRow label="Dividends received" value={fields.dividends_received != null ? fmtCad(Number(fields.dividends_received)) : "—"} />
          </CardContent></Card>
        </div>
      )}

      {holding.data_quality_flags.length ? <p className="text-muted-foreground text-xs">Data quality flags: {holding.data_quality_flags.join(", ")}</p> : null}
    </div>
  );
}

function ClassificationContent({ classification, fields, actualWeightPct }: { classification: ClassificationDetail | undefined; fields: ClassificationFields; actualWeightPct: number }) {
  if (!classification) return <p className="text-muted-foreground text-sm">No classification on record for this holding.</p>;
  const targetWeight = fields.target_weight_percent;
  return (
    <div className="space-y-3 text-sm">
      <div className="flex flex-wrap gap-1">
        <Badge variant="secondary">{classification.primary_group}</Badge>
        {classification.confidence ? <Badge variant="outline" className="font-normal capitalize">{classification.confidence} confidence</Badge> : null}
        {classification.secondary_tags.map((tag) => <Badge key={tag} variant="outline" className="font-normal">{tag}</Badge>)}
      </div>
      {classification.reasoning ? <p className="text-muted-foreground">{classification.reasoning}</p> : null}
      <FactRow label="Your thesis" value={fields.user_thesis ? String(fields.user_thesis) : "None recorded"} />
      <FactRow label="Target vs actual weight" value={targetWeight != null ? `${fmtNumber(actualWeightPct)}% vs ${fmtNumber(Number(targetWeight))}% target` : `${fmtNumber(actualWeightPct)}% (no target)`} />
      {classification.review_needed ? <Badge variant="destructive" className="font-normal">Needs review</Badge> : null}
    </div>
  );
}

function FactRow({ label, value }: { label: string; value: string }) { return <div className="flex justify-between gap-4 py-0.5"><span className="text-muted-foreground">{label}</span><span className="text-right">{value}</span></div>; }
function strOrDash(value: unknown): string { return value === null || value === undefined || value === "" ? "—" : String(value); }
function numOrDash(value: unknown): string { return value === null || value === undefined ? "—" : String(value); }

function sectorRows(raw: unknown): ExposureRow[] {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return [];
  return Object.entries(raw as Record<string, unknown>).map(([label, weight]) => ({ label, weight: Number(weight) })).filter((row) => row.label && Number.isFinite(row.weight) && row.weight > 0).sort((a, b) => b.weight - a.weight);
}

function companyRows(raw: unknown): ExposureRow[] {
  const records = Array.isArray(raw) ? raw : raw && typeof raw === "object" ? Object.values(raw as Record<string, unknown>) : [];
  return records.filter((record): record is Record<string, unknown> => Boolean(record) && typeof record === "object" && !Array.isArray(record)).map((record) => ({ label: String(record.Name ?? record.name ?? record.Symbol ?? ""), weight: Number(record["Holding Percent"] ?? record.holding_percent) })).filter((row) => row.label && Number.isFinite(row.weight) && row.weight > 0).sort((a, b) => b.weight - a.weight);
}
