"use client";

import { useMemo } from "react";
import { Bar, CartesianGrid, ComposedChart, Line, XAxis, YAxis } from "recharts";

import {
  ChartContainer,
  ChartTooltip,
  ChartTooltipContent,
  type ChartConfig,
} from "@/components/ui/chart";
import { fmtCad } from "@/lib/format";

export interface DividendMonth {
  month: string;
  amount: number;
  rolling: number | null;
  /** Present only when `payers` is passed to `DividendBars` -- one entry per payer key. */
  [payerKey: string]: number | string | null;
}

export interface DividendPayer {
  key: string;
  label: string;
  color: string;
}

interface DividendBarsProps {
  data: DividendMonth[];
  /** When provided, renders one stacked bar segment per payer instead of a single total bar. */
  payers?: DividendPayer[];
}

function monthLabel(m: string): string {
  const [y, mo] = m.split("-");
  const d = new Date(Number(y), Number(mo) - 1, 1);
  return d.toLocaleDateString("en-CA", { month: "short", year: "2-digit" });
}

export function DividendBars({ data, payers }: DividendBarsProps) {
  const config = useMemo<ChartConfig>(() => {
    const cfg: ChartConfig = { rolling: { label: "12-mo avg", color: "var(--chart-1)" } };
    if (payers) {
      for (const p of payers) cfg[p.key] = { label: p.label, color: p.color };
    } else {
      cfg.amount = { label: "Dividends", color: "var(--chart-4)" };
    }
    return cfg;
  }, [payers]);

  return (
    <ChartContainer config={config} className="h-72 w-full">
      <ComposedChart data={data} margin={{ left: 4, right: 8, top: 4 }}>
        <CartesianGrid vertical={false} strokeDasharray="3 3" />
        <XAxis
          dataKey="month"
          tickLine={false}
          axisLine={false}
          minTickGap={24}
          tickFormatter={monthLabel}
        />
        <YAxis tickLine={false} axisLine={false} width={44} tickFormatter={(v) => `$${v}`} />
        <ChartTooltip
          content={
            <ChartTooltipContent
              labelFormatter={(v) => monthLabel(String(v))}
              formatter={(value, name) =>
                `${name === "rolling" ? "12-mo avg" : (config[name as string]?.label ?? name)}: ${fmtCad(Number(value))}`
              }
            />
          }
        />
        {payers ? (
          payers.map((p, i) => (
            <Bar
              key={p.key}
              dataKey={p.key}
              stackId="dividends"
              fill={p.color}
              radius={i === payers.length - 1 ? [3, 3, 0, 0] : [0, 0, 0, 0]}
            />
          ))
        ) : (
          <Bar dataKey="amount" fill="var(--chart-4)" radius={[3, 3, 0, 0]} />
        )}
        <Line dataKey="rolling" type="monotone" stroke="var(--chart-1)" dot={false} strokeWidth={2} connectNulls />
      </ComposedChart>
    </ChartContainer>
  );
}
