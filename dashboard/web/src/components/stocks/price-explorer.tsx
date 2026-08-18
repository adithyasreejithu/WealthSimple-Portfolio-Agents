"use client";

import { useEffect, useMemo, useState } from "react";
import { CartesianGrid, Line, LineChart, XAxis, YAxis } from "recharts";
import { toast } from "sonner";

import {
  ChartContainer,
  ChartTooltip,
  ChartTooltipContent,
  type ChartConfig,
} from "@/components/ui/chart";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { Toggle } from "@/components/ui/toggle";
import { Skeleton } from "@/components/ui/skeleton";
import { getPriceHistory } from "@/lib/api";
import { indexToHundred, type IndexedPoint } from "@/lib/derive";
import { fmtCcy, fmtDate, fmtNumber } from "@/lib/format";
import type { HistoryRange, PriceHistory } from "@/lib/types";

const RANGES: HistoryRange[] = ["1m", "3m", "6m", "1y", "3y", "max"];
const BENCHMARK = "XEQT";

interface PriceExplorerProps {
  initialSymbol: string;
  symbols?: { symbol: string; name: string }[];
}

export function PriceExplorer({ initialSymbol, symbols }: PriceExplorerProps) {
  const [symbol, setSymbol] = useState(initialSymbol);
  const [range, setRange] = useState<HistoryRange>("1y");
  const [compare, setCompare] = useState(false);
  const showBenchmark = compare && symbol.toUpperCase() !== BENCHMARK;
  const requestKey = `${symbol}-${range}-${showBenchmark}`;
  const [result, setResult] = useState<{ key: string; primary: PriceHistory | null; benchmark: PriceHistory | null; error: string | null } | null>(null);

  useEffect(() => {
    let cancelled = false;
    Promise.all([
      getPriceHistory(symbol, range),
      showBenchmark ? getPriceHistory(BENCHMARK, range) : Promise.resolve(null),
    ])
      .then(([primary, benchmark]) => !cancelled && setResult({ key: requestKey, primary, benchmark, error: null }))
      .catch((e) => {
        if (cancelled) return;
        const message = e instanceof Error ? e.message : "Failed to load prices";
        setResult((previous) => ({ key: requestKey, primary: previous?.primary ?? null, benchmark: null, error: message }));
        toast.error(`Couldn't load ${symbol} prices`, { description: message });
      });
    return () => {
      cancelled = true;
    };
  }, [symbol, range, showBenchmark, requestKey]);

  const current = result?.key === requestKey ? result : null;
  const primary = current?.primary ?? null;
  const benchmark = current?.benchmark ?? null;
  const error = current?.error ?? null;
  const loading = current === null;

  const { chartData, config, indexed } = useMemo<{
    chartData: IndexedPoint[];
    config: ChartConfig;
    indexed: boolean;
  }>(() => {
    // Fetches for the primary and benchmark resolve independently, so guard
    // against joining series from different symbols mid-fetch.
    if (!primary || primary.ticker_symbol.toUpperCase() !== symbol.toUpperCase()) {
      return { chartData: [], config: {}, indexed: false };
    }
    if (showBenchmark && benchmark && benchmark.ticker_symbol.toUpperCase() === BENCHMARK) {
      const cfg: ChartConfig = {
        base: { label: primary.ticker_symbol, color: "var(--chart-1)" },
        compare: { label: BENCHMARK, color: "var(--chart-3)" },
      };
      return { chartData: indexToHundred(primary, benchmark), config: cfg, indexed: true };
    }
    const cfg: ChartConfig = { base: { label: primary.ticker_symbol, color: "var(--chart-1)" } };
    return {
      chartData: primary.points.map((p) => ({ date: p.date, base: p.close })),
      config: cfg,
      indexed: false,
    };
  }, [primary, benchmark, showBenchmark, symbol]);

  const currency = primary?.currency ?? "USD";

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        {symbols && symbols.length > 1 ? (
          <Select value={symbol} onValueChange={(v) => v && setSymbol(v)}>
            <SelectTrigger className="w-40">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {symbols.map((s) => (
                <SelectItem key={s.symbol} value={s.symbol}>
                  {s.symbol}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        ) : null}
        <ToggleGroup size="sm" value={[range]} onValueChange={(v) => v[0] && setRange(v[0] as HistoryRange)}>
          {RANGES.map((r) => (
            <ToggleGroupItem key={r} value={r} aria-label={r}>
              {r.toUpperCase()}
            </ToggleGroupItem>
          ))}
        </ToggleGroup>
        {symbol.toUpperCase() !== BENCHMARK ? (
          <Toggle size="sm" pressed={compare} onPressedChange={setCompare} variant="outline">
            vs {BENCHMARK}
          </Toggle>
        ) : null}
      </div>

      {/* The chart stays mounted across loading states with the skeleton overlaid on
          top. Mounting it only once loading flipped false made recharts'
          ResponsiveContainer take its first measurement in that same commit, where it
          could read a zero width and paint an empty SVG until the next resize — which
          is why the initial symbol showed blank until a reload or re-select. */}
      <div className="relative h-72 w-full">
        {error ? (
          <p className="text-muted-foreground flex h-full items-center justify-center text-sm">{error}</p>
        ) : (
          <ChartContainer config={config} className="h-72 w-full">
            <LineChart data={chartData} margin={{ left: 4, right: 8, top: 4 }}>
              <CartesianGrid vertical={false} strokeDasharray="3 3" />
              <XAxis
                dataKey="date"
                tickLine={false}
                axisLine={false}
                minTickGap={40}
                tickFormatter={(v) => fmtDate(v)}
              />
              <YAxis
                tickLine={false}
                axisLine={false}
                width={56}
                domain={["auto", "auto"]}
                tickFormatter={(v) => (indexed ? fmtNumber(Number(v), 0) : fmtCcy(Number(v), currency, 0))}
                label={
                  indexed
                    ? { value: "Indexed (100 = start)", angle: -90, position: "insideLeft", className: "fill-muted-foreground text-xs" }
                    : undefined
                }
              />
              <ChartTooltip
                content={
                  <ChartTooltipContent
                    labelFormatter={(v) => fmtDate(String(v))}
                    formatter={(value, name) =>
                      `${name}: ${indexed ? fmtNumber(Number(value)) : fmtCcy(Number(value), currency)}`
                    }
                  />
                }
              />
              <Line dataKey="base" type="monotone" stroke="var(--chart-1)" dot={false} strokeWidth={2} />
              {indexed ? (
                <Line dataKey="compare" type="monotone" stroke="var(--chart-3)" dot={false} strokeWidth={2} />
              ) : null}
            </LineChart>
          </ChartContainer>
        )}
        {loading ? <Skeleton className="absolute inset-0 z-10 rounded-lg" /> : null}
        {!loading && !error && chartData.length === 0 ? (
          <p className="text-muted-foreground bg-card absolute inset-0 z-10 flex items-center justify-center text-sm">
            No price history for {symbol}.
          </p>
        ) : null}
      </div>
    </div>
  );
}
