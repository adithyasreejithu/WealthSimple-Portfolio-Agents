"use client";

import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { postResolveTicker, postRetryTicker } from "@/lib/api";
import { useAction } from "@/lib/use-action";
import type { PendingTicker } from "@/lib/types";

/** Canadian listings need the exchange suffix Yahoo Finance publishes them under. */
function defaultYahooSymbol(canonical: string, currency: string): string {
  if (!canonical) return "";
  if (/\.(TO|V|NE|CN)$/i.test(canonical)) return canonical;
  return currency === "CAD" ? `${canonical}.TO` : canonical;
}

/** Already-mapped symbol still pending reprocessing: no fields, just retry. */
function AlreadyMappedRow({ pending }: { pending: PendingTicker }) {
  const { run, running } = useAction();
  const mapping = pending.mapping;

  return (
    <li className="flex flex-wrap items-center justify-between gap-2 rounded-md border p-3">
      <div>
        <span className="font-medium">{pending.source_symbol}</span>
        <p className="text-muted-foreground text-xs">
          {mapping
            ? `Already mapped to ${mapping.canonical_symbol} → ${mapping.provider_symbol} (${mapping.currency})`
            : "Already mapped"}
          {" · "}
          {pending.trade_count} pending trade{pending.trade_count === 1 ? "" : "s"} still
          quarantined
        </p>
      </div>
      <Button
        size="sm"
        disabled={running}
        onClick={() =>
          run(() => postRetryTicker({ source_symbol: pending.source_symbol }), {
            pending: `Retrying ${pending.source_symbol}…`,
            success: `${pending.source_symbol} reprocessed`,
          })
        }
      >
        {running ? "Retrying…" : "Retry"}
      </Button>
    </li>
  );
}

function UnmappedRow({ pending }: { pending: PendingTicker }) {
  const [canonical, setCanonical] = useState(pending.source_symbol);
  const [currency, setCurrency] = useState(pending.detected_currency ?? "");
  const [yahoo, setYahoo] = useState("");
  const [exchange, setExchange] = useState("");
  const { run, running } = useAction();

  const provider = yahoo || defaultYahooSymbol(canonical, currency);
  const complete = canonical.trim() !== "" && currency !== "" && provider.trim() !== "";

  return (
    <li className="space-y-2 rounded-md border p-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <span className="font-medium">{pending.source_symbol}</span>
        <span className="text-muted-foreground text-xs">
          {pending.trade_count} pending trade{pending.trade_count === 1 ? "" : "s"} ·{" "}
          {pending.sources.join(", ")}
        </span>
      </div>

      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
        <Select value={currency} onValueChange={(v) => setCurrency(v ?? "")}>
          <SelectTrigger aria-label="Currency">
            <SelectValue placeholder="Currency" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="CAD">CAD</SelectItem>
            <SelectItem value="USD">USD</SelectItem>
          </SelectContent>
        </Select>
        <Input
          value={canonical}
          onChange={(e) => setCanonical(e.target.value.toUpperCase())}
          placeholder="Canonical symbol"
          aria-label="Canonical symbol"
        />
        <Input
          value={provider}
          onChange={(e) => setYahoo(e.target.value.toUpperCase())}
          placeholder="Yahoo symbol"
          aria-label="Yahoo symbol"
        />
        <Input
          value={exchange}
          onChange={(e) => setExchange(e.target.value.toUpperCase())}
          placeholder="Exchange (optional)"
          aria-label="Exchange"
        />
      </div>

      <div className="flex justify-end">
        <Button
          size="sm"
          disabled={!complete || running}
          onClick={() =>
            run(
              () =>
                postResolveTicker({
                  source_symbol: pending.source_symbol,
                  canonical_symbol: canonical,
                  yahoo_symbol: provider,
                  currency,
                  exchange,
                }),
              {
                pending: `Verifying ${provider}…`,
                success: `${pending.source_symbol} mapped to ${canonical}`,
              },
            )
          }
        >
          {running ? "Resolving…" : "Resolve"}
        </Button>
      </div>
    </li>
  );
}

/**
 * Web equivalent of the interactive `resolve-tickers` CLI. The symbol is
 * verified against Yahoo Finance server-side before any mapping is saved, so
 * an unverifiable symbol fails the job rather than storing a bad alias.
 */
export function ResolveTickerForm({ pending }: { pending: PendingTicker[] }) {
  if (pending.length === 0) {
    return <p className="text-muted-foreground text-sm">No unresolved symbols.</p>;
  }
  return (
    <ul className="space-y-3">
      {pending.map((row) =>
        row.already_mapped ? (
          <AlreadyMappedRow key={row.source_symbol} pending={row} />
        ) : (
          <UnmappedRow key={row.source_symbol} pending={row} />
        ),
      )}
    </ul>
  );
}
