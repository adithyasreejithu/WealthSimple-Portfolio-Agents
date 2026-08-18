// Typed client for the read-only dashboard API. Server components call these
// from the Next server (no CORS); the one client-side caller is the Stocks
// explorer, which fetches price history from the browser.
//
// Every response reflects the last pipeline run — the API caches the full
// report on a database watermark that advances on every write (see
// analytics.get_data_watermark), invalidated further on every completed
// action job — so we always request fresh (`cache: "no-store"`) rather than
// layer Next's cache on top.

import type {
  ActionsState,
  Classifications,
  CorrelationMatrix,
  GroupCorrelationMatrix,
  CorrelationWindow,
  EtfOverlap,
  Health,
  Job,
  PendingTicker,
  HistoryRange,
  Holding,
  PriceHistory,
  ReportEnvelope,
  Summary,
  TrendPoint,
  UpcomingIncome,
  WishlistOverview,
} from "./types";

export const API_BASE =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function getJson<T>(path: string): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, { cache: "no-store" });
  } catch {
    throw new ApiError(
      `Cannot reach the dashboard API at ${API_BASE}. Is uvicorn running on port 8000?`,
      0,
    );
  }
  if (!response.ok) {
    let detail = "";
    try {
      const body = await response.json();
      detail = typeof body?.detail === "string" ? ` — ${body.detail}` : "";
    } catch {
      /* body was not JSON */
    }
    throw new ApiError(`API ${path} returned ${response.status}${detail}`, response.status);
  }
  return (await response.json()) as T;
}

export const getHealth = () => getJson<Health>("/health");
export const getSummary = () => getJson<Summary>("/api/portfolio/summary");
export const getHoldings = () => getJson<Holding[]>("/api/portfolio/holdings");
export const getTrend = () => getJson<TrendPoint[]>("/api/portfolio/trend");
export const getReport = () => getJson<ReportEnvelope>("/api/portfolio/report");
export const getClassifications = () =>
  getJson<Classifications>("/api/portfolio/classifications");
export const getEtfOverlap = () => getJson<EtfOverlap>("/api/etfs/overlap");
export const getPriceHistory = (symbol: string, range: HistoryRange = "1y") =>
  getJson<PriceHistory>(`/api/stocks/${encodeURIComponent(symbol)}/history?range=${range}`);
export const getCorrelation = (symbols: string[], window: CorrelationWindow = "1y") => {
  const params = new URLSearchParams({ window });
  symbols.forEach((s) => params.append("symbols", s));
  return getJson<CorrelationMatrix>(`/api/portfolio/correlation?${params.toString()}`);
};
export const getGroupCorrelation = (groups: string[], window: CorrelationWindow = "1y") => {
  const params = new URLSearchParams({ window });
  groups.forEach((group) => params.append("groups", group));
  return getJson<GroupCorrelationMatrix>(`/api/portfolio/group-correlation?${params.toString()}`);
};
export const getUpcomingIncome = (horizon = 90) =>
  getJson<UpcomingIncome>(`/api/income/upcoming?horizon=${horizon}`);
export const getWishlist = () => getJson<WishlistOverview>("/api/wishlist");

// --- Actions -------------------------------------------------------------
// The only calls that change state. They are browser-side (the forms are
// client components), and every one is gated by the API's action auth: a
// bearer token when NEXT_PUBLIC_ACTION_TOKEN is configured, otherwise the API
// accepts loopback callers only.

async function postJson<T>(path: string, body?: unknown): Promise<T> {
  const token = process.env.NEXT_PUBLIC_ACTION_TOKEN;
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      method: "POST",
      cache: "no-store",
      headers: {
        "Content-Type": "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(
      `Cannot reach the dashboard API at ${API_BASE}. Is uvicorn running on port 8000, ` +
        "and does DASHBOARD_CORS_ORIGINS on the API include this page's origin " +
        `(${typeof window !== "undefined" ? window.location.origin : "unknown"})?`,
      0,
    );
  }
  if (!response.ok) {
    let detail = "";
    try {
      const payload = await response.json();
      detail = typeof payload?.detail === "string" ? payload.detail : "";
    } catch {
      /* body was not JSON */
    }
    throw new ApiError(detail || `API ${path} returned ${response.status}`, response.status);
  }
  return (await response.json()) as T;
}

export const getPendingTickers = () => getJson<PendingTicker[]>("/api/tickers/pending");
export const getActions = () => getJson<ActionsState>("/api/actions");
export const getJob = (id: string) => getJson<Job>(`/api/actions/${encodeURIComponent(id)}`);

export const postClassify = () => postJson<Job>("/api/actions/classify");
export const postRefresh = () => postJson<Job>("/api/actions/refresh");
export const postOverride = (body: {
  ticker: string;
  primary_group: string;
  rationale: string;
  secondary_tags?: string[];
}) => postJson<{ override: Record<string, unknown>; job: Job }>("/api/actions/overrides", body);
export const postResolveTicker = (body: {
  source_symbol: string;
  canonical_symbol: string;
  yahoo_symbol: string;
  currency: string;
  exchange?: string;
}) => postJson<Job>("/api/actions/resolve-ticker", body);
export const postRetryTicker = (body: { source_symbol: string }) =>
  postJson<Job>("/api/actions/retry-ticker", body);
