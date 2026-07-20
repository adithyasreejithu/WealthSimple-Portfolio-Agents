import { Badge } from "@/components/ui/badge";
import type { TargetGroup } from "@/lib/types";
import { driftBadgeVariant, groupColor, sortByGroupOrder } from "@/lib/derive";
import { fmtPp } from "@/lib/format";

/**
 * One track per policy group: the filled bar is the actual weight, the vertical
 * marker is the target. Groups needing a rebalance (outside their band) are
 * flagged. Percentages here are already in percent points (actual_percent etc).
 */
export function TargetVsActual({ groups }: { groups: TargetGroup[] }) {
  const ceiling = Math.max(
    ...groups.map((g) => Math.max(g.actual_percent, g.target_percent ?? 0)),
    1,
  );

  const sorted = sortByGroupOrder(groups, (g) => g.group);

  return (
    <ul className="space-y-3">
      {sorted.map((g) => {
        const targetPos = g.target_percent !== null ? (g.target_percent / ceiling) * 100 : null;
        return (
          <li key={g.group} className="space-y-1">
            <div className="flex items-center justify-between text-sm">
              <span className="font-medium">{g.group}</span>
              <span className="flex items-center gap-2 tabular-nums">
                <span>{fmtPp(g.actual_percent)}</span>
                {g.target_percent !== null ? (
                  <Badge variant={driftBadgeVariant(g.drift_pp)} className="font-normal">
                    target {g.target_percent}%
                  </Badge>
                ) : (
                  <span className="text-muted-foreground text-xs">no target</span>
                )}
              </span>
            </div>
            <div className="relative h-3 rounded-full bg-muted">
              <span
                className="block h-full rounded-full"
                style={{
                  width: `${Math.min(100, (g.actual_percent / ceiling) * 100)}%`,
                  background: groupColor(g.group),
                }}
              />
              {targetPos !== null ? (
                <span
                  className="absolute top-[-2px] h-[calc(100%+4px)] w-0.5 bg-foreground"
                  style={{ left: `${Math.min(100, targetPos)}%` }}
                  title={`Target ${g.target_percent}%`}
                />
              ) : null}
            </div>
          </li>
        );
      })}
    </ul>
  );
}
