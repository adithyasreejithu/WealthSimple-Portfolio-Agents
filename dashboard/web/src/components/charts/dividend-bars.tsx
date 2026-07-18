"use client";

import { Bar, CartesianGrid, ComposedChart, Line, XAxis, YAxis } from "recharts";

import {
  ChartContainer,
  ChartTooltip,
  ChartTooltipContent,
  type ChartConfig,
} from "@/components/ui/chart";
import { fmtCad } from "@/lib/format";

const config = {
  amount: { label: "Dividends", color: "var(--chart-4)" },
  rolling: { label: "12-mo avg", color: "var(--chart-1)" },
} satisfies ChartConfig;

export interface DividendMonth {
  month: string;
  amount: number;
  rolling: number | null;
}

function monthLabel(m: string): string {
  const [y, mo] = m.split("-");
  const d = new Date(Number(y), Number(mo) - 1, 1);
  return d.toLocaleDateString("en-CA", { month: "short", year: "2-digit" });
}

export function DividendBars({ data }: { data: DividendMonth[] }) {
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
              formatter={(value, name) => `${name === "rolling" ? "12-mo avg" : "Dividends"}: ${fmtCad(Number(value))}`}
            />
          }
        />
        <Bar dataKey="amount" fill="var(--chart-4)" radius={[3, 3, 0, 0]} />
        <Line dataKey="rolling" type="monotone" stroke="var(--chart-1)" dot={false} strokeWidth={2} connectNulls />
      </ComposedChart>
    </ChartContainer>
  );
}
