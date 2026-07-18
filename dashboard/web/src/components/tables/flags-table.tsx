import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import type { DataQualityFlag } from "@/lib/types";

const SEVERITY_VARIANT: Record<string, "default" | "secondary" | "destructive" | "outline"> = {
  warning: "destructive",
  error: "destructive",
  info: "secondary",
};

const SEVERITY_ORDER: Record<string, number> = { error: 0, warning: 1, info: 2 };

export function FlagsTable({ flags }: { flags: DataQualityFlag[] }) {
  if (flags.length === 0) {
    return <p className="text-muted-foreground text-sm">No data-quality flags. 🎉</p>;
  }
  const sorted = [...flags].sort(
    (a, b) => (SEVERITY_ORDER[a.severity] ?? 9) - (SEVERITY_ORDER[b.severity] ?? 9),
  );
  return (
    <div className="overflow-x-auto rounded-lg border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Severity</TableHead>
            <TableHead>Code</TableHead>
            <TableHead>Ticker</TableHead>
            <TableHead>Detail</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {sorted.map((f, i) => (
            <TableRow key={`${f.code}-${f.ticker_symbol}-${i}`}>
              <TableCell>
                <Badge variant={SEVERITY_VARIANT[f.severity] ?? "outline"} className="font-normal capitalize">
                  {f.severity}
                </Badge>
              </TableCell>
              <TableCell className="font-mono text-xs">{f.code}</TableCell>
              <TableCell className="font-medium">{f.ticker_symbol ?? "—"}</TableCell>
              <TableCell className="text-muted-foreground">{f.detail}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
