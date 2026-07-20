import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { AllocationDonut } from "@/components/charts/allocation-donut";
import { OTHER_COLOR, type NamedWeight } from "@/lib/derive";
import { fmtPct } from "@/lib/format";
import type { EtfOverlap } from "@/lib/types";

/**
 * How much of the ETF sleeve sits in underlying names more than one fund holds.
 * The donut splits the sleeve into overlapping / unique / unreported exposure;
 * the table below it names the fund pairs that actually collide. Slice values
 * are CAD so the donut's own tooltip math stays honest.
 */
export function OverlapCard({ overlap, sleeveValue }: { overlap: EtfOverlap; sleeveValue: number }) {
  if (!overlap.available) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>ETF overlap</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-muted-foreground text-sm">
            {overlap.reason ?? "Overlap cannot be measured yet."}
          </p>
        </CardContent>
      </Card>
    );
  }

  const { overlapping_weight, unique_weight, unreported_weight } = overlap.share;
  const slices: NamedWeight[] = [
    {
      label: "Overlapping",
      weight: overlapping_weight,
      market_value: overlapping_weight * sleeveValue,
      color: "var(--chart-1)",
    },
    {
      label: "Held by one fund",
      weight: unique_weight,
      market_value: unique_weight * sleeveValue,
      color: "var(--chart-3)",
    },
    {
      label: "Not reported",
      weight: unreported_weight,
      market_value: unreported_weight * sleeveValue,
      color: OTHER_COLOR,
    },
  ].filter((slice) => slice.weight > 0);

  return (
    <Card>
      <CardHeader>
        <CardTitle>ETF overlap</CardTitle>
      </CardHeader>
      <CardContent className="grid gap-6 lg:grid-cols-[minmax(0,20rem)_1fr]">
        <div>
          <AllocationDonut
            data={slices}
            centerValue={fmtPct(overlapping_weight)}
            centerLabel="overlapping"
          />
        </div>

        <div className="space-y-4">
          {overlap.pairs.length > 0 ? (
            <div className="space-y-2">
              <h3 className="text-sm font-medium">Funds holding the same names</h3>
              <ul className="space-y-2">
                {overlap.pairs.map((pair) => (
                  <li key={`${pair.a}-${pair.b}`} className="text-sm">
                    <div className="flex items-baseline justify-between gap-3">
                      <span className="font-medium">
                        {pair.a} ∩ {pair.b}
                      </span>
                      <span className="tabular-nums">{fmtPct(pair.overlap_pct)}</span>
                    </div>
                    <p className="text-muted-foreground truncate text-xs">
                      {pair.shared.map((s) => s.name).join(" · ")}
                    </p>
                  </li>
                ))}
              </ul>
            </div>
          ) : (
            <p className="text-muted-foreground text-sm">
              No two funds report holding the same underlying name.
            </p>
          )}

          {overlap.top_shared_holdings.length > 0 ? (
            <div className="space-y-2">
              <h3 className="text-sm font-medium">Most doubled-up positions</h3>
              <ul className="space-y-1">
                {overlap.top_shared_holdings.slice(0, 6).map((holding) => (
                  <li
                    key={holding.name}
                    className="flex items-baseline justify-between gap-3 text-sm"
                  >
                    <span className="truncate">
                      {holding.name}
                      <span className="text-muted-foreground text-xs"> · {holding.etfs.join(", ")}</span>
                    </span>
                    <span className="shrink-0 tabular-nums">{fmtPct(holding.combined_weight)}</span>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </div>
      </CardContent>
      <CardContent className="pt-0">
        <p className="text-muted-foreground text-xs">{overlap.caveat}</p>
      </CardContent>
    </Card>
  );
}
