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
  const [primary, setPrimary] = useState<PriceHistory | null>(null);
  const [benchmark, setBenchmark] = useState<PriceHistory | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const showBenchmark = compare && symbol.toUpperCase() !== BENCHMARK;

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    const jobs: Promise<unknown>[] = [
      getPriceHistory(symbol, range).then((d) => !cancelled && setPrimary(d)),
    ];
    if (showBenchmark) {
      jobs.push(getPriceHistory(BENCHMARK, range).then((d) => !cancelled && setBenchmark(d)));
    } else {
      setBenchmark(null);
    }
    Promise.all(jobs)
      .catch((e) => {
        if (cancelled) return;
        const message = e instanceof Error ? e.message : "Failed to load prices";
        setError(message);
        toast.error(`Couldn't load ${symbol} prices`, { description: message });
      })
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [symbol, range, showBenchmark]);

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

      {loading ? (
        <Skeleton className="h-72 w-full rounded-lg" />
      ) : error ? (
        <p className="text-muted-foreground flex h-72 items-center justify-center text-sm">{error}</p>
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
    </div>
  );
}
