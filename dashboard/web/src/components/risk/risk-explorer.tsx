"use client";

import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { Skeleton } from "@/components/ui/skeleton";
import { StatTile } from "@/components/stat-tile";
import { CorrelationHeatmap } from "@/components/charts/correlation-heatmap";
import { TickerPicker, type TickerOption } from "@/components/risk/ticker-picker";
import { getCorrelation, getGroupCorrelation } from "@/lib/api";
import { groupColor, groupRank } from "@/lib/derive";
import { fmtNumber, fmtPct } from "@/lib/format";
import type { CorrelationMatrix, CorrelationWindow, GroupCorrelationMatrix } from "@/lib/types";

const WINDOWS: { key: CorrelationWindow; label: string }[] = [{ key: "3m", label: "3M" }, { key: "1y", label: "1Y" }, { key: "3y", label: "3Y" }];

export function RiskExplorer({ options }: { options: TickerOption[] }) {
  const allSymbols = useMemo(() => options.map((option) => option.symbol), [options]);
  const allGroups = useMemo(() => [...new Set(options.map((option) => option.group).filter((group): group is string => Boolean(group)))].sort((a, b) => groupRank(a) - groupRank(b)), [options]);
  const [mode, setMode] = useState<"holdings" | "groups">("holdings");
  const [selectedSymbols, setSelectedSymbols] = useState<string[]>(allSymbols);
  const [selectedGroups, setSelectedGroups] = useState<string[]>(allGroups);
  const [window_, setWindow] = useState<CorrelationWindow>("1y");
  const selection = mode === "holdings" ? selectedSymbols : selectedGroups;

  return (
    <div className="space-y-4">
      <Card><CardHeader><CardTitle>Comparison</CardTitle></CardHeader><CardContent className="space-y-4">
        <div className="flex flex-wrap items-center gap-3">
          <ToggleGroup size="sm" variant="outline" value={[mode]} onValueChange={(value) => value[0] && setMode(value[0] as "holdings" | "groups")}>
            <ToggleGroupItem value="holdings">Holdings</ToggleGroupItem><ToggleGroupItem value="groups">Classification groups</ToggleGroupItem>
          </ToggleGroup>
          <div className="flex items-center gap-2"><span className="text-muted-foreground text-xs">Window</span><ToggleGroup size="sm" value={[window_]} onValueChange={(value) => value[0] && setWindow(value[0] as CorrelationWindow)}>{WINDOWS.map((window) => <ToggleGroupItem key={window.key} value={window.key}>{window.label}</ToggleGroupItem>)}</ToggleGroup></div>
        </div>
        {mode === "holdings" ? <TickerPicker options={options} selected={selectedSymbols} onChange={setSelectedSymbols} /> : (
          <div className="space-y-3">
            <ToggleGroup multiple value={selectedGroups} onValueChange={(value) => setSelectedGroups(value as string[])} className="flex-wrap justify-start">
              {allGroups.map((group) => <ToggleGroupItem key={group} value={group} size="sm" variant="outline" className="gap-1.5"><span className="size-2 rounded-full" style={{ background: groupColor(group) }} />{group}</ToggleGroupItem>)}
            </ToggleGroup>
            <p className="text-muted-foreground text-xs">Each group is a current-market-value-weighted basket, renormalized within the group. These are comparative proxies, not historical allocation returns.</p>
          </div>
        )}
      </CardContent></Card>

      {selection.length ? <AsyncRiskMatrix key={`${mode}-${window_}-${selection.join("|")}`} mode={mode} selection={selection} window_={window_} /> : <Card><CardContent className="py-6 text-muted-foreground text-sm">Select at least {mode === "groups" ? "two groups" : "one holding"}.</CardContent></Card>}
    </div>
  );
}

function AsyncRiskMatrix({ mode, selection, window_ }: { mode: "holdings" | "groups"; selection: string[]; window_: CorrelationWindow }) {
  const [matrix, setMatrix] = useState<CorrelationMatrix | GroupCorrelationMatrix | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const request = mode === "holdings" ? getCorrelation(selection, window_) : getGroupCorrelation(selection, window_);
    request.then((data) => { if (!cancelled) setMatrix(data); }).catch((reason) => {
      if (cancelled) return;
      const message = reason instanceof Error ? reason.message : "Failed to load risk data";
      setError(message);
      toast.error("Couldn't load risk comparison", { description: message });
    });
    return () => { cancelled = true; };
  }, [mode, selection, window_]);

  const risk = matrix?.available ? matrix.portfolio_risk : undefined;
  const contributors = risk?.available ? Object.entries(risk.risk_contribution).sort((a, b) => b[1] - a[1]).slice(0, 8) : [];
  const groupMatrix = mode === "groups" && matrix ? matrix as GroupCorrelationMatrix : null;

  return <>
    <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
      <StatTile label={`${mode === "groups" ? "Group basket" : "Selection"} volatility`} value={risk?.available ? fmtPct(risk.portfolio_volatility) : "—"} />
      <StatTile label="Selection coverage" value={risk?.available ? fmtPct(risk.covered_weight) : "—"} />
      <StatTile label={mode === "groups" ? "Groups in matrix" : "Tickers in matrix"} value={matrix?.available ? matrix.tickers.length : "—"} />
      <StatTile label="Observations" value={matrix?.available ? matrix.observations : "—"} />
    </div>
    <Card><CardHeader><CardTitle>Correlation &amp; covariance</CardTitle></CardHeader><CardContent><div className="relative min-h-40">{error ? <p className="text-muted-foreground text-sm">{error}</p> : matrix ? <CorrelationHeatmap matrix={matrix} /> : <Skeleton className="absolute inset-0 rounded-lg" />}</div>{groupMatrix?.unclassified_weight ? <p className="text-muted-foreground mt-3 text-xs">Unclassified holdings excluded: {fmtPct(groupMatrix.unclassified_weight)} of securities value.</p> : null}</CardContent></Card>
    {contributors.length ? <Card><CardHeader><CardTitle>Risk contribution</CardTitle></CardHeader><CardContent><ul className="space-y-1.5 text-sm">{contributors.map(([label, contribution]) => <li key={label} className="flex items-center justify-between gap-3"><span className="flex items-center gap-2 font-medium">{mode === "groups" ? <span className="size-2 rounded-full" style={{ background: groupColor(label) }} /> : null}{label}</span><span className="text-muted-foreground tabular-nums">{fmtNumber(contribution, 4)}{risk?.available && risk.portfolio_volatility ? ` (${fmtPct(contribution / risk.portfolio_volatility)})` : ""}</span></li>)}</ul></CardContent></Card> : null}
  </>;
}
