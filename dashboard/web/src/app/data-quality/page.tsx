export const dynamic = "force-dynamic";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { FlagsTable } from "@/components/tables/flags-table";
import { ClassifyDialog } from "@/components/actions/classify-dialog";
import { ClassifyActionButton } from "@/components/actions/classify-action-button";
import { ResolveTickerForm } from "@/components/actions/resolve-ticker-form";
import { getClassifications, getHealth, getPendingTickers, getReport } from "@/lib/api";
import { ASSIGNABLE_GROUPS } from "@/lib/derive";
import { fmtDateTime, fmtNumber } from "@/lib/format";

const SEVERITY_STYLE: Record<string, string> = {
  error: "border-destructive/50",
  warning: "border-amber-500/50",
  info: "border-border",
};

export default async function DataQualityPage() {
  const [{ database_mtime, report }, classifications, health, pendingTickers] = await Promise.all([
    getReport(),
    getClassifications().catch(() => null),
    getHealth().catch(() => null),
    getPendingTickers().catch(() => []),
  ]);

  const flags = report.data_quality.flags;
  const bySeverity = flags.reduce<Record<string, number>>((acc, f) => {
    acc[f.severity] = (acc[f.severity] ?? 0) + 1;
    return acc;
  }, {});

  const provisional = report.holdings.filter((h) => h.has_provisional_activity);
  const review = classifications?.classifications.filter((c) => c.review_needed) ?? [];
  // A holding with no portfolio_classifications row at all can never appear in
  // `classifications.classifications` (that list is built by joining FROM the
  // table) or in `review` above, so it needs its own diff against the report's
  // holdings -- this is the only place an unclassified holding is reachable
  // with a Classify action, matching the unclassified_holding data-quality flag.
  const classifiedIds = new Set((classifications?.classifications ?? []).map((c) => c.ticker_id));
  const unclassified = report.holdings.filter((h) => !classifiedIds.has(h.ticker_id));
  const classificationStale = Boolean(
    classifications?.generated_at && new Date(classifications.generated_at) < new Date(database_mtime),
  );

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

      {unclassified.length ? (
        <Card className="border-amber-500/50">
          <CardHeader>
            <CardTitle>Unclassified holdings</CardTitle>
          </CardHeader>
          <CardContent>
            <ul className="space-y-3 text-sm">
              {unclassified.map((h) => (
                <li key={h.ticker_id} className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <span className="font-medium">{h.ticker_symbol}</span>
                    <p className="text-muted-foreground text-xs">
                      No portfolio_classifications row for this ticker -- excluded from group
                      allocation until it is classified.
                    </p>
                  </div>
                  <ClassifyActionButton />
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      ) : null}

      {pendingTickers.length ? (
        <Card>
          <CardHeader>
            <CardTitle>Unresolved ticker symbols</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <p className="text-muted-foreground text-sm">
              These symbols block ingestion until they are mapped to a canonical and Yahoo
              symbol. The symbol is verified before the mapping is saved.
            </p>
            <ResolveTickerForm pending={pendingTickers} />
          </CardContent>
        </Card>
      ) : null}

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
                  <li key={c.ticker_id} className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="font-medium">{c.ticker_symbol}</span>
                        <Badge variant="outline" className="font-normal capitalize">
                          {c.confidence ?? "unknown"}
                        </Badge>
                      </div>
                      {c.missing_data.length ? (
                        <p className="text-muted-foreground text-xs">
                          Missing: {c.missing_data.join(", ")}
                        </p>
                      ) : null}
                      {c.reasoning ? (
                        <p className="text-muted-foreground text-xs">{c.reasoning}</p>
                      ) : null}
                    </div>
                    <ClassifyDialog
                      ticker={c.ticker_symbol}
                      groups={ASSIGNABLE_GROUPS}
                      currentGroup={c.primary_group}
                    />
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
        {classifications?.generated_at ? (
          <>
            {" · Classification data "}
            {fmtDateTime(classifications.generated_at)}
            {classificationStale ? (
              <span className="text-amber-500"> (older than the database -- run classification)</span>
            ) : null}
          </>
        ) : null}
      </p>
    </div>
  );
}
