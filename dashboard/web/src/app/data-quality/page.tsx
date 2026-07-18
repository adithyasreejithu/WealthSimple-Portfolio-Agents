export const dynamic = "force-dynamic";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { FlagsTable } from "@/components/tables/flags-table";
import { getClassifications, getHealth, getReport } from "@/lib/api";
import { fmtDateTime, fmtNumber } from "@/lib/format";

const SEVERITY_STYLE: Record<string, string> = {
  error: "border-destructive/50",
  warning: "border-amber-500/50",
  info: "border-border",
};

export default async function DataQualityPage() {
  const [{ database_mtime, report }, classifications, health] = await Promise.all([
    getReport(),
    getClassifications().catch(() => null),
    getHealth().catch(() => null),
  ]);

  const flags = report.data_quality.flags;
  const bySeverity = flags.reduce<Record<string, number>>((acc, f) => {
    acc[f.severity] = (acc[f.severity] ?? 0) + 1;
    return acc;
  }, {});

  const provisional = report.holdings.filter((h) => h.has_provisional_activity);
  const review = classifications?.classifications.filter((c) => c.review_needed) ?? [];

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4">
        {(["error", "warning", "info"] as const).map((sev) => (
          <Card key={sev} className={`gap-0 py-4 ${SEVERITY_STYLE[sev]}`}>
            <CardContent className="px-4">
              <p className="text-muted-foreground text-sm capitalize">{sev}</p>
              <p className="mt-1 text-2xl font-semibold tabular-nums">{bySeverity[sev] ?? 0}</p>
            </CardContent>
          </Card>
        ))}
        <Card className="gap-0 py-4">
          <CardContent className="px-4">
            <p className="text-muted-foreground text-sm">Provisional holdings</p>
            <p className="mt-1 text-2xl font-semibold tabular-nums">{provisional.length}</p>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Data-quality flags</CardTitle>
        </CardHeader>
        <CardContent>
          <FlagsTable flags={flags} />
        </CardContent>
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Provisional activity</CardTitle>
          </CardHeader>
          <CardContent>
            {provisional.length ? (
              <ul className="space-y-2 text-sm">
                {provisional.map((h) => (
                  <li key={h.ticker_id} className="flex justify-between">
                    <span className="font-medium">{h.ticker_symbol}</span>
                    <span className="text-muted-foreground tabular-nums">
                      {fmtNumber(h.provisional_quantity, 4)} provisional units
                    </span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-muted-foreground text-sm">No unreconciled provisional activity.</p>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Classifications needing review</CardTitle>
          </CardHeader>
          <CardContent>
            {review.length ? (
              <ul className="space-y-3 text-sm">
                {review.map((c) => (
                  <li key={c.ticker_id}>
                    <div className="flex items-center gap-2">
                      <span className="font-medium">{c.ticker_symbol}</span>
                      <Badge variant="outline" className="font-normal capitalize">
                        {c.confidence ?? "unknown"}
                      </Badge>
                    </div>
                    {c.missing_data.length ? (
                      <p className="text-muted-foreground text-xs">Missing: {c.missing_data.join(", ")}</p>
                    ) : null}
                    {c.reasoning ? <p className="text-muted-foreground text-xs">{c.reasoning}</p> : null}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-muted-foreground text-sm">No classifications flagged for review.</p>
            )}
          </CardContent>
        </Card>
      </div>

      {report.unavailable_metrics.length ? (
        <Card>
          <CardHeader>
            <CardTitle>Unavailable metrics</CardTitle>
          </CardHeader>
          <CardContent>
            <ul className="space-y-1 text-sm">
              {report.unavailable_metrics.map((m) => (
                <li key={m.metric} className="flex flex-col sm:flex-row sm:justify-between sm:gap-4">
                  <span className="font-mono text-xs">{m.metric}</span>
                  <span className="text-muted-foreground">{m.reason}</span>
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      ) : null}

      <p className="text-muted-foreground text-xs">
        Cash source: {report.summary.cash.source} · Database updated {fmtDateTime(database_mtime)}
        {health ? ` · ${health.database}` : ""}
      </p>
    </div>
  );
}
