# Wishlist Dashboard — Full Version (deferred)

## Context

`docs/dashboard-corrections-2026-08-17.md` shipped a first-pass `/wishlist`
dashboard page: status, classification, and a compact agent verdict
(fundamental rating, valuation stance, thesis direction/confidence, proposed
action, failing policy checks) for each declared-wishlist ticker. That scope
was a deliberate, user-requested cut for time; this document captures the
richer version to build later, so the rationale isn't lost.

The data already exists — `investment-thesis.v1` and decision-proposal
artifacts under `workspace/runs/*` carry far more than what the first pass
surfaces. This is additive to `analytics.get_wishlist_overview` and the
`/wishlist` page, not a rewrite.

## What to add

**From the thesis artifact** (`src/workspace/analysis_models.py`'s
`conclusion` block, already partially read):
- `investment_case` / `case_against` — the full prose, not just the
  structured verdict fields. Currently truncated out of the overview.
- `most_important_catalyst`, `most_important_risk`, `most_important_unknown`.
- `key_claims[]` — cited evidence, so the page can show *why* a rating was
  given, not just the rating.
- `valuation` block, `scenarios`, `unknowns`.
- `thesis_direction` history across runs (initial → strengthening/weakening) —
  the overview currently only shows the latest run; a small trend view (e.g.
  a sparkline of `thesis_confidence` over the last N runs, since each run's
  filename is already timestamped) would show whether a name is getting more
  or less attractive over time.

**From the decision-proposal artifact** (`src/workspace/models.py`):
- `order_guidance` — order type and cited price levels (advisory only, never
  an instruction to trade), for when a Watch/Wait resolves to Buy.
- `sizing.rationale` in full.
- `uncertainties[]`.
- All `policy_checks`, not just failing ones — passing checks are useful
  context too (e.g. confirming single-name cap has headroom even when group
  cap doesn't).

**Knowledge-Base integration:**
- Join `Knowledge-Base/stocks/TICKER.md` front matter (`status:`) when a page
  exists. None of the 5 current wishlist names has one yet, but future
  wishlist tickers may.
- Link out to the KB page from the wishlist card when present.

**Run history, not just latest:**
- A ticker detail view (`/wishlist/[ticker]`, mirroring `/holdings/[symbol]`)
  listing every thesis/decision run for that ticker, so the user can see how
  the verdict evolved rather than only the newest snapshot.

## Design questions to resolve before building

- Reading full narrative text (multi-paragraph `investment_case`, etc.) into
  a dashboard API response is a bigger payload than the first pass's compact
  fields — decide whether `/api/wishlist` grows to include it directly, or a
  separate `/api/wishlist/{ticker}` detail endpoint stays lean on the list
  view and loads narrative content on demand (matches the `/holdings/[symbol]`
  precedent of a list endpoint + a detail endpoint).
- `key_claims[]`/evidence citations reference evidence IDs
  (`ev_xxxxxxxxxxxx`) resolved elsewhere in the run's `evidence/sources.jsonl`
  — decide whether to resolve and inline them, or link out.
- Multi-run trend views need a decision on how many runs to show and how to
  handle tickers with only one run (all 5 current names already have
  multiple).

## Not changing

- The read-only, DB-plus-filesystem-glob approach `get_wishlist_overview`
  already established — no new write paths, no new persistence.
- The `WishlistAction` vocabulary guard (`action_vocabulary_mismatch`) stays
  as-is; it should keep flagging any decision artifact that doesn't follow
  the not-owned vocabulary rather than trusting it.
