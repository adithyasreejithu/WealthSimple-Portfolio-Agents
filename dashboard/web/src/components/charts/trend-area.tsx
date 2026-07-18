"use client";

import { useMemo, useState } from "react";
import { Area, CartesianGrid, ComposedChart, Line, XAxis, YAxis } from "recharts";

import {
  ChartContainer,
  ChartTooltip,
  ChartTooltipContent,
  type ChartConfig,
} from "@/components/ui/chart";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { Toggle } from "@/components/ui/toggle";
import type { TrendOverlayPoint, TrendOverlays, TrendPoint } from "@/lib/types";
import { fmtCad, fmtCompact, fmtDate } from "@/lib/format";

const RANGES = [
  { key: "3m", label: "3M", days: 91 },
  { key: "1y", label: "1Y", days: 365 },
  { key: "max", label: "Max", days: null },
] as const;

// Benchmark overlays are re-anchored per visible range: the line is scaled so
// its first drawable point equals the portfolio value on that date ("what if
// the portfolio had tracked the benchmark from here").
const BENCHMARK_KEYS = ["XEQT", "SP500"] as const;

const SERIES = {
  portfolio_value: { label: "Portfolio value", color: "var(--chart-1)" },
  net_deposits: { label: "Net deposits", color: "var(--muted-foreground)" },
  XEQT: { label: "XEQT", color: "var(--chart-2)" },
  SP500: { label: "S&P 500 (VFV)", color: "var(--chart-3)" },
} satisfies ChartConfig;

interface TrendAreaProps {
  data: TrendPoint[];
  overlays?: TrendOverlays | null;
}

export function TrendArea({ data, overlays }: TrendAreaProps) {
  const [range, setRange] = useState<string>("1y");
  const [showDeposits, setShowDeposits] = useState(false);
  const [shownBenchmarks, setShownBenchmarks] = useState<Record<string, boolean>>({});

  const overlaysAvailable = overlays?.available ?? false;

  const sliced = useMemo(() => {
    const spec = RANGES.find((r) => r.key === range);
    if (!spec?.days) return data;
    const cutoff = new Date();
    cutoff.setDate(cutoff.getDate() - spec.days);
    const iso = cutoff.toISOString().slice(0, 10);
    return data.filter((d) => d.date >= iso);
  }, [data, range]);

  const overlayByDate = useMemo(() => {
    const byDate = new Map<string, TrendOverlayPoint>();
    overlays?.points.forEach((p) => byDate.set(p.date, p));
    return byDate;
  }, [overlays]);

  const chartData = useMemo(() => {
    const rows = sliced.map((d) => ({ point: d, overlay: overlayByDate.get(d.date) }));
    const scales: Record<string, number | null> = {};
    for (const key of BENCHMARK_KEYS) {
      if (!shownBenchmarks[key]) continue;
      scales[key] = null;
      for (const r of rows) {
        const close = r.overlay?.benchmarks?.[key];
        if (close != null && close > 0 && r.point.portfolio_value > 0) {
          scales[key] = r.point.portfolio_value / close;
          break;
        }
      }
    }
    return rows.map(({ point, overlay }) => {
      const row: Record<string, number | string | null> = {
        date: point.date,
        portfolio_value: point.portfolio_value,
      };
      if (showDeposits) row.net_deposits = overlay?.net_deposits_cum ?? null;
      for (const key of BENCHMARK_KEYS) {
        if (!shownBenchmarks[key]) continue;
        const close = overlay?.benchmarks?.[key];
        row[key] = close != null && scales[key] != null ? close * scales[key]! : null;
      }
      return row;
    });
  }, [sliced, overlayByDate, showDeposits, shownBenchmarks]);

  const benchmarkToggles = BENCHMARK_KEYS.filter(
    (key) => overlays?.benchmarks?.[key]?.available,
  );

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        {overlaysAvailable ? (
          <Toggle
            size="sm"
            variant="outline"
            pressed={showDeposits}
            onPressedChange={setShowDeposits}
          >
            <span
              className="size-2 rounded-full"
              style={{ background: SERIES.net_deposits.color }}
            />
            Net deposits
          </Toggle>
        ) : null}
        {benchmarkToggles.map((key) => (
          <Toggle
            key={key}
            size="sm"
            variant="outline"
            pressed={!!shownBenchmarks[key]}
            onPressedChange={(pressed) =>
              setShownBenchmarks((prev) => ({ ...prev, [key]: pressed }))
            }
          >
            <span className="size-2 rounded-full" style={{ background: SERIES[key].color }} />
            {SERIES[key].label}
          </Toggle>
        ))}
        <div className="ml-auto">
          <ToggleGroup
            size="sm"
            value={[range]}
            onValueChange={(v) => v[0] && setRange(String(v[0]))}
          >
            {RANGES.map((r) => (
              <ToggleGroupItem key={r.key} value={r.key} aria-label={r.label}>
                {r.label}
              </ToggleGroupItem>
            ))}
          </ToggleGroup>
        </div>
      </div>
      <ChartContainer config={SERIES} className="h-64 w-full">
        <ComposedChart data={chartData} margin={{ left: 4, right: 8, top: 4 }}>
          <defs>
            <linearGradient id="fillTrend" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor="var(--chart-1)" stopOpacity={0.3} />
              <stop offset="95%" stopColor="var(--chart-1)" stopOpacity={0.02} />
            </linearGradient>
          </defs>
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
            width={48}
            tickFormatter={(v) => fmtCompact(v)}
          />
          <ChartTooltip
            content={
              <ChartTooltipContent
                labelFormatter={(v) => fmtDate(String(v))}
                formatter={(value, name) =>
                  `${SERIES[name as keyof typeof SERIES]?.label ?? name}: ${fmtCad(Number(value))}`
                }
              />
            }
          />
          <Area
            dataKey="portfolio_value"
            type="monotone"
            stroke="var(--chart-1)"
            fill="url(#fillTrend)"
            strokeWidth={2}
          />
          {showDeposits ? (
            <Line
              dataKey="net_deposits"
              type="stepAfter"
              stroke={SERIES.net_deposits.color}
              strokeDasharray="4 4"
              dot={false}
              strokeWidth={2}
            />
          ) : null}
          {benchmarkToggles
            .filter((key) => shownBenchmarks[key])
            .map((key) => (
              <Line
                key={key}
                dataKey={key}
                type="monotone"
                stroke={SERIES[key].color}
                dot={false}
                strokeWidth={2}
              />
            ))}
        </ComposedChart>
      </ChartContainer>
    </div>
  );
}
