import Link from "next/link";

import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { fmtCad, fmtCcy, fmtCompact, fmtPct, signClass } from "@/lib/format";

export interface EtfRow {
  symbol: string;
  name: string;
  category: string | null;
  fundFamily: string | null;
  mer: number | null;
  aum: number | null;
  weight: number;
  marketValue: number;
  unrealized: number;
  currency: string;
}

/** ETF-specific table. Few rows, so a plain table (sorted by weight) with the
 *  symbol linking to the shared holding detail route. */
export function EtfTable({ rows }: { rows: EtfRow[] }) {
  return (
    <div className="overflow-x-auto rounded-lg border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Symbol</TableHead>
            <TableHead>Category</TableHead>
            <TableHead>Fund family</TableHead>
            <TableHead className="text-right">MER</TableHead>
            <TableHead className="text-right">AUM</TableHead>
            <TableHead className="text-right">Weight</TableHead>
            <TableHead className="text-right">Value</TableHead>
            <TableHead className="text-right">Unrealized</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((r) => (
            <TableRow key={r.symbol}>
              <TableCell>
                <Link href={`/holdings/${encodeURIComponent(r.symbol)}`} className="font-medium hover:underline">
                  {r.symbol}
                </Link>
                <div className="text-muted-foreground line-clamp-1 text-xs">{r.name}</div>
              </TableCell>
              <TableCell>{r.category ?? "—"}</TableCell>
              <TableCell>{r.fundFamily ?? "—"}</TableCell>
              <TableCell className="text-right tabular-nums">{r.mer != null ? fmtPct(r.mer) : "—"}</TableCell>
              <TableCell className="text-right tabular-nums">{r.aum != null ? fmtCompact(r.aum) : "—"}</TableCell>
              <TableCell className="text-right tabular-nums">{fmtPct(r.weight)}</TableCell>
              <TableCell className="text-right tabular-nums">{fmtCad(r.marketValue)}</TableCell>
              <TableCell className={`text-right tabular-nums ${signClass(r.unrealized)}`}>
                {fmtCcy(r.unrealized, r.currency)}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
