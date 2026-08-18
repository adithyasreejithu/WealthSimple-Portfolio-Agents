# Investment Portfolio Manager Agent

This document is the human-readable companion to the Claude Code agent defined at
[`.claude/agents/investment-portfolio-manager.md`](../../../.claude/agents/investment-portfolio-manager.md).

## Purpose

The Portfolio Manager stage of the rebuilt Investment Analyst track
(`docs/plans/investment-analyst-rebuild-roadmap.md`, Phase 11). Reads one
run's validated `investment-thesis.v1` (Phase 4's `investment-analyst`
output) and a deterministic `portfolio-policy-worksheet.v1` (built by
`src/workspace/policy_worksheet.py`), and produces a `DecisionProposal`:
`Buy`/`Hold`/`Trim`/`Sell`/`Add` for an owned security, or
`Buy`/`Watch`/`Wait`/`Pass` for a not-currently-owned one, plus a weight-based
sizing recommendation, cited to both inputs. It is the first stage in this
track to emit a portfolio action — the vocabulary Phase 4's agent was
explicitly forbidden from using. Which vocabulary applies is re-derived from
the policy worksheet's `subject.currently_held`, never left to the agent's
judgment (see "The policy-consistency gate" below).

**Scope note resolved during planning:** the roadmap's dependency graph
(`investment-analyst-rebuild-roadmap.md`, Diagram 2) has Phase 11 waiting on
Phase 10 (Market Researcher, not yet built) "because a Portfolio Manager
needs at least a starting market view." Built now anyway, per the repo
owner: market context is an unavailable input this phase — recorded as a gap
via `market_researcher_agent: "deferred"` in `KNOWN_COMPONENTS`
(`src/workspace/run.py`), not silently omitted or invented.

**Policy scope note:** `Knowledge-Base/ref/policy_v1_1.yaml` defines exactly
two enforceable limits — `constraints.single_name_max_percent: 10` and
per-group `allocation_targets` (Core/Income/Quality/Growth/Alternatives/Cash
— portfolio roles, not GICS sectors). There are no sector-cap or
currency-limit fields. This agent enforces only what the file actually
contains; `src/analytics.py`'s sector/look-through-sector/currency exposure
functions are surfaced as informational context, never as an invented cap.

## Runtime Settings

- `model: sonnet` — unlike `investment-analyst` (`opus`, open-ended
  fundamental synthesis), this agent's arithmetic is fully delegated to
  `policy_worksheet.py`; its judgment is bounded to choosing among the
  actions the deterministic checks leave open and phrasing a citation-backed
  rationale. Matches the precedent `CLAUDE.md` already sets for
  simpler-judgment tracks (the ETF-batch `stock-analyst` `sonnet` override).
- `tools: ["Bash", "Read", "Write"]` — `Bash` drives three new deterministic
  CLI stages (`run build-policy-context`, `run check-decision`,
  `run save-decision`); `Read` opens the thesis and policy worksheet JSON;
  `Write` is scoped by the guardrails to `tmp/<TICKER>-decision-draft.json`
  only — the finished artifact is written by `save-decision`, not directly
  by the agent.
- **No `skills:` field.** Both new CLI stages are deterministic Python
  (`src/workspace/policy_worksheet.py`, `src/workspace/decision_validation.py`),
  not Claude skills — same reasoning as `investment-analyst`.

## Inputs

- `agent_outputs/<TICKER>-<stamp>-thesis.json` — the `investment-thesis.v1`
  artifact (Phase 4), read for `conclusion.fundamental_rating`/
  `valuation_stance`/`thesis_confidence`, `key_claims`, and (for
  `order_guidance`) `valuation.methods`/`scenarios`.
- `calculations/<TICKER>-<stamp>-policy-worksheet.json` — the
  `portfolio-policy-worksheet.v1` artifact (Phase 11), read for
  `policy_checks`, `current_weight_pct`, `portfolio_context`, and (for
  `order_guidance`) `price_and_market_context`.
- The subject's resolved status (`owned`/`wishlist`/`avoid`/`retired`/`unknown`)
  from the `.claude/skills/security-status/` skill — invoked once, before
  `build-policy-context`. A hard gate, not just context: it selects which
  action vocabulary applies, and a non-owned subject may not receive
  `Trim`/`Sell` (see "The policy-consistency gate" below). This is the
  **same skill, unchanged**, `investment-analyst` invokes — there is
  deliberately no separate copy for either agent.

The agent never recomputes a weight, cap, or exposure figure itself — both
inputs are already fully computed by deterministic Python.

## Outputs

One `DecisionProposal` JSON, written to
`final/<TICKER>-<stamp>-decision.json` by `run save-decision` (never directly
by the agent), registered as evidence with
`evidence_type="decision_proposal"`, `source_name="portfolio_manager"`,
`collection_method="llm_judgment"`. `final/`, not `agent_outputs/` — per
`docs/architecture/run_workspace.md`'s "How information moves" diagram, the
portfolio stage's output is `final/*.json`, "a proposal, never an order."
`trade_executed` and `human_approval_required` are pinned by the
`DecisionProposal` schema itself (`src/workspace/models.py`).

For a live buy/sell action (`Buy`/`Add`/`Trim`/`Sell`), the `DecisionProposal`
also carries `order_guidance` (`models.OrderGuidance`): an `order_type`
(`Market`/`Limit`/`Stop-Limit`/`Stop-Market`) plus one to three cited prices
(`reference_price`, and `trigger_price`/`limit_price` as the order type
requires). This is advisory, not an order -- see "Order guidance" below.
`Hold`/`Watch`/`Wait`/`Pass` must **not** carry `order_guidance`; there is no
trade to give mechanics for.

## The deterministic policy layer

`src/workspace/policy_worksheet.py` mirrors `investment_worksheet.py`'s role
for Phase 4: all arithmetic happens in plain Python. Given a run and a
ticker, it reads the live portfolio (fresh DB read — portfolio state is not
run-scoped) and evaluates exactly two checks:

| Check | Source | Fails when |
|---|---|---|
| `single_name_cap` | `SINGLE_NAME_MAX_WEIGHT` (`src/config.py`, backing `policy_v1_1.yaml`'s `constraints.single_name_max_percent: 10`) | The security's current weight exceeds the limit — unless it is a Core-classified broad-market ETF, exempt for the same reason `analytics.portfolio_report`'s portfolio-wide concentration check exempts them (idiosyncratic single-company risk, not what a diversified index fund represents) |
| `group_allocation_target` | `analytics.load_allocation_targets()` + `calculate_rebalance_drift()` | The security's classifier group's current weight exceeds that group's configured `max_percent` |

Both reuse the exact functions `analytics.portfolio_report` already uses for
its portfolio-wide concentration/drift figures (`calculate_position_weights`,
`calculate_concentration`, `calculate_rebalance_drift`), rather than
reimplementing the math — see `src/analytics.py`'s `get_ticker_id` and
`get_classification` (added this phase to resolve a possibly-not-yet-held
candidate ticker to its `ticker_id`/classifier group, since
`portfolio_report`'s existing helpers only classify already-held tickers).

Sector, look-through-sector, and currency exposure
(`get_sector_allocation`/`get_look_through_sector_exposure`/
`get_currency_exposure`) are carried through as informational
`portfolio_context` only — no cap exists for either in `policy_v1_1.yaml`,
so none is invented.

## The three CLI stages it drives

| Command | Side effects | Purpose |
|---|---|---|
| `run build-policy-context --run-id --ticker [--db-path]` | Writes `calculations/*-policy-worksheet.json`, registers it as `policy_worksheet` evidence, appends a `policy_worksheet_built` audit event | Wraps `policy_worksheet.build_policy_worksheet_for_run` |
| `run check-decision --run-id --path <draft>` | None — pure validation | Wraps `decision_validation.check_decision_draft`; the iterate-until-valid loop |
| `run save-decision --run-id --path <draft> --ticker` | Writes `final/*-decision.json` (only on success), registers `decision_proposal` evidence, appends `pm_drafted` + `validated` audit events | Wraps `decision_validation.save_decision`; the one authoritative finalize step |

## The policy-consistency gate

The Phase 11 success gate — "a single-name-cap breach is correctly flagged"
— is enforced deterministically, not left to the agent's judgment.
`decision_validation.check_action_consistent_with_policy` re-derives the
verdict from the policy worksheet **on disk** (never trusts the agent's own
copy of `policy_checks` in the draft): if any check reads `fail`,
`proposed_action` must be `Hold`/`Trim`/`Sell` — `Buy`/`Add` is a validation
error at both `check-decision` and `save-decision`, and again at `run
validate` (which independently re-runs `decision_validation.validate_decision`
against every `final/*.json`, mirroring how it already re-validates every
thesis under `agent_outputs/`). This is the same "recheck from the source,
never trust the agent's copy" principle `thesis_validation.py` already
applies to worksheet references.

The same module enforces two further, independent gates. First,
`decision_validation.check_action_matches_ownership_vocabulary` rejects a
`proposed_action` from the wrong vocabulary for this subject's ownership --
`Hold`/`Trim`/`Sell`/`Add` require `subject.currently_held: true`;
`Watch`/`Wait`/`Pass` require `false`; `Buy` is valid either way. Second,
`decision_validation.check_action_requires_ownership` rejects `Trim`/`Sell`
whenever `subject.currently_held` reads `false` -- redundant with the first
gate for `Trim`/`Sell` specifically (neither is even a legal value in the
not-owned vocabulary), kept as defense in depth. Both re-derive
`currently_held` from `analytics.get_holdings()` at worksheet-build time,
never trusted from the agent's own reading of the `security-status` skill's
digest. You cannot trim or sell a position you do not hold; for a non-owned
subject the entry actions are `Buy`/`Watch`/`Wait`/`Pass`.

## Capacity constraint vs. thesis quality in `rationale`

The policy-consistency gate above is deterministic about the *action*: a
failing `single_name_cap`/`group_allocation_target` forces `Hold`/`Trim`/
`Sell` (owned) or `Watch`/`Wait`/`Pass` (not-owned) regardless of the thesis.
It says nothing about the *prose*, though, and an unqualified `Watch`/`Wait`/
`Hold` reads to a human as "this stock isn't good enough" even when the real
reason is that the portfolio (or the security's classifier group) has no
room left. The agent's workflow (step 7) now requires the `rationale` to say
which of the two it is whenever a passing, attractive thesis is the one
being blocked by a failing weight/allocation check: name the failing check
explicitly as a capacity constraint, and state that the thesis is not the
reason for the negative-leaning action. This is a prose requirement checked
only by instruction, not a schema field — `check-decision`/`save-decision`
do not parse `rationale` text, so getting it right is on the agent at draft
time, not something the validator will catch after the fact.

## Possible-misclassification flag

A full `group_allocation_target` is not always a pure capacity problem — a
crowded classifier group is also exactly where a misclassified ticker first
becomes visible, since it is now competing for room in a group it may not
actually belong to. The agent's workflow (step 7) has it weigh, specifically
when `group_allocation_target` is the failing check, whether the thesis's
own description of the business (`key_claims`, `conclusion`) actually
matches `policy_worksheet.subject.primary_group`. When there's a
thesis-grounded reason to think it doesn't, the agent adds one
`DecisionProposal.uncertainties` entry naming the group and the specific
mismatch — kept separate from the capacity-constraint rationale above, since
they are different claims ("no room right now" vs. "may be in the wrong
group"). This is a flag for human review, never a reclassification the agent
performs itself: it has no write access to `portfolio_classifications`, and
`classify-portfolio` (`.claude/skills/classify-portfolio/`) is the only path
that changes a group. Silence is the default — a full group on a
well-classified name gets no flag, since raising one without thesis-grounded
support is noise a reader would learn to ignore.

## Order guidance

`order_guidance` (`models.OrderGuidance`) is strictly advisory -- a
statement of "if this were acted on, here is the mechanism and the price
levels," never a claim that a trade happened. It does not weaken the
"never execute" guarantee (`docs/architecture/run_workspace.md`):
`trade_executed`/`human_approval_required` remain pinned by the schema, and
its field names were checked against both forbidden-field scanners in this
codebase and collide with neither -- `analysis_models.FORBIDDEN_FIELD_NAMES`
(includes `order_type`) is invoked only from `InvestmentThesis`'s own
validator, never against a `DecisionProposal`; `validation.FORBIDDEN_EXECUTION_KEYS`
(which does scan every `final/*.json` artifact, including this one) contains
`order_id`/`executed_at`/`execution_id`/`fill_price`/`fill_quantity`/
`filled_at`/`broker_*`/`fill_*`, none of which `OrderGuidance` uses.

Two gates enforce it:

- `decision_validation.check_order_guidance_present` -- a pure shape check:
  `order_guidance` is required for `Buy`/`Add`/`Trim`/`Sell` and forbidden
  for `Hold`/`Watch`/`Wait`/`Pass`.
- `decision_validation.check_order_guidance_prices_are_grounded` -- the
  "no invented numbers" guarantee. Each `PriceLevel.source` is a dotted
  citation path rooted at `thesis.` or `policy_worksheet.` (e.g.
  `thesis.scenarios.bear.fair_value_per_share`,
  `thesis.valuation.methods[fcf_yield].resulting_equity_value_per_share.low`,
  `policy_worksheet.price_and_market_context.week52_low`). This function
  re-resolves the path against the actual cited documents **on disk** and
  requires `PriceLevel.price` to match the resolved value within tolerance
  (`max($0.01, 0.1% of the resolved value)`) -- never trusted from the
  agent's own copy, the same "recheck from the source" discipline
  `check_thesis_ref` applies to hashes, applied here to prices.

The policy worksheet's `price_and_market_context` (`latest_close`,
`week52_low`, `week52_high` -- the trailing 365-day close range, added this
phase via `policy_worksheet._price_and_market_context`, reusing
`analytics.get_price_history`) is deliberately the *only* price source
besides the thesis: it carries no moving average, drawdown, or volatility --
those live only in the `security-technicals` artifact, which
`DecisionProposal` does not cite and this agent does not read directly.

## What this agent does not do

- Does not score `Knowledge-Base/taxonomy/decision-rubric.yml` — legacy
  benchmark path only.
- Does not emit `Watchlist`/`Avoid` — not-held research outcomes reserved for
  the legacy `stock-analyst` → `kb-intake` path; `DecisionProposal.proposed_action`
  does not accept them.
- Does not mix the owned and not-owned action vocabularies — `Hold`/`Trim`/
  `Sell`/`Add` on a not-owned subject, or `Watch`/`Wait`/`Pass` on an owned
  one, are both validation errors.
- Does not consume Market Researcher output (Phase 10, not yet built) — see
  the scope note above.
- Does not invent a sector or currency cap — `policy_v1_1.yaml` defines none.
- Does not invent an order-guidance price — every `PriceLevel` must cite, and
  match, a value already present in the thesis or policy worksheet.
- Does not write to `Knowledge-Base/` — KB promotion is a separate,
  human-gated, unbuilt component.
- Does not fetch data or build a thesis itself — if no thesis exists for the
  ticker, it hands off to `investment-analyst` rather than doing that work.
- Does not reclassify a security — a suspected `primary_group` mismatch is
  raised as an `uncertainties` entry for human review, never acted on
  directly; only `classify-portfolio` changes `portfolio_classifications`.

## Guardrails

See the agent definition's Guardrails section for the full list. The two
most load-bearing: an `unavailable` policy check (e.g. an unclassified
security) is a gap, not permission to `Buy`/`Add`; and `confidence` never
exceeds the source thesis's own `thesis_confidence`.

## Handoffs

| Label | Action |
| --- | --- |
| No thesis for this ticker in this run | Invoke `investment-analyst` for the ticker in this run, then retry. |
| Security's classifier group could not be resolved | Stop; `python src/app.py classify` needs to run for this ticker first -- rare for a declared `wishlist` subject, expected for `avoid`/`retired`/`unknown` ones. |

## Code Location

`src/workspace/policy_worksheet.py` (`build_policy_worksheet`,
`build_policy_worksheet_for_run`) and `src/workspace/decision_validation.py`
(`check_thesis_ref`, `check_policy_worksheet_ref`,
`check_action_matches_ownership_vocabulary`,
`check_action_consistent_with_policy`, `check_action_requires_ownership`,
`check_order_guidance_present`, `check_order_guidance_prices_are_grounded`,
`check_decision_draft`, `save_decision`) hold the deterministic logic;
`src/workspace/cli.py` (`build-policy-context`/`check-decision`/`save-decision`
subcommands) wires them to the CLI. `DecisionProposal`/`PortfolioAction`/
`WishlistAction`/`OrderGuidance`/`PriceLevel`/`PolicyCheck`/`ArtifactRef`/
`DecisionSizing` (`src/workspace/models.py`) define the output schema. Two
small additions to `src/analytics.py` this phase — `get_ticker_id`,
`get_classification` — resolve a candidate ticker's identity/group without
requiring it to already be held. See
[`docs/architecture/run_workspace.md`](../../architecture/run_workspace.md).
