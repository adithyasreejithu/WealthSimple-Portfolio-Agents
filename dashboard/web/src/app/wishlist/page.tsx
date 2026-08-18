export const dynamic = "force-dynamic";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { getWishlist } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import type { WishlistEntry } from "@/lib/types";

export default async function WishlistPage() {
  const wishlist = await getWishlist().catch(() => null);

  if (!wishlist) {
    return (
      <p className="text-muted-foreground text-sm">
        Wishlist data unavailable -- the dashboard API may be unreachable.
      </p>
    );
  }

  if (wishlist.wishlist.length === 0) {
    return (
      <p className="text-muted-foreground text-sm">
        No tickers are currently declared wishlist. Use{" "}
        <code className="bg-muted rounded px-1 py-0.5">
          python src/app.py database status --ticker TICKER --set wishlist
        </code>{" "}
        to add one.
      </p>
    );
  }

  return (
    <div className="space-y-4">
      <p className="text-muted-foreground text-sm">
        {wishlist.count} declared wishlist ticker{wishlist.count === 1 ? "" : "s"} -- not currently held,
        with the classifier&apos;s group and the latest research verdict any agent has recorded for them.
      </p>
      <div className="grid gap-4 lg:grid-cols-2">
        {wishlist.wishlist.map((entry) => (
          <WishlistCard key={entry.ticker} entry={entry} />
        ))}
      </div>
    </div>
  );
}

function WishlistCard({ entry }: { entry: WishlistEntry }) {
  const { declaration, classification, thesis, decision } = entry;
  return (
    <Card>
      <CardHeader className="flex-row items-start justify-between gap-2">
        <div>
          <CardTitle className="flex items-center gap-2">
            {entry.ticker}
            {classification ? (
              <Badge variant="outline" className="font-normal">
                {classification.primary_group}
              </Badge>
            ) : null}
          </CardTitle>
          <p className="text-muted-foreground text-xs">{entry.company_name ?? "—"}</p>
        </div>
        {decision ? <DecisionBadge decision={decision} /> : null}
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <div>
          <p className="text-muted-foreground text-xs">Declared wishlist</p>
          <p>{declaration.rationale ?? "No rationale recorded."}</p>
          <p className="text-muted-foreground text-xs">
            {fmtDate(declaration.declared_at)}
            {declaration.declared_by ? ` · by ${declaration.declared_by}` : ""}
          </p>
        </div>

        {classification ? (
          <div className="flex items-center gap-2 text-xs">
            <span className="text-muted-foreground">Classifier confidence</span>
            <Badge variant="outline" className="font-normal capitalize">
              {classification.confidence ?? "unknown"}
            </Badge>
          </div>
        ) : null}

        {thesis ? (
          <div className="grid grid-cols-2 gap-2 text-xs sm:grid-cols-3">
            <ThesisField label="Fundamental" value={thesis.fundamental_rating} />
            <ThesisField label="Valuation" value={thesis.valuation_stance} />
            <ThesisField label="Direction" value={thesis.thesis_direction} />
            <ThesisField label="Confidence" value={thesis.thesis_confidence} />
            <ThesisField label="Horizon" value={thesis.analysis_horizon} />
            <ThesisField label="As of" value={fmtDate(thesis.as_of)} />
          </div>
        ) : (
          <p className="text-muted-foreground text-xs">No research thesis recorded yet.</p>
        )}

        {decision ? (
          <div className="space-y-1 border-t pt-2">
            {decision.action_vocabulary_mismatch ? (
              <p className="text-xs text-destructive">
                Recorded action &quot;{decision.proposed_action}&quot; uses the owned-holding vocabulary
                (Buy/Hold/Trim/Sell/Add), not the Buy/Watch/Wait/Pass vocabulary a not-currently-held
                security requires -- treat this verdict as unreliable until re-run.
              </p>
            ) : null}
            {decision.summary ? <p className="text-xs">{decision.summary}</p> : null}
            {decision.failing_policy_checks.length > 0 ? (
              <ul className="text-muted-foreground text-xs">
                {decision.failing_policy_checks.map((check) => (
                  <li key={check.name}>
                    {check.name}: {check.detail}
                  </li>
                ))}
              </ul>
            ) : null}
          </div>
        ) : (
          <p className="text-muted-foreground text-xs">No portfolio-manager verdict recorded yet.</p>
        )}
      </CardContent>
    </Card>
  );
}

function ThesisField({ label, value }: { label: string; value: string | null }) {
  return (
    <div>
      <p className="text-muted-foreground">{label}</p>
      <p className="capitalize">{value ?? "—"}</p>
    </div>
  );
}

function DecisionBadge({ decision }: { decision: NonNullable<WishlistEntry["decision"]> }) {
  if (decision.action_vocabulary_mismatch) {
    return (
      <Badge variant="destructive" className="font-normal">
        {decision.proposed_action} (invalid)
      </Badge>
    );
  }
  return (
    <Badge variant="secondary" className="font-normal">
      {decision.proposed_action ?? "—"}
    </Badge>
  );
}
