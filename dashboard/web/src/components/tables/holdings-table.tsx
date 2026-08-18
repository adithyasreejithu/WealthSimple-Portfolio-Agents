"use client";

import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import {
  type ColumnDef,
  type SortingState,
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  useReactTable,
} from "@tanstack/react-table";
import { ArrowDown, ArrowUp, ChevronsUpDown, TriangleAlert } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { fmtCad, fmtCcy, fmtPct, fmtSignedPct, signClass } from "@/lib/format";
import type { HoldingRow } from "@/lib/holdings";

function sortHeader(label: string, align: "left" | "right" = "left") {
  const Header = ({ column }: { column: import("@tanstack/react-table").Column<HoldingRow> }) => (
    <Button
      variant="ghost"
      size="sm"
      className={`-mx-2 h-7 ${align === "right" ? "ml-auto" : ""}`}
      onClick={() => column.toggleSorting(column.getIsSorted() === "asc")}
    >
      {label}
      {column.getIsSorted() === "asc" ? (
        <ArrowUp className="size-3.5" />
      ) : column.getIsSorted() === "desc" ? (
        <ArrowDown className="size-3.5" />
      ) : (
        <ChevronsUpDown className="size-3.5 opacity-50" />
      )}
    </Button>
  );
  Header.displayName = `SortHeader(${label})`;
  return Header;
}

const CONFIDENCE_VARIANT: Record<string, "default" | "secondary" | "outline"> = {
  high: "default",
  medium: "secondary",
  low: "outline",
};

export function HoldingsTable({ rows }: { rows: HoldingRow[] }) {
  const router = useRouter();
  const [sorting, setSorting] = useState<SortingState>([{ id: "weight", desc: true }]);

  const columns = useMemo<ColumnDef<HoldingRow>[]>(
    () => [
      {
        accessorKey: "ticker_symbol",
        header: sortHeader("Symbol"),
        cell: ({ row }) => (
          <span className="flex items-center gap-1.5 font-medium">
            {row.original.ticker_symbol}
            {row.original.provisional ? (
              <TriangleAlert className="size-3.5 text-amber-500" aria-label="Provisional activity" />
            ) : null}
          </span>
        ),
      },
      {
        accessorKey: "security_name",
        header: "Name",
        cell: ({ getValue }) => (
          <span className="text-muted-foreground line-clamp-1">{getValue<string>()}</span>
        ),
      },
      {
        accessorKey: "weight",
        header: sortHeader("Weight", "right"),
        cell: ({ getValue }) => <div className="text-right tabular-nums">{fmtPct(getValue<number>())}</div>,
      },
      {
        accessorKey: "market_value",
        header: sortHeader("Mkt Value", "right"),
        cell: ({ getValue }) => <div className="text-right tabular-nums">{fmtCad(getValue<number>())}</div>,
      },
      {
        accessorKey: "unrealized_mkt",
        header: sortHeader("Unrealized", "right"),
        cell: ({ row }) => (
          <div className={`text-right tabular-nums ${signClass(row.original.unrealized_mkt)}`}>
            {fmtCcy(row.original.unrealized_mkt, row.original.currency)}
          </div>
        ),
      },
      {
        accessorKey: "pnl_pct",
        header: sortHeader("P/L %", "right"),
        cell: ({ getValue }) => {
          const v = getValue<number | null>();
          return <div className={`text-right tabular-nums ${signClass(v)}`}>{v === null ? "—" : fmtSignedPct(v)}</div>;
        },
      },
      {
        accessorKey: "group",
        header: "Group",
        cell: ({ getValue }) => getValue<string>() ?? "—",
      },
      {
        accessorKey: "confidence",
        header: "Confidence",
        cell: ({ getValue }) => {
          const c = getValue<string | null>();
          if (!c) return <span className="text-muted-foreground">—</span>;
          return (
            <Badge variant={CONFIDENCE_VARIANT[c.toLowerCase()] ?? "outline"} className="font-normal capitalize">
              {c}
            </Badge>
          );
        },
      },
    ],
    [],
  );

  const table = useReactTable({
    data: rows,
    columns,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  });

  return (
    <div className="overflow-x-auto rounded-lg border">
      <Table>
        <TableHeader>
          {table.getHeaderGroups().map((hg) => (
            <TableRow key={hg.id}>
              {hg.headers.map((header) => (
                <TableHead key={header.id}>
                  {header.isPlaceholder
                    ? null
                    : flexRender(header.column.columnDef.header, header.getContext())}
                </TableHead>
              ))}
            </TableRow>
          ))}
        </TableHeader>
        <TableBody>
          {table.getRowModel().rows.map((row) => (
            <TableRow
              key={row.id}
              className="cursor-pointer"
              onClick={() => router.push(`/holdings/${encodeURIComponent(row.original.ticker_symbol)}`)}
            >
              {row.getVisibleCells().map((cell) => (
                <TableCell key={cell.id}>
                  {flexRender(cell.column.columnDef.cell, cell.getContext())}
                </TableCell>
              ))}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
