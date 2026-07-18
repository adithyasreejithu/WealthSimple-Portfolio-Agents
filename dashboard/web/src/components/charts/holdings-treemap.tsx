"use client";

import { ResponsiveContainer, Tooltip, Treemap } from "recharts";

import { useIsMobile } from "@/hooks/use-mobile";
import { WeightBars } from "@/components/charts/weight-bars";
import { groupColor } from "@/lib/derive";
import { fmtCad, fmtPct } from "@/lib/format";

export interface TreemapDatum {
  name: string;
  size: number; // market value
  weight: number;
  group: string;
  // Recharts' Treemap data type carries an index signature.
  [key: string]: string | number;
}

/**
 * Holdings sized by market value, colored by classification group. On phones a
 * treemap's labels are unreadable, so we fall back to a top-holdings bar list.
 */
export function HoldingsTreemap({ data }: { data: TreemapDatum[] }) {
  const isMobile = useIsMobile();

  if (isMobile) {
    const top = [...data].sort((a, b) => b.weight - a.weight).slice(0, 10);
    return (
      <WeightBars
        data={top.map((d) => ({ label: d.name, weight: d.weight, color: groupColor(d.group) }))}
      />
    );
  }

  return (
    <ResponsiveContainer width="100%" height={320}>
      <Treemap
        data={data}
        dataKey="size"
        nameKey="name"
        stroke="var(--background)"
        content={<TreemapCell />}
        isAnimationActive={false}
      >
        <Tooltip content={<TreemapTooltip />} />
      </Treemap>
    </ResponsiveContainer>
  );
}

interface CellProps {
  x?: number;
  y?: number;
  width?: number;
  height?: number;
  name?: string;
  group?: string;
  weight?: number;
}

function TreemapCell({ x = 0, y = 0, width = 0, height = 0, name, group, weight }: CellProps) {
  const showLabel = width > 44 && height > 24;
  return (
    <g>
      <rect x={x} y={y} width={width} height={height} fill={groupColor(group ?? "")} rx={2} />
      {showLabel ? (
        <>
          <text x={x + 5} y={y + 15} className="fill-white text-[11px] font-medium">
            {name}
          </text>
          {height > 38 ? (
            <text x={x + 5} y={y + 29} className="fill-white/80 text-[10px]">
              {fmtPct(weight ?? 0)}
            </text>
          ) : null}
        </>
      ) : null}
    </g>
  );
}

function TreemapTooltip({ active, payload }: { active?: boolean; payload?: { payload: TreemapDatum }[] }) {
  if (!active || !payload?.length) return null;
  const d = payload[0].payload;
  return (
    <div className="rounded-md border bg-background px-2.5 py-1.5 text-xs shadow-sm">
      <p className="font-medium">{d.name}</p>
      <p className="text-muted-foreground">
        {fmtCad(d.size)} · {fmtPct(d.weight)} · {d.group}
      </p>
    </div>
  );
}
