// Shared join of report holdings with their classification detail, used by the
// Stocks and ETFs tables and the holding detail route.

import type { ClassificationDetail, Holding } from "./types";

export interface HoldingRow {
  ticker_symbol: string;
  security_name: string;
  group: string | null;
  weight: number;
  market_value: number;
  unrealized_mkt: number;
  currency: string;
  pnl_pct: number | null;
  confidence: string | null;
  provisional: boolean;
}

export function buildHoldingRows(
  holdings: Holding[],
  byTicker: Map<number, ClassificationDetail>,
): HoldingRow[] {
  return holdings.map((h) => {
    const c = byTicker.get(h.ticker_id);
    const pnl_pct = h.cost_basis_mkt > 0 ? h.unrealized_mkt / h.cost_basis_mkt : null;
    return {
      ticker_symbol: h.ticker_symbol,
      security_name: h.security_name,
      group: c?.primary_group ?? null,
      weight: h.weight,
      market_value: h.market_value,
      unrealized_mkt: h.unrealized_mkt,
      currency: h.currency,
      pnl_pct,
      confidence: c?.confidence ?? null,
      provisional: h.has_provisional_activity,
    };
  });
}
