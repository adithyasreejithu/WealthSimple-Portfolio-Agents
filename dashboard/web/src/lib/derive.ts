// Client-side derivations the API does not pre-compute. Kept in one place so
// the same math (blended MER, compare-mode indexing, group colors) is used
// everywhere.

import type { ClassificationDetail, PriceHistory, WeightMap } from "./types";

// Fixed group -> chart color slot. Color follows the group, never its rank, so
// a group keeps its color as weights shift. Unknown groups fall through to the
// neutral slot handled by the caller.
export const GROUP_COLORS: Record<string, string> = {
  Core: "var(--chart-1)",
  Growth: "var(--chart-2)",
  Quality: "var(--chart-3)",
  Income: "var(--chart-4)",
  Alternatives: "var(--chart-5)",
};

export const CHART_SLOTS = [
  "var(--chart-1)",
  "var(--chart-2)",
  "var(--chart-3)",
  "var(--chart-4)",
  "var(--chart-5)",
  "var(--chart-6)",
  "var(--chart-7)",
  "var(--chart-8)",
];
export const OTHER_COLOR = "var(--muted-foreground)";

// Pinned colors for labels whose identity should never depend on rank:
// CAD/USD keep their hue no matter which currency dominates.
export const CURRENCY_COLORS: Record<string, string> = {
  CAD: "var(--chart-1)",
  USD: "var(--chart-2)",
};

export function groupColor(group: string): string {
  return GROUP_COLORS[group] ?? OTHER_COLOR;
}

/** Assign colors to an ordered set of category labels, folding extras to Other. */
export function categoryColor(index: number): string {
  return index < CHART_SLOTS.length ? CHART_SLOTS[index] : OTHER_COLOR;
}

export interface NamedWeight {
  label: string;
  market_value: number;
  weight: number;
  color: string;
}

/**
 * Turn a WeightMap into a sorted array, keeping the top `limit` by weight and
 * folding the remainder into a single "Other" slice so charts never run out
 * of hues. Labels present in `pinned` always get their pinned color; the rest
 * take palette slots in order.
 */
export function topWeights(
  map: WeightMap | null | undefined,
  limit = 5,
  pinned?: Record<string, string>,
): NamedWeight[] {
  if (!map) return [];
  const entries = Object.entries(map)
    .map(([label, v]) => ({ label, market_value: v.market_value, weight: v.weight }))
    .sort((a, b) => b.weight - a.weight);
  let slot = 0;
  const pinnedColors = new Set(Object.values(pinned ?? {}));
  const colorFor = (label: string) => {
    const pin = pinned?.[label];
    if (pin) return pin;
    while (slot < CHART_SLOTS.length && pinnedColors.has(CHART_SLOTS[slot])) slot++;
    return categoryColor(slot++);
  };
  if (entries.length <= limit) {
    return entries.map((e) => ({ ...e, color: colorFor(e.label) }));
  }
  const head = entries.slice(0, limit).map((e) => ({ ...e, color: colorFor(e.label) }));
  const rest = entries.slice(limit);
  const other: NamedWeight = {
    label: "Other",
    market_value: rest.reduce((s, e) => s + e.market_value, 0),
    weight: rest.reduce((s, e) => s + e.weight, 0),
    color: OTHER_COLOR,
  };
  return [...head, other];
}

/**
 * Drift badge severity for group allocation vs target: essentially on target
 * (<=1pp) stays neutral, up to 5pp is a warning, beyond 5pp needs action.
 */
export function driftBadgeVariant(
  driftPp: number | null | undefined,
): "secondary" | "warning" | "destructive" {
  if (driftPp === null || driftPp === undefined) return "secondary";
  const drift = Math.abs(driftPp);
  if (drift > 5) return "destructive";
  if (drift > 1) return "warning";
  return "secondary";
}

/** Weight-average expense ratio across ETFs that report one. */
export interface BlendedMer {
  value: number | null;
  coveredWeight: number;
  missingCount: number;
}

export function blendedMer(
  etfs: { weight: number; expense_ratio: number | null | undefined }[],
): BlendedMer {
  let weightedSum = 0;
  let coveredWeight = 0;
  let missingCount = 0;
  for (const etf of etfs) {
    if (etf.expense_ratio === null || etf.expense_ratio === undefined || Number.isNaN(etf.expense_ratio)) {
      missingCount += 1;
      continue;
    }
    weightedSum += etf.weight * etf.expense_ratio;
    coveredWeight += etf.weight;
  }
  return {
    value: coveredWeight > 0 ? weightedSum / coveredWeight : null,
    coveredWeight,
    missingCount,
  };
}

/**
 * Index one or two close series to 100 at their first shared date so a holding
 * and its benchmark are visually comparable regardless of price level.
 */
export interface IndexedPoint {
  date: string;
  base: number | null;
  compare?: number | null;
}

export function indexToHundred(
  primary: PriceHistory,
  compare?: PriceHistory | null,
): IndexedPoint[] {
  const compareByDate = new Map<string, number>();
  if (compare) {
    for (const p of compare.points) compareByDate.set(p.date, p.close);
  }

  // First date present in both series (or just the primary when no compare).
  let baseStart: number | null = null;
  let compareStart: number | null = null;
  for (const p of primary.points) {
    const c = compare ? compareByDate.get(p.date) : undefined;
    if (compare && c === undefined) continue;
    baseStart = p.close;
    compareStart = c ?? null;
    break;
  }
  if (baseStart === null || baseStart === 0) {
    return primary.points.map((p) => ({ date: p.date, base: null }));
  }

  return primary.points.map((p) => {
    const point: IndexedPoint = { date: p.date, base: (p.close / baseStart!) * 100 };
    if (compare) {
      const c = compareByDate.get(p.date);
      point.compare =
        c !== undefined && compareStart && compareStart !== 0 ? (c / compareStart) * 100 : null;
    }
    return point;
  });
}

/** Index of classifications by ticker_id for joining against holdings. */
export function classificationsById(
  rows: ClassificationDetail[],
): Map<number, ClassificationDetail> {
  return new Map(rows.map((r) => [r.ticker_id, r]));
}

const CONFIDENCE_ORDER: Record<string, number> = { high: 3, medium: 2, low: 1 };
export function confidenceRank(confidence: string | null | undefined): number {
  if (!confidence) return 0;
  return CONFIDENCE_ORDER[confidence.toLowerCase()] ?? 0;
}
