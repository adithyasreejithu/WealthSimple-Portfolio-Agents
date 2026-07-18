// Typed client for the read-only dashboard API. Server components call these
// from the Next server (no CORS); the one client-side caller is the Stocks
// explorer, which fetches price history from the browser.
//
// Every response reflects the last pipeline run — the API caches the full
// report on the DuckDB file's mtime — so we always request fresh
// (`cache: "no-store"`) rather than layer Next's cache on top.

import type {
  Classifications,
  Health,
  HistoryRange,
  Holding,
  PriceHistory,
  ReportEnvelope,
  Summary,
  TrendPoint,
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
export const getPriceHistory = (symbol: string, range: HistoryRange = "1y") =>
  getJson<PriceHistory>(`/api/stocks/${encodeURIComponent(symbol)}/history?range=${range}`);
