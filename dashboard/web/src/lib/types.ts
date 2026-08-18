// TypeScript mirrors of the dashboard API payloads (dashboard/api/main.py,
// which serializes src/analytics.py output). Only the fields the UI reads are
// typed narrowly; open-ended maps use index signatures.

export type SecurityType = "stock" | "etf" | string;

export interface Holding {
  ticker_id: number;
  ticker_symbol: string;
  exchange: string;
  security_name: string;
  security_type: SecurityType;
  quantity: number;
  cost_basis: number; // CAD book value
  market_value: number; // CAD
  currency: string; // listing currency
  last_price: number | null;
  last_price_date: string | null;
  provisional_quantity: number;
  has_provisional_activity: boolean;
  market_value_mkt: number; // listing currency
  cost_basis_mkt: number;
  unrealized_mkt: number; // listing currency
  realized_gain_cad: number;
  data_quality_flags: string[];
  weight: number; // fraction of portfolio (from /holdings endpoint)
}

export interface TrendPoint {
  date: string;
  securities_value: number;
  cash_balance: number;
  portfolio_value: number;
}

export interface TrendOverlayPoint {
  date: string;
  net_deposits_cum: number;
  /** Raw CAD closes per overlay key (e.g. XEQT, SP500); null before inception. */
  benchmarks: Record<string, number | null>;
}

export interface TrendOverlays {
  available: boolean;
  reason?: string | null;
  benchmarks: Record<string, { symbol: string; available: boolean; reason?: string }>;
  points: TrendOverlayPoint[];
}

export interface Summary {
  portfolio_value: number;
  cash_balance: number;
  cash_source: string;
  holdings_count: number;
}

/** A metric that may be unavailable, carrying a reason when it is. */
export interface Availability {
  available: boolean;
  reason?: string | null;
  [key: string]: unknown;
}

export interface WeightEntry {
  market_value: number;
  weight: number;
}
export type WeightMap = Record<string, WeightEntry>;

export interface TargetGroup {
  group: string;
  target_percent: number | null;
  min_percent: number | null;
  max_percent: number | null;
  actual_percent: number;
  drift_pp: number | null;
  rebalance_needed: boolean;
}

export interface Concentration {
  available: boolean;
  top_1: number;
  top_5: number;
  top_10: number;
  hhi: number;
  effective_holdings: number | null;
  max_single_name_weight: number;
  max_single_name_ticker: string;
  single_name_limit: number;
  single_name_limit_breached: boolean;
  /** Largest holding after exempting broad-market core ETFs from the cap. */
  max_flagged_name_weight?: number | null;
  max_flagged_name_ticker?: string | null;
  exempt_tickers?: string[];
}

export interface ReportSummary {
  portfolio_value: number;
  cash: {
    balance: number;
    source: string;
    /** CAD portion of `balance` from still-provisional, unreconciled email trades. Zero once reconciled. */
    provisional_adjustment: number;
    /** Subset of provisional_adjustment built from a derived or missing (not 'reported') amount. */
    estimated_adjustment: number;
  };
  securities_value: number;
  book_cost: number;
  holdings_count: number;
  unrealized_gain: { amount: number; percent: number };
  realized_gain_total: number;
}

export interface DrawdownDetail extends Availability {
  current_drawdown: number;
  max_drawdown: number;
  peak_date: string | null;
  trough_date: string | null;
  recovery_date: string | null;
  days_to_recover: number | null;
}

export interface Report {
  schema_version: string;
  generated_at: string;
  summary: ReportSummary;
  holdings: Holding[];
  allocation: {
    by_ticker: WeightMap;
    by_group: WeightMap;
    by_sector: WeightMap;
    by_currency: WeightMap;
    by_geography: WeightMap | null;
    look_through_sector: { available: boolean; weights: Record<string, number>; coverage_percent: number };
    concentration: Concentration;
  };
  targets: { available: boolean; source_file?: string; groups: TargetGroup[] } | null;
  performance: {
    historical_values: TrendPoint[];
    trend_overlays?: TrendOverlays | null;
    adjusted_returns: {
      total_return: Availability & { total_return: number; annualized_return: number; span_days: number };
      volatility: Availability & { volatility: number; daily_volatility: number };
      sharpe_ratio: Availability & { sharpe_ratio: number };
      sortino_ratio: Availability & { sortino_ratio: number };
      drawdown: DrawdownDetail;
    };
    money_weighted: {
      source: string;
      contributions: number;
      withdrawals: number;
      net_contributions: number;
      net_investment_profit: number;
      money_weighted_return: Availability & { xirr: number };
    };
    benchmark: Availability & {
      symbol: string;
      overlap_days: number;
      active_return: number;
      tracking_error: number;
      information_ratio: number;
      beta: number;
      alpha: number;
    };
  };
  income: {
    source: string;
    transaction_count: number;
    by_month: Record<string, number>;
    totals_by_currency: Record<string, number>;
    by_ticker: Record<
      string,
      {
        ticker_id: number | null;
        totals_by_currency: Record<string, number>;
        trailing_12_month_by_currency: Record<string, number>;
        transaction_count: number;
      }
    >;
    /** month -> ticker_symbol -> amount, currencies summed within a cell (see by_month). */
    by_ticker_month: Record<string, Record<string, number>>;
    trailing_12_month: {
      totals_by_currency: Record<string, number>;
      yield_on_portfolio_value: Availability & { value: number };
    };
    yield_on_cost: Availability & { value: number };
    dividend_growth: Availability & { value: number; from_year: number; to_year: number };
  };
  fees: {
    commissions: { source: string; transaction_count: number; totals_by_currency: Record<string, number> };
    fx: Availability & {
      source: string;
      fee_rate: number;
      transaction_count: number;
      buy_count: number;
      sell_count: number;
      cad_exposure: number;
      estimated_buy_fee_cad: number;
      estimated_sell_fee_cad: number;
      estimated_fx_fee_cad: number;
      fee_percent_of_exposure: number;
    };
    fee_drag: {
      fx_fee_percent_of_value: Availability & { value: number };
      fx_fee_percent_of_gains: Availability & { value: number };
      weighted_mer: Availability & { weighted_mer: number | null };
    };
  };
  activity: {
    turnover: Availability & { turnover_ratio: number; method: string };
    average_holding_period: {
      open: Availability & { days: number | null };
      closed: Availability & { days: number | null };
    };
  };
  realized_gains: {
    source: string;
    by_ticker: Record<string, number>;
    events: {
      event_date: string;
      ticker_symbol: string;
      realized_gain_cad: number;
      /** 'reported' | 'derived_from_quantity_and_price' | 'missing' -- see config.AMOUNT_QUALITY_*. */
      amount_quality: string;
    }[];
    total_realized_gain: number;
  };
  data_quality: {
    flags: DataQualityFlag[];
    excluded_negative_positions: unknown[];
    counts_by_code: Record<string, number>;
  };
  unavailable_metrics: { metric: string; reason: string }[];
}

export interface DataQualityFlag {
  code: string;
  ticker_symbol: string | null;
  detail: string;
  severity: string;
}

export interface ReportEnvelope {
  generated_at: string;
  database_mtime: string;
  report: Report;
}

export interface ClassificationFields {
  sector?: string | null;
  industry?: string | null;
  expense_ratio?: number | null;
  aum?: number | null;
  etf_category?: string | null;
  fund_family?: string | null;
  market_cap?: number | null;
  dividend_yield?: number | null;
  user_thesis?: string | null;
  target_weight_percent?: number | null;
  account_type?: string | null;
  first_purchase_date?: string | null;
  latest_purchase_date?: string | null;
  number_of_buys?: number | null;
  number_of_sells?: number | null;
  dividends_received?: number | null;
  sector_weights?: Record<string, number> | null;
  [key: string]: unknown;
}

export interface ClassificationDetail {
  ticker_id: number;
  ticker_symbol: string;
  security_name: string;
  security_type: SecurityType;
  primary_group: string;
  secondary_tags: string[];
  confidence: string | null;
  reasoning: string | null;
  evidence_used: string[];
  missing_data: string[];
  review_needed: boolean;
  fields: ClassificationFields;
  field_provenance: Record<string, string>;
  generated_at: string | null;
}

export interface Classifications {
  generated_at: string | null;
  count: number;
  review_count: number;
  classifications: ClassificationDetail[];
}

export interface PricePoint {
  date: string;
  close: number;
  adjusted_close: number;
  volume: number;
}

export interface PriceHistory {
  ticker_id: number;
  ticker_symbol: string;
  security_name: string;
  currency: string;
  count: number;
  points: PricePoint[];
}

export interface OverlapEtf {
  ticker_symbol: string;
  security_name: string;
  market_value: number;
  sleeve_weight: number;
  /** Share of the fund its reported top holdings cover (rest is unreported). */
  reported_weight: number;
  holdings_count: number;
}

export interface OverlapPair {
  a: string;
  b: string;
  overlap_pct: number;
  shared: { name: string; a_pct: number; b_pct: number }[];
}

export interface SharedHolding {
  name: string;
  etfs: string[];
  combined_weight: number;
}

export interface EtfOverlap {
  available: boolean;
  reason?: string;
  basis: string;
  caveat?: string;
  etf_count: number;
  compared_count?: number;
  etfs: OverlapEtf[];
  share: {
    overlapping_weight: number;
    unique_weight: number;
    unreported_weight: number;
  };
  pairs: OverlapPair[];
  top_shared_holdings: SharedHolding[];
}

export type JobStatus = "queued" | "running" | "succeeded" | "failed";

export interface Job {
  id: string;
  kind: string;
  status: JobStatus;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  detail: string | null;
  result: unknown;
}

export interface ActionsState {
  active: Job | null;
  jobs: Job[];
}

export interface PendingTickerMapping {
  canonical_symbol: string;
  provider_symbol: string;
  currency: string;
  exchange: string | null;
}

export interface PendingTicker {
  source_symbol: string;
  detected_currency: string | null;
  trade_count: number;
  first_seen: string | null;
  last_seen: string | null;
  sources: string[];
  already_mapped: boolean;
  mapping: PendingTickerMapping | null;
}

export interface Health {
  status: string;
  database: string;
  database_exists: boolean;
}

export type HistoryRange = "1m" | "3m" | "6m" | "1y" | "3y" | "max";

/** Matches config.CORRELATION_WINDOWS' keys on the API. */
export type CorrelationWindow = "3m" | "1y" | "3y";

export interface PortfolioRiskFromCovariance {
  available: boolean;
  reason?: string;
  portfolio_volatility: number | null;
  covered_weight: number | null;
  risk_contribution: Record<string, number>;
}

export interface CorrelationMatrix {
  available: boolean;
  reason?: string;
  window_days: number;
  observations: number;
  start_date: string | null;
  end_date: string | null;
  tickers: string[];
  unavailable_tickers: string[];
  correlation: Record<string, Record<string, number>>;
  covariance: Record<string, Record<string, number>>;
  /** Present only when `available`; computed server-side from `covariance` and current weights. */
  portfolio_risk?: PortfolioRiskFromCovariance;
}

export interface GroupCorrelationMatrix extends CorrelationMatrix {
  unavailable_groups: string[];
  unclassified_weight: number;
  group_metadata: Record<
    string,
    {
      portfolio_weight: number;
      constituent_coverage: number;
      constituents: string[];
      unavailable_constituents: string[];
    }
  >;
}

export interface UpcomingDividend {
  ticker_id: number;
  ticker_symbol: string;
  ex_dividend_date: string;
  pay_date: string | null;
  declared_amount: number;
  frequency: string | null;
  quantity: number;
  currency: string;
  expected_cash: number;
}

export interface UpcomingEarnings {
  ticker_id: number;
  ticker_symbol: string;
  report_date: string;
  period: string | null;
  eps_estimate: number | null;
  weight: number;
}

export interface WishlistDeclaration {
  declared_status: string;
  rationale: string | null;
  declared_at: string | null;
  declared_by: string | null;
}

export interface WishlistClassification {
  primary_group: string;
  confidence: string | null;
}

export interface WishlistThesis {
  fundamental_rating: string | null;
  valuation_stance: string | null;
  thesis_direction: string | null;
  thesis_confidence: string | null;
  analysis_horizon: string | null;
  as_of: string | null;
  generated_at: string | null;
}

export interface WishlistPolicyCheck {
  name: string;
  result: string;
  detail: string;
}

export interface WishlistDecision {
  proposed_action: string | null;
  /** True when `proposed_action` uses the owned PortfolioAction vocabulary
   * (Buy/Hold/Trim/Sell/Add) instead of the required WishlistAction
   * vocabulary (Buy/Watch/Wait/Pass) for a not-currently-held security. */
  action_vocabulary_mismatch: boolean;
  confidence: string | null;
  summary: string | null;
  generated_at: string | null;
  failing_policy_checks: WishlistPolicyCheck[];
}

export interface WishlistEntry {
  ticker: string;
  ticker_id: number;
  company_name: string | null;
  declaration: WishlistDeclaration;
  classification: WishlistClassification | null;
  thesis: WishlistThesis | null;
  decision: WishlistDecision | null;
}

export interface WishlistOverview {
  generated_at: string;
  count: number;
  wishlist: WishlistEntry[];
}

export interface UpcomingIncome {
  horizon_days: number;
  dividends: UpcomingDividend[];
  earnings: UpcomingEarnings[];
  totals_by_currency: Record<string, number>;
  synced_at: { dividends: string | null; earnings: string | null };
}
