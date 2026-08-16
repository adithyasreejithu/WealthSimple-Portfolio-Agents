"""The investment worksheet builder -- Phase 3 of the Investment Analyst rebuild.

Turns one run's already-registered `investment-analyst-resources` bundle
(plus, if present, a `security-technicals` artifact) into the compact,
evidence-linked worksheet the not-yet-built Phase 4 analyst will read. A
deterministic Python module/stage, not a Claude skill -- see
`docs/plans/combined-investment-analyst-plan/00-overview.md` §3.2.

Two layers, mirroring `security_technicals_cli.py`'s split between pure
computation and its I/O shell:

- `build_analysis_scope` / `build_worksheet` -- pure functions, no
  filesystem access, fully deterministic given their inputs.
- `build_worksheet_for_run` -- the I/O wrapper: locates this run's
  registered bundle/technicals evidence, reads them, calls `build_worksheet`,
  writes the worksheet JSON and a compact analyst-context Markdown, registers
  both as evidence, and appends a `worksheet_built` audit event.

**This module never fetches and never reads the database.** Its documented
inputs (`00-overview.md` §5.7) are what a run already holds: the resource
bundle, the run's other registered evidence, and (optionally) prior-thesis
and market-context summaries supplied by a caller. If a run has no
`security-technicals` artifact yet, that is reported as an explicit gap, not
computed here -- see `docs/plans/implementation/phase-3/design-decisions.md`.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import audit as audit_module
from . import evidence as evidence_module
from . import financial_metrics
from . import scenarios as scenarios_module
from . import valuation as valuation_module
from .analysis_models import (
    ETF_NOT_APPLICABLE_SECTIONS,
    TRACE_BLOCKING_THRESHOLD,
    TRACE_WARNING_THRESHOLD,
    AnalysisScope,
    EvidenceCompleteness,
    get_mode_section_map,
)
from .paths import PathEscapeError, WorkspaceError, relative_to_run, resolve_in_run, utc_now_iso

WORKSHEET_SCHEMA = "investment-worksheet.v1"

# Must match `security_technicals_cli.SCHEMA` -- duplicated rather than
# imported because `src/` never imports `.claude/` (see CLAUDE.md).
TECHNICALS_SCHEMA = "security-technicals.v1"

# Calibrated against the PLTR fixture's rendered context
# (tests/fixtures/investment_analyst/); see design-decisions.md.
CONTEXT_SIZE_CEILING_CHARS = 12_000

# Maps analysis-scope.v1 evidence-domain names to how they are actually
# graded inside the investment-analyst-resources bundle's own TRACE record.
# ("domain", name): a top-level TRACE domain in bundle["trace"]["domains"].
# ("live_field", group): one of the LIVE_TOP_UP_GROUPS, graded as a single
#   ok/missing/not_applicable membership test inside the "live" domain --
#   that domain does not carry per-field detail for these groups.
_DOMAIN_TRACE_MAP: dict[str, tuple[str, str]] = {
    "position": ("domain", "position"),
    "ledger": ("domain", "ledger_summary"),
    "prices": ("domain", "prices"),
    "financials": ("domain", "financials"),
    "earnings": ("domain", "earnings"),
    "dividends": ("domain", "dividends"),
    "classification": ("domain", "classification"),
    "portfolio_context": ("domain", "portfolio_context"),
    "overview": ("live_field", "overview"),
    "valuation": ("live_field", "valuation"),
    "analyst": ("live_field", "analyst"),
    "options": ("live_field", "options"),
    "news": ("live_field", "news"),
    "insider": ("live_field", "insider"),
    "institutional": ("live_field", "institutional"),
    "funds": ("live_field", "funds"),
}
# Domains with no producer at all this phase (Phase 6/10). Always "missing"
# per thesis_validation.confidence_cap's rule that an unknown required
# domain caps confidence exactly like one TRACE already graded missing.
_UNBUILT_DOMAINS = frozenset({"market_context", "company_filings"})

_OPTIONS_FIELDS = (
    "put_call_oi_ratio", "put_call_volume_ratio", "atm_iv_near",
    "atm_iv_far", "iv_skew", "max_oi_call_strike", "max_oi_put_strike",
)


# --- scope mapper ----------------------------------------------------------


def build_analysis_scope(
    *,
    run_id: str,
    subject: str,
    asset_track: str,
    mode: str,
    trigger: dict[str, Any],
    decision_horizon: str,
) -> AnalysisScope:
    """Deterministic `analysis-scope.v1` from `mode` alone, per
    `investment-analysis-policy.yml`'s `mode_section_map`
    (`analysis_scope_schema.md` §3: "an agent never hand-picks these
    lists"). ETF sections in `etf_not_applicable_sections` are pulled out of
    whichever bucket the mode's policy entry put them in and moved to
    `not_applicable_sections`, keeping the 16-section partition intact."""
    spec = get_mode_section_map().get(mode)
    if spec is None:
        raise ValueError(f"unknown analysis mode for scope mapping: {mode!r}")

    not_applicable = list(ETF_NOT_APPLICABLE_SECTIONS) if asset_track == "etf" else []
    evaluate = [s for s in (spec.get("evaluate_sections") or []) if s not in not_applicable]
    preserve = [s for s in (spec.get("preserve_sections") or []) if s not in not_applicable]

    payload = {
        "schema": "analysis-scope.v1",
        "run_id": run_id,
        "subject": subject,
        "asset_track": asset_track,
        "mode": mode,
        "trigger": trigger,
        "decision_horizon": decision_horizon,
        "evaluate_sections": evaluate,
        "preserve_sections": preserve,
        "not_applicable_sections": not_applicable,
        "required_evidence_domains": list(spec.get("required_evidence_domains") or []),
        "optional_evidence_domains": list(spec.get("optional_evidence_domains") or []),
        "critical_gap_policy": spec.get("critical_gap_policy", "stop"),
    }
    return AnalysisScope.model_validate(payload)


# --- evidence health ---------------------------------------------------


def _domain_status(trace: dict[str, Any], domain_name: str) -> EvidenceCompleteness:
    if domain_name in _UNBUILT_DOMAINS:
        return "missing"
    mapping = _DOMAIN_TRACE_MAP.get(domain_name)
    if mapping is None:
        return "missing"
    kind, name = mapping
    domains = (trace or {}).get("domains") or {}
    if kind == "domain":
        entry = domains.get(name)
        if entry is None:
            return "missing"
        if entry.get("missing"):
            return "missing"
        if entry.get("ok"):
            return "ok"
        return "not_applicable"
    live = domains.get("live") or {}
    if name in (live.get("ok") or []):
        return "ok"
    if name in (live.get("missing") or []):
        return "missing"
    if name in (live.get("not_applicable") or []):
        return "not_applicable"
    return "missing"


def _build_evidence_health(scope: AnalysisScope, bundle_trace: dict[str, Any]) -> dict[str, Any]:
    """Completeness **scoped to this run's `required_evidence_domains`**
    (`investment-analysis-policy.yml`'s `trace_policy.scope`), not the
    bundle's own overall `completeness_pct` -- a run requiring only
    `[financials, earnings, valuation]` is judged on those, not on whether
    an optional domain came back empty. Domain-level, not field-level: the
    `live_field` domains only carry group-level grading in the bundle trace
    to begin with, so a field-level percentage would be false precision.
    """
    required = scope.required_evidence_domains
    optional = scope.optional_evidence_domains
    domain_status = {name: _domain_status(bundle_trace, name) for name in (*required, *optional)}

    graded = [domain_status[name] for name in required if domain_status[name] != "not_applicable"]
    ok_count = sum(1 for status in graded if status == "ok")
    scoped_completeness_pct = round(100.0 * ok_count / len(graded), 1) if graded else 100.0

    failed_domains = [name for name in required if domain_status[name] == "missing"]
    below_threshold = scoped_completeness_pct < TRACE_BLOCKING_THRESHOLD
    blocking = scope.critical_gap_policy == "stop" and (below_threshold or bool(failed_domains))

    blocking_reasons: list[str] = []
    if blocking:
        if below_threshold:
            blocking_reasons.append(
                f"scoped completeness {scoped_completeness_pct}% below blocking threshold "
                f"{TRACE_BLOCKING_THRESHOLD}%"
            )
        blocking_reasons.extend(f"required evidence domain failed: {name}" for name in failed_domains)

    return {
        "scoped_completeness_pct": scoped_completeness_pct,
        "warning": scoped_completeness_pct < TRACE_WARNING_THRESHOLD,
        "domain_status": domain_status,
        "blocking": blocking,
        "blocking_reasons": blocking_reasons,
        "bundle_completeness_pct": bundle_trace.get("completeness_pct"),
    }


# --- block builders ------------------------------------------------------


def _build_company_profile(db: dict[str, Any], live: dict[str, Any], asset_track: str) -> dict[str, Any]:
    overview = live.get("overview") or {}
    if asset_track == "etf":
        return {"etf_details": db.get("etf_details"), "overview": overview}
    return {"stock_details": db.get("stock_details"), "overview": overview}


def _build_price_and_market_context(
    db: dict[str, Any], quote: dict[str, Any] | None, technicals: dict[str, Any] | None
) -> dict[str, Any]:
    prices = db.get("prices") or {}
    block: dict[str, Any] = {
        "latest_close": prices.get("latest_close"),
        "period_return_pct": prices.get("period_return_pct"),
        "week52_low": prices.get("week52_low"),
        "week52_high": prices.get("week52_high"),
        "count": prices.get("count"),
        "start": prices.get("start"),
        "end": prices.get("end"),
        "quote": quote,
    }
    if technicals is not None:
        block["technicals"] = technicals.get("technicals")
        block["technicals_benchmark"] = technicals.get("benchmark")
        block["technicals_gap"] = None
    else:
        block["technicals"] = None
        block["technicals_benchmark"] = None
        block["technicals_gap"] = "no security-technicals artifact registered in this run"
    return block


def _build_expectations(db: dict[str, Any], derived: dict[str, Any]) -> dict[str, Any]:
    earnings = db.get("earnings") or []
    return {
        "last_reported": earnings[-1] if earnings else None,
        "event_count": len(earnings),
        "upgrades_90d": derived.get("upgrades_90d"),
        "downgrades_90d": derived.get("downgrades_90d"),
        "net_revisions_365d": derived.get("net_revisions_365d"),
        "capability_label": "current_snapshot_only",
    }


def _build_options(derived: dict[str, Any]) -> dict[str, Any]:
    block: dict[str, Any] = {field_name: derived.get(field_name) for field_name in _OPTIONS_FIELDS}
    block["capability_label"] = "snapshot_only"
    return block


def _build_ownership(live: dict[str, Any], derived: dict[str, Any]) -> dict[str, Any]:
    return {
        "insider": live.get("insider") or {},
        "institutional": live.get("institutional") or {},
        "net_insider_shares": derived.get("net_insider_shares"),
        "capability_label": "current_snapshot_only",
    }


# --- worksheet assembly --------------------------------------------------


def build_worksheet(
    *,
    run_id: str,
    scope: AnalysisScope,
    bundle: dict[str, Any],
    bundle_evidence_id: str | None = None,
    technicals: dict[str, Any] | None = None,
    technicals_evidence_id: str | None = None,
    prior_thesis: dict[str, Any] | None = None,
    market_context: dict[str, Any] | None = None,
    as_of: str | None = None,
) -> dict[str, Any]:
    """Pure transform: already-loaded bundle/technicals dicts in, worksheet
    dict out. No filesystem or network access -- see `build_worksheet_for_run`
    for the I/O wrapper. Deterministic: identical inputs (including a fixed
    `as_of`) produce byte-identical JSON."""
    bundle = bundle or {}
    db = bundle.get("db") or {}
    live = bundle.get("live") or {}
    derived = bundle.get("derived") or {}
    quote = bundle.get("quote")
    bundle_trace = bundle.get("trace") or {}

    evidence_health = _build_evidence_health(scope, bundle_trace)
    financial_trajectory = financial_metrics.build_financial_trajectory(db.get("financials"), derived)

    live_valuation = live.get("valuation") or {}
    # The bundle carries no listing-currency field of its own (`stock_details`
    # is sector/industry only) -- USD is the correct default for every
    # US-listed name this phase's fixtures/tests cover; a TSX-listed holding
    # would need this threaded through from `db_resources.resolve_ticker`,
    # left for whichever phase first exercises one.
    valuation_methods = valuation_module.build_valuation_methods(live_valuation, derived, currency="USD")

    current_price = None
    if quote and not quote.get("skipped"):
        current_price = quote.get("price")
    if current_price is None:
        current_price = (db.get("prices") or {}).get("latest_close")
    scenario_templates = scenarios_module.build_scenario_templates(
        valuation_methods, current_price, scope.decision_horizon
    )

    price_and_market_context = _build_price_and_market_context(db, quote, technicals)
    expectations = _build_expectations(db, derived)
    options = _build_options(derived)
    ownership = _build_ownership(live, derived)
    company_profile = _build_company_profile(db, live, scope.asset_track)
    portfolio_context = db.get("portfolio_context") or {"held": False}

    unknowns: list[str] = [
        f"evidence domain missing: {name}"
        for name, status in evidence_health["domain_status"].items()
        if status == "missing"
    ]
    if price_and_market_context["technicals_gap"]:
        unknowns.append(price_and_market_context["technicals_gap"])
    if prior_thesis is None:
        unknowns.append("no prior thesis loaded this run (loader not built until Phase 5/7)")
    if market_context is None:
        unknowns.append("no market context loaded this run (Market Researcher not built until Phase 10)")
    if not valuation_methods:
        unknowns.append("no valuation method could be computed from available inputs")
    if scenario_templates is None:
        unknowns.append(
            "no scenario templates could be scaffolded "
            "(requires at least one valuation method and a current price)"
        )

    return {
        "schema": WORKSHEET_SCHEMA,
        "run_id": run_id,
        "as_of": as_of or bundle.get("as_of"),
        "identity": {
            "ticker": bundle.get("ticker"),
            "provider_symbol": bundle.get("provider_symbol"),
            "asset_track": scope.asset_track,
        },
        "request_and_scope": scope.model_dump(mode="json", by_alias=True),
        "evidence_health": evidence_health,
        "company_profile": company_profile,
        "financial_history_and_metrics": financial_trajectory,
        "price_and_market_context": price_and_market_context,
        "expectations_and_results": expectations,
        "options_and_positioning": options,
        "ownership_and_capital_allocation": ownership,
        "valuation_methods": valuation_methods,
        "scenario_inputs": scenario_templates,
        "market_context": market_context or {"available": False},
        "prior_thesis": prior_thesis or {"exists": False},
        "portfolio_context_informational": portfolio_context,
        "section_requirements": {
            "evaluate_sections": scope.evaluate_sections,
            "preserve_sections": scope.preserve_sections,
            "not_applicable_sections": scope.not_applicable_sections,
        },
        "known_conflicts": [],
        "unknowns": unknowns,
        "source_evidence": {
            "bundle_evidence_id": bundle_evidence_id,
            "technicals_evidence_id": technicals_evidence_id,
        },
    }


# --- compact analyst context ----------------------------------------------


def render_analyst_context(worksheet: dict[str, Any]) -> str:
    """Short bullet summaries per block -- never a raw list/series, which
    stays only in the sibling `.json`. See `CONTEXT_SIZE_CEILING_CHARS`."""
    identity = worksheet["identity"]
    lines = [f"# {identity['ticker']} -- investment worksheet context ({worksheet.get('as_of')})", ""]

    eh = worksheet["evidence_health"]
    lines.append("## Evidence health")
    lines.append(f"- Scoped completeness: {eh['scoped_completeness_pct']}% (bundle overall: {eh.get('bundle_completeness_pct')}%)")
    reason_suffix = f" -- {'; '.join(eh['blocking_reasons'])}" if eh["blocking_reasons"] else ""
    lines.append(f"- Blocking: {eh['blocking']}{reason_suffix}")
    for name, status in eh["domain_status"].items():
        lines.append(f"  - {name}: {status}")
    lines.append("")

    lines.append("## Company profile")
    stock_details = worksheet["company_profile"].get("stock_details")
    if stock_details:
        lines.append(f"- Sector: {stock_details.get('sector')} / {stock_details.get('industry')}")
    else:
        lines.append("- (ETF or no sector/industry on file)")
    lines.append("")

    ft = worksheet["financial_history_and_metrics"]
    lines.append("## Financial trajectory (quarterly)")
    lines.append(f"- {ft['period_count']} periods on file, latest period end {ft['latest_period_end']}")
    latest_period = ft["periods"][-1] if ft["periods"] else None
    if latest_period:
        lines.append(f"- Latest revenue QoQ: {latest_period.get('revenue_qoq_pct')}")
    for name, value in ft["ratios_ref"].items():
        lines.append(f"  - {name}: {value}")
    lines.append("")

    pm = worksheet["price_and_market_context"]
    lines.append("## Price and market context")
    lines.append(f"- Latest close: {pm.get('latest_close')}, period return: {pm.get('period_return_pct')}%")
    if pm.get("technicals"):
        moving_averages = pm["technicals"].get("moving_averages") or {}
        beta_alpha = pm["technicals"].get("beta_alpha") or {}
        lines.append(f"  - SMA50/200: {moving_averages.get('sma_50d')} / {moving_averages.get('sma_200d')}")
        lines.append(f"  - Beta vs {pm.get('technicals_benchmark')}: {beta_alpha.get('beta') if beta_alpha else None}")
    else:
        lines.append(f"  - technicals: gap -- {pm.get('technicals_gap')}")
    lines.append("")

    lines.append("## Valuation methods")
    for method in worksheet["valuation_methods"]:
        value_range = method["resulting_equity_value_per_share"]
        lines.append(
            f"- {method['method']}: low {value_range['low']} / mid {value_range['mid']} / "
            f"high {value_range['high']} {method['currency']}"
        )
    if not worksheet["valuation_methods"]:
        lines.append("- none computed (see unknowns)")
    lines.append("")

    lines.append("## Scenarios")
    scenario_templates = worksheet["scenario_inputs"]
    if scenario_templates:
        for name in ("bull", "base", "bear"):
            scenario = scenario_templates[name]
            lines.append(
                f"- {name}: fair value {scenario['fair_value_per_share']}, "
                f"expected return {scenario['expected_return_pct']:.1%}, p={scenario['probability']}"
            )
    else:
        lines.append("- none scaffolded (see unknowns)")
    lines.append("")

    lines.append("## Unknowns")
    if worksheet["unknowns"]:
        lines.extend(f"- {item}" for item in worksheet["unknowns"])
    else:
        lines.append("- none")

    return "\n".join(lines) + "\n"


# --- I/O wrapper -----------------------------------------------------------


def _find_latest_evidence(run_dir: Path, evidence_type: str, ticker: str) -> dict[str, Any] | None:
    """Most recent registered evidence of `evidence_type` for `ticker`.

    `EvidenceRecord` carries no `ticker` field, so this matches on the
    ticker appearing in `artifact_path` -- true for every producer this
    module reads from (`<TICKER>-<date>-resources.json`,
    `<TICKER>-<date>-security-technicals.json`). `evidence_type` values are
    now distinct per producer (`market_data_bundle`, `security_technicals`,
    `security_status`, `policy_worksheet`) -- before this was split, three
    producers shared `derived_calculation`, and this filter alone could not
    tell them apart (a `security-status`/`policy_worksheet` artifact would be
    mistaken for technicals whenever it was registered more recently; see
    the schema check in `build_worksheet_for_run`, which is the remaining
    defense against a fourth producer ever reusing a type by mistake)."""
    candidates = [
        record
        for record in evidence_module.read_records(run_dir)
        if record.get("evidence_type") == evidence_type and ticker in (record.get("artifact_path") or "")
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda record: record.get("retrieved_at") or "")
    return candidates[-1]


def _resolve_cached_payload(run_dir: Path, value: dict[str, Any] | None) -> dict[str, Any] | None:
    """Resolve an `investment-analyst-resources` cache pointer back into the
    full payload it points at.

    `investment_analyst_resources.build_bundle` replaces the bulky raw
    `db`/`live` payloads with `{"cached": true, "cache_path": "cache/..."}`
    rather than embedding them in the persisted bundle (see
    `workspace/cache.py`). `build_worksheet` still needs the full payload, so
    the pointer is resolved here, in the I/O wrapper -- never inside
    `build_worksheet` itself, which stays a pure function over already-loaded
    dicts, unaware that the bundle it is handed might have come from a cache
    lookup. A cache file that no longer exists (already purged, or never
    written -- direct callers of `build_worksheet` still pass a plain dict)
    resolves to an empty payload, which `build_worksheet` already reports as
    gaps, rather than raising: this module never invents data, but it also
    does not crash on data that is legitimately gone.
    """
    if not value or not value.get("cached"):
        return value
    cache_path = value.get("cache_path")
    if not cache_path:
        return {}
    try:
        resolved = resolve_in_run(run_dir, cache_path)
        if not resolved.is_file():
            return {}
        return json.loads(resolved.read_text(encoding="utf-8"))
    except (PathEscapeError, OSError, json.JSONDecodeError):
        return {}


def build_worksheet_for_run(
    run_dir: Path,
    *,
    run_id: str,
    ticker: str,
    scope: AnalysisScope,
    prior_thesis: dict[str, Any] | None = None,
    market_context: dict[str, Any] | None = None,
    as_of: str | None = None,
    now: datetime | None = None,
) -> tuple[dict[str, Any], Path, str]:
    """Locate this run's registered bundle (and, if present, technicals)
    evidence for `ticker`, build the worksheet, write it and a compact
    `analyst-context.md`, register both as evidence, and append a
    `worksheet_built` audit event. Returns `(worksheet, worksheet_path,
    sha256_hash)` -- the hash is what a future `investment-thesis.v1`
    artifact's `worksheet_ref.hash` will cite.

    Raises `WorkspaceError` if no `market_data_bundle` evidence exists for
    `ticker` in this run: this module transforms an already-collected
    resource pull, it does not perform one."""
    bundle_record = _find_latest_evidence(run_dir, "market_data_bundle", ticker)
    if bundle_record is None or not bundle_record.get("artifact_path"):
        raise WorkspaceError(
            f"no market_data_bundle evidence registered for {ticker!r} in this run "
            "-- run investment-analyst-resources first"
        )
    bundle_path = resolve_in_run(run_dir, bundle_record["artifact_path"])
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    bundle["db"] = _resolve_cached_payload(run_dir, bundle.get("db"))
    bundle["live"] = _resolve_cached_payload(run_dir, bundle.get("live"))

    technicals_record = _find_latest_evidence(run_dir, "security_technicals", ticker)
    technicals: dict[str, Any] | None = None
    if technicals_record and technicals_record.get("artifact_path"):
        technicals_path = resolve_in_run(run_dir, technicals_record["artifact_path"])
        if technicals_path.is_file():
            technicals = json.loads(technicals_path.read_text(encoding="utf-8"))
            # A missing technicals artifact is a declared gap (the `else`
            # branch of `_build_price_and_market_context` below); an artifact
            # that exists but isn't actually a security-technicals document
            # must fail loudly instead of silently presenting as
            # present-but-empty -- that silent case is exactly what let a
            # `security-status`/`policy_worksheet` artifact get mistaken for
            # technicals before `evidence_type` was split per-producer.
            if technicals.get("schema") != TECHNICALS_SCHEMA:
                raise WorkspaceError(
                    f"evidence {technicals_record.get('evidence_id')!r} "
                    f"({technicals_record['artifact_path']}) is registered as "
                    f"security_technicals for {ticker!r} but its schema is "
                    f"{technicals.get('schema')!r}, not {TECHNICALS_SCHEMA!r}"
                )
        else:
            technicals_record = None

    worksheet = build_worksheet(
        run_id=run_id,
        scope=scope,
        bundle=bundle,
        bundle_evidence_id=bundle_record.get("evidence_id"),
        technicals=technicals,
        technicals_evidence_id=technicals_record.get("evidence_id") if technicals_record else None,
        prior_thesis=prior_thesis,
        market_context=market_context,
        as_of=as_of,
    )

    moment = now or datetime.now(timezone.utc)
    stamp = moment.strftime("%Y-%m-%dT%H%M%SZ")
    calculations_dir = run_dir / "calculations"
    calculations_dir.mkdir(parents=True, exist_ok=True)
    worksheet_path = calculations_dir / f"{ticker}-{stamp}-worksheet.json"
    context_path = calculations_dir / f"{ticker}-{stamp}-analyst-context.md"

    worksheet_json = json.dumps(worksheet, indent=2, sort_keys=False, ensure_ascii=False)
    # `write_bytes`, not `write_text`: on Windows, `write_text` silently
    # translates `\n` -> `\r\n`, which would make the sha256 computed here
    # from the in-memory string not match the bytes actually on disk --
    # exactly the mismatch `thesis_validation.check_worksheet_ref` (Phase 4)
    # exists to catch. Writing the exact encoded bytes keeps the two in sync
    # on every platform.
    worksheet_path.write_bytes(worksheet_json.encode("utf-8"))
    context_path.write_bytes(render_analyst_context(worksheet).encode("utf-8"))
    worksheet_hash = "sha256:" + hashlib.sha256(worksheet_json.encode("utf-8")).hexdigest()

    evidence_module.register(
        run_dir, run_id=run_id, evidence_type="worksheet", source_name="investment_worksheet",
        status="available", artifact=worksheet_path, retrieved_at=utc_now_iso(),
        collection_method="deterministic_transform",
    )
    evidence_module.register(
        run_dir, run_id=run_id, evidence_type="analyst_context", source_name="investment_worksheet",
        status="available", artifact=context_path, retrieved_at=utc_now_iso(),
        collection_method="deterministic_transform",
    )
    audit_module.append_event(
        run_dir, run_id=run_id, event="worksheet_built", actor="investment_worksheet",
        artifact=relative_to_run(run_dir, worksheet_path),
        details={
            "ticker": ticker,
            "scoped_completeness_pct": worksheet["evidence_health"]["scoped_completeness_pct"],
            "blocking": worksheet["evidence_health"]["blocking"],
        },
    )
    _emit_worksheet_trace(
        run_dir, run_id=run_id, ticker=ticker, technicals_record=technicals_record,
        prior_thesis=prior_thesis, market_context=market_context,
    )

    return worksheet, worksheet_path, worksheet_hash


def _emit_worksheet_trace(
    run_dir: Path, *, run_id: str, ticker: str, technicals_record: dict[str, Any] | None,
    prior_thesis: dict[str, Any] | None, market_context: dict[str, Any] | None,
) -> None:
    """Record, at worksheet-build time, whether the three optional inputs a
    worksheet can carry were present or absent -- this is what would have
    surfaced the post-mortem's F1 (technicals silently mistaken for a
    different artifact) and F2 (`security-technicals` never invoked) on the
    very first run, instead of three runs later. `prior_thesis`/
    `market_context` grade `not_applicable` rather than `missing`: no loader
    for either exists yet (Phase 5/7 and Phase 10 respectively), so their
    absence is not yet an actionable gap for any caller to fix.

    Best-effort, matching every other trace emitter in this codebase: a
    logging failure must never sink a worksheet that otherwise built
    successfully."""
    try:
        import skill_trace

        optional_context = {"prior_thesis": prior_thesis, "market_context": market_context}
        trace = skill_trace.Trace(skill="investment_worksheet", subject=ticker, kind="worksheet")
        trace.add(
            "optional_inputs",
            ok=(["technicals"] if technicals_record else [])
            + [name for name, value in optional_context.items() if value is not None],
            missing=[] if technicals_record else ["technicals"],
            not_applicable=[name for name, value in optional_context.items() if value is None],
        )
        skill_trace.emit(trace, run_dir=run_dir, run_id=run_id)
    except Exception:  # noqa: BLE001 -- never sink a successful worksheet build
        pass
