import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { fmtCad, signClass } from "@/lib/format";

/** Realized gains by ticker (CAD), largest magnitude first. */
export function RealizedTable({ byTicker }: { byTicker: Record<string, number> }) {
  const rows = Object.entries(byTicker).sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]));
  if (rows.length === 0) {
    return <p className="text-muted-foreground text-sm">No realized gains recorded.</p>;
  }
  return (
    <div className="max-h-72 overflow-y-auto">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Ticker</TableHead>
            <TableHead className="text-right">Realized (CAD)</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map(([ticker, value]) => (
            <TableRow key={ticker}>
              <TableCell className="font-medium">{ticker}</TableCell>
              <TableCell className={`text-right tabular-nums ${signClass(value)}`}>
                {fmtCad(value)}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
