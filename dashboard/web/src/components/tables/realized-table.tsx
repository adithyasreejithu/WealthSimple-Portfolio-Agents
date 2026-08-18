"use client";

import { useMemo, useState } from "react";
import {
  type ColumnDef,
  type SortingState,
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  useReactTable,
} from "@tanstack/react-table";
import { ArrowDown, ArrowUp, ChevronsUpDown } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { fmtCad, fmtDate, signClass } from "@/lib/format";

interface RealizedEvent {
  event_date: string;
  ticker_symbol: string;
  realized_gain_cad: number;
  amount_quality?: string;
}

/** Label + tooltip for a sale whose proceeds aren't a confirmed brokerage figure. */
function amountQualityBadge(quality: string | undefined) {
  if (!quality || quality === "reported") return null;
  if (quality === "missing") {
    return (
      <Badge variant="outline" className="ml-2 text-amber-600" title="No source has reported this sale's proceeds yet; shown as break-even until one does.">
        Unconfirmed
      </Badge>
    );
  }
  return (
    <Badge variant="outline" className="ml-2 text-amber-600" title="Proceeds derived from quantity x fill price; not yet confirmed by a statement or activity export.">
      Estimated
    </Badge>
  );
}

function SortHeader({ label, column }: { label: string; column: import("@tanstack/react-table").Column<RealizedEvent> }) {
  return (
    <Button
      variant="ghost"
      size="sm"
      className="-mx-2 h-7 ml-auto"
      onClick={() => column.toggleSorting(column.getIsSorted() === "asc")}
    >
      {label}
      {column.getIsSorted() === "asc" ? <ArrowUp className="size-3.5" /> : column.getIsSorted() === "desc" ? <ArrowDown className="size-3.5" /> : <ChevronsUpDown className="size-3.5 opacity-50" />}
    </Button>
  );
}

export function RealizedTable({ events }: { events: RealizedEvent[] }) {
  const [sorting, setSorting] = useState<SortingState>([{ id: "event_date", desc: true }]);
  const columns = useMemo<ColumnDef<RealizedEvent>[]>(() => [
    {
      accessorKey: "event_date",
      header: ({ column }) => <SortHeader label="Date" column={column} />,
      cell: ({ getValue }) => <div className="text-right tabular-nums">{fmtDate(getValue<string>())}</div>,
    },
    {
      accessorKey: "ticker_symbol",
      header: "Ticker",
      cell: ({ getValue, row }) => (
        <span className="font-medium">
          {getValue<string>()}
          {amountQualityBadge(row.original.amount_quality)}
        </span>
      ),
    },
    {
      accessorKey: "realized_gain_cad",
      header: ({ column }) => <SortHeader label="Realized (CAD)" column={column} />,
      cell: ({ getValue }) => {
        const value = getValue<number>();
        return <div className={`text-right tabular-nums ${signClass(value)}`}>{fmtCad(value)}</div>;
      },
    },
  ], []);
  const table = useReactTable({ data: events, columns, state: { sorting }, onSortingChange: setSorting, getCoreRowModel: getCoreRowModel(), getSortedRowModel: getSortedRowModel() });

  if (!events.length) return <p className="text-muted-foreground text-sm">No realized gains recorded.</p>;
  return (
    <div className="max-h-72 overflow-y-auto rounded-lg border">
      <Table>
        <TableHeader>{table.getHeaderGroups().map((group) => <TableRow key={group.id}>{group.headers.map((header) => <TableHead key={header.id}>{header.isPlaceholder ? null : flexRender(header.column.columnDef.header, header.getContext())}</TableHead>)}</TableRow>)}</TableHeader>
        <TableBody>{table.getRowModel().rows.map((row) => <TableRow key={row.id}>{row.getVisibleCells().map((cell) => <TableCell key={cell.id}>{flexRender(cell.column.columnDef.cell, cell.getContext())}</TableCell>)}</TableRow>)}</TableBody>
      </Table>
    </div>
  );
}
