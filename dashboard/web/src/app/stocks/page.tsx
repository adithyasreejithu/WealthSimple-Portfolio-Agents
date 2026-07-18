export const dynamic = "force-dynamic";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { HoldingsTable } from "@/components/tables/holdings-table";
import { PriceExplorer } from "@/components/stocks/price-explorer";
import { getClassifications, getReport } from "@/lib/api";
import { classificationsById } from "@/lib/derive";
import { buildHoldingRows } from "@/lib/holdings";

export default async function StocksPage() {
  const [{ report }, classifications] = await Promise.all([
    getReport(),
    getClassifications().catch(() => null),
  ]);

  const byTicker = classificationsById(classifications?.classifications ?? []);
  const stocks = report.holdings.filter((h) => h.security_type === "stock");
  const rows = buildHoldingRows(stocks, byTicker).sort((a, b) => b.weight - a.weight);
  const symbols = stocks.map((h) => ({ symbol: h.ticker_symbol, name: h.security_name }));

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle>Price explorer</CardTitle>
        </CardHeader>
        <CardContent>
          {symbols.length ? (
            <PriceExplorer initialSymbol={symbols[0].symbol} symbols={symbols} />
          ) : (
            <p className="text-muted-foreground text-sm">No stock holdings.</p>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Stock holdings</CardTitle>
        </CardHeader>
        <CardContent>
          {rows.length ? (
            <HoldingsTable rows={rows} />
          ) : (
            <p className="text-muted-foreground text-sm">No stock holdings.</p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}