# Plan: `trade-planner` agent — turns a Buy/Sell/Hold verdict into an executable price plan

## Context

This session walked through a real decision on PLTR: the existing `stock-analyst`
agent already produces a rubric-driven Buy/Sell/Hold/Trim/Add/Watchlist/Avoid
verdict (`Knowledge-Base/taxonomy/decision-rubric.yml`), but nothing in the
pipeline turns that verdict into an actual order — what price to set as a limit,
what price to set as a stop, what order type Wealthsimple supports, or how many
shares to trade. That gap was worked around by hand this session (and hit a real
bug along the way: a raw query read `position_ledger.running_book_cad` instead of
the market-currency column, producing a wrong average cost — $149.99 CAD/share
computed vs. the user's real $105.50 USD/share cost basis from Wealthsimple).

`docs/project/portfolio-manager-gap-analysis.md` §3.7 independently documents this
same gap: no part of the system prices a trade or sizes a position today. The user
wants a new agent whose job is specifically to (1) consume an existing buy/sell
decision and (2) dictate the price levels — entry/limit, stop, and size — needed
to actually place the order, using every signal available (volatility, options
IV, analyst targets) rather than one fixed formula. This is agreed to be a first
iteration ("we will build more onto this"), so the scope below is deliberately
narrow: one new agent, one new skill, one new hand-curated policy file, no writes
back into `Knowledge-Base/` yet.

## Design decisions confirmed with the user

- **Builds on `stock-analyst`, does not re-decide.** The new agent consumes the
  existing `stock-recommendation.v1` artifact's `proposed.action` /
  `weighted_score` / `confidence`. It never scores gates/dimensions itself and
  never overrides the verdict — same non-negotiable rule `stock-analyst` follows
  for the rubric.
- **Covers both held positions and prospective new buys.** For a held ticker it
  needs real cost basis (stop-loss and trim sizing depend on it); for a
  not-yet-owned ticker there is no cost basis, so it computes an entry/limit price
  and a stop relative to that entry instead.
- **Uses every available pricing signal, not one method.** A deterministic script
  computes multiple *candidate* stop and limit/target prices — volatility-based
  (ATR from OHLCV, already in `historical_records`), options-IV-based (already
  fetched by `fetch-stock-research-data`), fixed-percentage bands, and
  analyst-target-anchored — and a hand-curated policy file decides how they're
  weighted/selected per situation. The agent's one judgment step is to pick/blend
  among the already-computed candidates and justify the choice; it never invents
  a number freehand. This mirrors the repo's core rule for `stock-analyst`: "the
  LLM never predicts markets... deterministic scripts recompute the arithmetic."

## Architecture

```
stock-analyst (existing)                    trade-planner (new)
  writes                                       reads
  exports/stock-recommendations/    ─────►    exports/stock-recommendations/<T>-<date>.json
  <TICKER>-<date>.json                              +
                                              analytics.get_position() / get_holdings()
                                              analytics.get_price_history()
                                              fetch-stock-research-data's options/analyst groups
                                                    │
                                                    ▼
                                        plan-trade-price skill:
                                        price_levels.py (deterministic candidates)
                                              │
                                              ▼  (one judgment step: select/blend)
                                        exports/trade-plans/<TICKER>-<date>.json
                                        (schema: trade-plan.v1)
```

No write path into `Knowledge-Base/` in this phase — the artifact is the
terminal output, same as `stock-analyst`'s artifact is terminal until a
`kb-intake` call is separately requested. A KB-writeback (e.g. stamping
`Target Price` / `Stop Price` into the thesis page's `## Status` block) is a
natural follow-on and was confirmed safe to add later without breaking existing
parsers (`kb_pages.parse_status_block` only recognizes a fixed key set and
silently ignores unrecognized `- Key: value` lines) — but is explicitly deferred,
not built now.

## New pieces to build

### 1. `Knowledge-Base/taxonomy/execution-policy.yml` (new, hand-curated)

Follows the same discipline `decision-rubric.yml` uses per `CLAUDE.md`: **every
tunable number lives here**, never hardcoded in a script. Structure, mirroring
`decision-rubric.yml`'s shape:

- `sources:` registry — same `yfinance`/`derived` groups already used by
  `decision-rubric.yml`, plus new `derived` metrics this plan introduces:
  `atr_14d`, `atr_multiple_stop`, `iv_implied_move`.
- `stop_methods:` — one block per candidate method (`atr_multiple`, `iv_implied`,
  `fixed_pct`), each with its tunable constant(s) (e.g. ATR multiple = 2.0,
  fixed-pct stop = 10%) and an `applies_when` (e.g. `iv_implied` only applies when
  options data resolved; risk-tag-aware fixed_pct bands for `High Volatility`
  vs. default).
- `target_methods:` — `analyst_target_anchored` (blend weight vs. `weighted_score`
  strength) and `fixed_pct`, same shape.
- `selection_policy:` — how candidates combine into one chosen stop/target (e.g.
  "stop = the *tighter* of the ATR and fixed-pct candidates when held; the wider
  when not held", "never set a stop above cost basis for a held position").
- `position_sizing:` — trim/add percentage bands keyed by `verdict_bands` action
  and score strength (e.g. Trim at score 2.0-2.75 → sell 25-40% band; Sell below
  2.0 → exit fully), and a check against `config.SINGLE_NAME_MAX_WEIGHT`.
- `order_type_rules:` — maps a computed stop+limit pair to a valid Wealthsimple
  order type (stop-market vs. stop-limit) and encodes the constraint this session
  hit directly: for a stop-limit sell, `limit_price <= stop_price`.

Edited only through a new **`author-execution-policy`** skill mirroring
`author-decision-rubric` (validates the file, checks any weighted blends sum
correctly, requires a version bump). `trade-planner` reads it, never writes it —
same read/write split every other KB agent follows.

### 2. `.claude/skills/plan-trade-price/` (new skill)

- `SKILL.md` — the workflow, in the same 3-scripts-bracketing-one-judgment-step
  shape as `evaluate-stock-decision/SKILL.md`:
  1. `scripts/price_levels.py --ticker T --recommendation <artifact.json> [--research <yfinance-research.json>] --output exports/trade-plans/T-<date>-candidates.json`
     — deterministic. Reads the recommendation artifact's `proposed.action`,
     `weighted_score`, position context; calls
     `src/analytics.py::get_position(ticker_id)` for real cost basis (using
     `cost_basis_mkt`/`quantity`, the market-currency figure that matches what
     Wealthsimple displays — **not** `cost_basis` (CAD), which is what produced
     this session's wrong number) and `data_quality_flags` (surfaces
     `fx_stale`/`provisional_quantity` as caveats); calls
     `get_price_history(symbol)` to compute 14-day ATR; reads the options/analyst
     groups already present in the research JSON for IV and target-price
     candidates. Applies `execution-policy.yml`'s formulas to produce every
     candidate stop/target/size, leaving a `selected`/`rationale` slot empty —
     same "worksheet with empty judgment slots" pattern as
     `scoring_worksheet.py`.
  2. LLM (the agent) fills `selected` — order type, stop price, limit price,
     quantity — choosing among the precomputed candidates per
     `selection_policy`, and writes a short rationale.
  3. `scripts/validate_price_plan.py --path <artifact>` — recomputes candidates
     and the policy's selection rule independently and diffs against what the
     agent chose; must exit 0. Same non-negotiable check as
     `validate_recommendation.py`.
- `references/price-plan-contract.md` — the `trade-plan.v1` JSON schema (below)
  and every validator rule, written in the same style as
  `evaluate-stock-decision/references/recommendation-contract.md`.
- `scripts/price_levels.py`, `scripts/validate_price_plan.py` — Python, live only
  here (per `CLAUDE.md`: skill-owned scripts go beside the skill, not in `src/`).

**`trade-plan.v1` artifact** (`exports/trade-plans/<TICKER>-<date>.json`):

```json
{
  "schema": "trade-plan.v1",
  "ticker": "PLTR",
  "generated": "2026-08-04",
  "policy_version": "v1.0",
  "input_recommendation": {"path": "exports/stock-recommendations/PLTR-2026-08-03.json",
                            "action": "Hold", "weighted_score": 3.73, "confidence": "High"},
  "position": {"held": true, "quantity": 1.74,
               "cost_basis_native": 105.50, "cost_basis_currency": "USD",
               "cost_basis_source": "analytics.get_position:cost_basis_mkt",
               "current_price": 160.00, "position_weight_pct": 4.9,
               "single_name_cap_pct": 10.0, "data_quality_flags": []},
  "candidates": {
    "stop": [{"method": "atr_multiple", "value": 145.20, "note": "..."},
             {"method": "fixed_pct", "value": 150.00, "note": "..."},
             {"method": "iv_implied", "value": 142.00, "note": "..."}],
    "target": [{"method": "analyst_target_anchored", "value": 182.00, "note": "..."},
               {"method": "fixed_pct", "value": 176.00, "note": "..."}]
  },
  "selected": {"order_type": "stop-market", "stop_price": 145.20,
               "limit_price": 178.00, "quantity_pct": 50, "quantity_shares": 0.87,
               "rationale": "..."},
  "sources": ["exports/stock-recommendations/PLTR-2026-08-03.json",
              "analytics.get_position", "historical_records via get_price_history",
              "yfinance options/analyst groups"]
}
```

### 3. `.claude/agents/trade-planner.md` (new agent)

- **Frontmatter:** `model: sonnet` (this is a bounded selection-and-justification
  task over pre-computed candidates, not open-ended multi-dimension scoring
  against citations the way `stock-analyst` is — doesn't need `opus`; matches the
  repo's existing cost-consciousness, e.g. the ETF-batch `sonnet` override).
  `color:` pick an unused one. `tools: ["Bash", "Read", "Write"]`. `skills:
  [plan-trade-price]`.
- **Body**, same section order every agent in this repo uses:
  - Identity paragraph — states plainly it does not decide buy/sell, only prices
    an already-decided trade, and never invents a price outside the computed
    candidate set.
  - **When to invoke** — "what price/stop/limit should I use for TICKER",
    "size and price this trade", after a recommendation already exists (or after
    the user has one from a prior session).
  - **Workflow** — the `plan-trade-price` skill's 3 steps.
  - **Guardrails:**
    - Requires an existing `stock-recommendation.v1` artifact for the ticker; if
      none exists or is stale, hand off to `stock-data-prep`/`stock-analyst`
      first rather than guessing the action.
    - Never sets a stop/limit outside the script-computed candidate set; never
      hand-tunes a price the way `stock-analyst` never hand-tunes a score.
    - Cost basis always read via `analytics.get_position` (`cost_basis_mkt`),
      never a raw `position_ledger`/`position_snapshots` query — the exact bug
      hit this session.
    - States the concrete order type (stop-market vs. stop-limit) and, for a
      stop-limit, enforces `limit_price <= stop_price` on the loss side — the
      broker constraint discovered this session.
    - **No trades.** Output is a price plan only; the user places the order
      manually in Wealthsimple. No automatic Portfolio Status change.
    - Write-scope: only `exports/trade-plans/`; never edits `Knowledge-Base/` or
      `execution-policy.yml`.
  - **Handoffs** — table: missing/stale recommendation → `stock-analyst`
    (via `stock-data-prep` first if no worksheet); "None" for a KB-commit step
    (explicitly deferred, not built this phase).
  - **Output Format** — Price Plan report: recommendation being priced, position
    context (with cost-basis source called out), candidate table, selected
    order (type/stop/limit/quantity), rationale, artifact path.

### 4. Docs

- `docs/agents/trade-planner/architecture.md` and `plan.md`, matching the
  `stock-analyst`/`stock-data-prep` companions (Purpose, Runtime Settings, Skill
  Dependencies, Workflow, Guardrails, Handoffs, Code Location).
- `docs/architecture/trade_execution_planning.md` (new) — documents the
  candidate-computation formulas and the policy file's schema in full, the way
  `docs/architecture/decision_support_flow.md` documents the rubric pipeline.
  Add one cross-link from `decision_support_flow.md`'s existing "deferred /
  next steps" section pointing to this new doc, since that section already lists
  "no price target, stop-loss, or position sizing output" as a known gap.

## Key files to reuse (confirmed this session, do not re-derive)

- Cost basis / position: `src/analytics.py::get_position(ticker_id, db_path)` and
  `get_holdings(db_path)` — use `.cost_basis_mkt` / `.quantity` for
  Wealthsimple-matching native-currency average cost; `.data_quality_flags` for
  `fx_stale`/`provisional_quantity` caveats. Ground truth if ever in doubt:
  `uv run python src/app.py reconcile-holdings --report <broker-export.csv>`.
- Price history / ATR input: `src/analytics.py::get_price_history(symbol, db_path)`.
- Position weight / single-name cap: `src/portfolio_metrics.py::calculate_position_weights`,
  `src/config.py::SINGLE_NAME_MAX_WEIGHT`.
- Options/analyst evidence already fetched per ticker:
  `data.options.*`, `data.analyst.price_targets` /
  `recommendations_summary`, same JSON `fetch-stock-research-data` already
  produces for `stock-data-prep` — no new fetch skill needed, `price_levels.py`
  reads the same research JSON the worksheet was built from.
- Pattern to copy directly: `evaluate-stock-decision/scripts/scoring_worksheet.py`
  + `validate_recommendation.py` (script/validator split) and
  `stock-analyst.md` (agent frontmatter/body shape) — both already read in full
  this session.

## Explicitly out of scope for this first phase

- Writing target/stop prices back onto the thesis page (`## Status` block or
  Decision History table) — confirmed structurally safe to add later, but not
  built now.
- Automatic order placement of any kind.
- Fixing the unrelated currency-mixing bug found in
  `read_classification_data.py`'s `current_weight_percent`/
  `unrealized_gain_loss_percent` (native-currency market value divided by CAD
  cost basis for non-CAD tickers) — noted for awareness, not part of this
  agent's build.

## Verification

- `uv run python .claude/skills/plan-trade-price/scripts/price_levels.py --ticker PLTR --recommendation exports/stock-recommendations/PLTR-2026-08-03.json --output exports/trade-plans/PLTR-2026-08-04-candidates.json` runs clean and produces plausible ATR/IV/fixed/target candidates against real PLTR data (cross-check the stop/target numbers by hand against `historical_records` and the known $105.50/$160 cost-basis/price pair from this session).
- Manually fill `selected` for that PLTR case and run
  `validate_price_plan.py --path <artifact>` — must exit 0.
- Confirm `cost_basis_native` in the artifact equals $105.50 (not the earlier
  wrong $149.99), proving the `cost_basis_mkt` fix.
- Run the new `trade-planner` agent end-to-end once wired up: "price a trim for
  PLTR" with the existing recommendation artifact present, and separately with no
  recommendation artifact present (confirm it hands off to
  stock-data-prep/stock-analyst instead of guessing).
- `uv run python -m unittest discover -s tests` still passes (no `src/`
  changes expected in this phase beyond reads).
