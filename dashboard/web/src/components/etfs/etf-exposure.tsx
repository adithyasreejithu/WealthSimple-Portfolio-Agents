"use client";

import { useState } from "react";

import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { canonicalSector, categoryColor, sectorColor } from "@/lib/derive";
import { fmtPct } from "@/lib/format";

export interface ExposureRow {
  label: string;
  weight: number;
}

export function EtfExposure({ sectors, companies }: { sectors: ExposureRow[]; companies: ExposureRow[] }) {
  const [mode, setMode] = useState<"sectors" | "companies">("sectors");
  const rows = mode === "sectors" ? sectors : companies;
  const reportedWeight = rows.reduce((sum, row) => sum + row.weight, 0);

  return (
    <div className="space-y-4">
      <ToggleGroup size="sm" variant="outline" value={[mode]} onValueChange={(value) => value[0] && setMode(value[0] as "sectors" | "companies")}>
        <ToggleGroupItem value="sectors">Sectors</ToggleGroupItem>
        <ToggleGroupItem value="companies">Companies</ToggleGroupItem>
      </ToggleGroup>
      {rows.length ? (
        <div className="space-y-2.5">
          {rows.map((row, index) => {
            const label = mode === "sectors" ? canonicalSector(row.label) : row.label;
            const color = mode === "sectors" ? sectorColor(label) : categoryColor(index);
            return (
              <div key={`${mode}-${row.label}`} className="grid grid-cols-[minmax(7rem,12rem)_1fr_auto] items-center gap-3 text-sm">
                <span className="truncate font-medium" title={label}>{label}</span>
                <div className="bg-muted h-2 overflow-hidden rounded-full">
                  <div className="h-full rounded-full" style={{ width: `${Math.min(row.weight * 100, 100)}%`, background: color }} />
                </div>
                <span className="text-muted-foreground w-14 text-right tabular-nums">{fmtPct(row.weight)}</span>
              </div>
            );
          })}
          {mode === "companies" && reportedWeight < 0.995 ? (
            <p className="text-muted-foreground pt-1 text-xs">Reported top holdings cover {fmtPct(reportedWeight)} of the fund; remaining holdings are not available in the stored classification data.</p>
          ) : null}
        </div>
      ) : (
        <p className="text-muted-foreground text-sm">No {mode === "sectors" ? "sector weights" : "reported company holdings"} are available for this ETF.</p>
      )}
    </div>
  );
}
