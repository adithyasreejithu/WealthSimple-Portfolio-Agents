"use client";

import { Cell, Label, Pie, PieChart } from "recharts";

import {
  ChartContainer,
  ChartTooltip,
  ChartTooltipContent,
  type ChartConfig,
} from "@/components/ui/chart";
import { fmtCad, fmtPct } from "@/lib/format";
import type { NamedWeight } from "@/lib/derive";

interface AllocationDonutProps {
  data: NamedWeight[];
  centerLabel?: string;
  centerValue?: string;
}

export function AllocationDonut({ data, centerLabel, centerValue }: AllocationDonutProps) {
  const config: ChartConfig = Object.fromEntries(
    data.map((d) => [d.label, { label: d.label, color: d.color }]),
  );

  return (
    <div className="flex flex-col items-center gap-3">
      <ChartContainer config={config} className="mx-auto aspect-square h-52">
        <PieChart>
          <ChartTooltip
            content={
              <ChartTooltipContent
                hideLabel
                formatter={(value, name) => (
                  <div className="flex w-full justify-between gap-3">
                    <span className="text-muted-foreground">{name}</span>
                    <span className="font-medium tabular-nums">
                      {fmtCad(Number(value))} · {fmtPct(Number(value) / total(data))}
                    </span>
                  </div>
                )}
              />
            }
          />
          <Pie
            data={data}
            dataKey="market_value"
            nameKey="label"
            innerRadius={55}
            outerRadius={80}
            strokeWidth={2}
          >
            {data.map((d) => (
              <Cell key={d.label} fill={d.color} />
            ))}
            {centerValue ? (
              <Label
                content={({ viewBox }) => {
                  if (!viewBox || !("cx" in viewBox)) return null;
                  const { cx, cy } = viewBox as { cx: number; cy: number };
                  return (
                    <text x={cx} y={cy} textAnchor="middle" dominantBaseline="middle">
                      <tspan x={cx} y={cy - 6} className="fill-foreground text-lg font-semibold">
                        {centerValue}
                      </tspan>
                      {centerLabel ? (
                        <tspan x={cx} y={cy + 12} className="fill-muted-foreground text-xs">
                          {centerLabel}
                        </tspan>
                      ) : null}
                    </text>
                  );
                }}
              />
            ) : null}
          </Pie>
        </PieChart>
      </ChartContainer>
      <ul className="grid w-full grid-cols-1 gap-1 text-sm sm:grid-cols-2">
        {data.map((d) => (
          <li key={d.label} className="flex items-center justify-between gap-2">
            <span className="flex items-center gap-1.5 truncate">
              <span className="size-2.5 shrink-0 rounded-[2px]" style={{ background: d.color }} />
              <span className="truncate">{d.label}</span>
            </span>
            <span className="text-muted-foreground shrink-0 tabular-nums">{fmtPct(d.weight)}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function total(data: NamedWeight[]): number {
  return data.reduce((s, d) => s + d.market_value, 0) || 1;
}
