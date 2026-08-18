"use client";

import { useMemo, useState } from "react";

import { Toggle } from "@/components/ui/toggle";
import type { CorrelationMatrix } from "@/lib/types";
import { fmtDate, fmtNumber } from "@/lib/format";

interface CorrelationHeatmapProps {
  matrix: CorrelationMatrix;
}

/**
 * Diverging cell colour built from the existing gain/loss theme tokens rather
 * than a new hardcoded palette -- positive values lean toward `--gain`,
 * negative toward `--loss`, magnitude scaled by `color-mix` against the card
 * background so the scale stays correct in both themes automatically.
 */
function cellBackground(value: number, maxMagnitude: number): string {
  if (maxMagnitude <= 0) return "var(--card)";
  const magnitude = Math.min(Math.abs(value) / maxMagnitude, 1);
  const tone = value >= 0 ? "var(--gain)" : "var(--loss)";
  const percent = Math.round(magnitude * 70);
  return `color-mix(in oklab, ${tone} ${percent}%, var(--card))`;
}

export function CorrelationHeatmap({ matrix }: CorrelationHeatmapProps) {
  const [mode, setMode] = useState<"correlation" | "covariance">("correlation");

  const { tickers, cells, maxMagnitude } = useMemo(() => {
    const t = matrix.tickers;
    const source = mode === "correlation" ? matrix.correlation : matrix.covariance;
    let max = 0;
    for (const a of t) {
      for (const b of t) {
        const v = source[a]?.[b] ?? 0;
        max = Math.max(max, Math.abs(v));
      }
    }
    // Correlation is bounded, so its scale is fixed at 1.0 regardless of
    // what this particular selection happens to reach -- otherwise a
    // low-correlation subset would render with the same visual intensity as
    // a highly-correlated one.
    return { tickers: t, cells: source, maxMagnitude: mode === "correlation" ? 1 : max };
  }, [matrix, mode]);

  if (!matrix.available) {
    return (
      <p className="text-muted-foreground text-sm">
        {matrix.reason ?? "Correlation matrix unavailable for this selection."}
      </p>
    );
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex gap-1">
          <Toggle size="sm" variant="outline" pressed={mode === "correlation"} onPressedChange={() => setMode("correlation")}>
            Correlation
          </Toggle>
          <Toggle size="sm" variant="outline" pressed={mode === "covariance"} onPressedChange={() => setMode("covariance")}>
            Covariance
          </Toggle>
        </div>
        <p className="text-muted-foreground text-xs">
          {matrix.observations} trading day{matrix.observations === 1 ? "" : "s"} ·{" "}
          {fmtDate(matrix.start_date)} – {fmtDate(matrix.end_date)}
        </p>
      </div>

      <div className="overflow-x-auto">
        <div
          className="grid w-fit"
          style={{ gridTemplateColumns: `minmax(3.5rem, auto) repeat(${tickers.length}, minmax(2.75rem, 1fr))` }}
        >
          <div />
          {tickers.map((t) => (
            <div
              key={`col-${t}`}
              className="text-muted-foreground flex items-end justify-center pb-1 text-[0.68rem] font-medium"
            >
              {t}
            </div>
          ))}
          {tickers.map((rowTicker) => (
            <FragmentRow key={rowTicker} rowTicker={rowTicker} tickers={tickers} cells={cells} maxMagnitude={maxMagnitude} mode={mode} />
          ))}
        </div>
      </div>

      {matrix.unavailable_tickers.length ? (
        <p className="text-muted-foreground text-xs">
          Excluded (incomplete price history over this window): {matrix.unavailable_tickers.join(", ")}
        </p>
      ) : null}
      {"unavailable_groups" in matrix && Array.isArray(matrix.unavailable_groups) && matrix.unavailable_groups.length ? (
        <p className="text-muted-foreground text-xs">Unavailable groups: {matrix.unavailable_groups.join(", ")}</p>
      ) : null}
    </div>
  );
}

function FragmentRow({
  rowTicker,
  tickers,
  cells,
  maxMagnitude,
  mode,
}: {
  rowTicker: string;
  tickers: string[];
  cells: Record<string, Record<string, number>>;
  maxMagnitude: number;
  mode: "correlation" | "covariance";
}) {
  return (
    <>
      <div className="text-muted-foreground flex items-center pr-2 text-[0.68rem] font-medium">{rowTicker}</div>
      {tickers.map((colTicker) => {
        const value = cells[rowTicker]?.[colTicker] ?? null;
        return (
          <div
            key={`${rowTicker}-${colTicker}`}
            // Native title rather than the Tooltip primitive: up to 27x27 =
            // 729 cells, and mounting a full Base UI Tooltip.Root per cell
            // would be a lot of overhead for a hover label.
            title={value === null ? "No data" : `${rowTicker} × ${colTicker}: ${fmtNumber(value, mode === "correlation" ? 3 : 6)}`}
            className="flex aspect-square items-center justify-center border border-transparent text-[0.62rem] tabular-nums"
            style={{ backgroundColor: value === null ? "var(--muted)" : cellBackground(value, maxMagnitude) }}
          >
            {value === null ? "" : fmtNumber(value, mode === "correlation" ? 2 : 3)}
          </div>
        );
      })}
    </>
  );
}
