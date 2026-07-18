import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { fmtCad, fmtPct, fmtPp } from "@/lib/format";
import { driftBadgeVariant, groupColor } from "@/lib/derive";

export interface GroupCardData {
  group: string;
  market_value: number;
  weight: number;
  targetPercent: number | null;
  driftPp: number | null;
  rebalanceNeeded: boolean;
  topHolding: string | null;
}

export function GroupCard({ data }: { data: GroupCardData }) {
  return (
    <Card className="gap-0 py-4">
      <CardContent className="px-4">
        <div className="flex items-center gap-1.5">
          <span className="size-2.5 rounded-[2px]" style={{ background: groupColor(data.group) }} />
          <span className="font-medium">{data.group}</span>
        </div>
        <p className="mt-2 text-xl font-semibold tabular-nums">{fmtCad(data.market_value)}</p>
        <div className="mt-1 flex items-center justify-between text-sm">
          <span className="text-muted-foreground tabular-nums">{fmtPct(data.weight)}</span>
          {data.targetPercent !== null ? (
            <Badge variant={driftBadgeVariant(data.driftPp)} className="font-normal">
              {data.driftPp !== null && data.driftPp > 0 ? "+" : ""}
              {fmtPp(data.driftPp)} vs {data.targetPercent}%
            </Badge>
          ) : null}
        </div>
        {data.topHolding ? (
          <p className="text-muted-foreground mt-2 truncate text-xs">Top: {data.topHolding}</p>
        ) : null}
      </CardContent>
    </Card>
  );
}
