import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { fmtCad, fmtPct } from "@/lib/format";

interface DividendHoldingRow {
  ticker_id: number | null;
  totals_by_currency: Record<string, number>;
  trailing_12_month_by_currency: Record<string, number>;
  transaction_count: number;
}

function sum(map: Record<string, number>): number {
  return Object.values(map).reduce((s, v) => s + v, 0);
}

/**
 * Trailing-12-month dividend income by holding, ranked, largest first.
 * Currencies are summed per row for the ranking total -- the same
 * mixed-currency simplification the monthly bars already make and disclose.
 */
export function DividendByHoldingTable({ byTicker }: { byTicker: Record<string, DividendHoldingRow> }) {
  const totalTtm = Object.values(byTicker).reduce((s, r) => s + sum(r.trailing_12_month_by_currency), 0);
  const rows = Object.entries(byTicker)
    .map(([symbol, r]) => ({
      symbol,
      ttm: sum(r.trailing_12_month_by_currency),
      allTime: sum(r.totals_by_currency),
      transactionCount: r.transaction_count,
    }))
    .sort((a, b) => b.ttm - a.ttm);

  if (rows.length === 0) {
    return <p className="text-muted-foreground text-sm">No dividend income recorded.</p>;
  }

  return (
    <div className="max-h-96 overflow-y-auto">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Holding</TableHead>
            <TableHead className="text-right">Trailing 12mo</TableHead>
            <TableHead className="text-right">Share</TableHead>
            <TableHead className="text-right">All-time</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((r) => (
            <TableRow key={r.symbol}>
              <TableCell className="font-medium">{r.symbol}</TableCell>
              <TableCell className="text-right tabular-nums">{fmtCad(r.ttm)}</TableCell>
              <TableCell className="text-muted-foreground text-right tabular-nums">
                {totalTtm > 0 ? fmtPct(r.ttm / totalTtm) : "—"}
              </TableCell>
              <TableCell className="text-muted-foreground text-right tabular-nums">{fmtCad(r.allTime)}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
