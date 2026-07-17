# Project Vision

**Status:** Approved baseline — read before starting any phase in
[`development-roadmap.md`](development-roadmap.md). Update this document only
when the vision itself changes, not to log routine progress (that belongs in
`docs/project/handover.md`).

## Why this document exists

The `Agent-Development` branch built a large amount at once — a data
pipeline, a portfolio classifier, a research knowledge base, and a
multi-agent stock decision-support system — and all of the agents/skills were
just archived (`.claude/agents/_archive/`, `.claude/skills/_archive/`). The
retrospective in `docs/project/agent-development-lessons.md` captures the
*technical* lessons from that (token budgets, mechanical/judgment split,
etc.). This document addresses the other cause: the project moved into
heavy implementation before "what am I actually building, end to end" was
written down anywhere. Everything below is the answer to that question, so
future work has a fixed target to check itself against instead of expanding
scope one plausible-sounding feature at a time.

## Who this is for

One person (Adithya), one Wealthsimple account, personal use only. Not a
multi-user product, not for distribution. Every design decision should
default to "simplest thing that works for one user on their own data,"
not "generalizable platform."

## The problem this solves

Wealthsimple has no queryable source of truth for portfolio history, and no
built-in decision support beyond raw numbers. Two separate needs follow from
that:

1. **Know what I actually hold and how it's doing** — a reliable, always
   current record of holdings, transactions, and performance, built from data
   already available (CSV exports, PDF statements, confirmation emails) —
   never by scraping Wealthsimple directly (ruled out in `docs/project/issues.md`
   on ToS/legal grounds).
2. **Get help deciding what to do about it** — for a given ticker (or the
   whole portfolio), a cited, reasoned buy/hold/sell/trim view — not a
   prediction, a structured application of rules Adithya himself has written
   down (see "What the agents are and are not" below).

Both needs converge on one end state: **a place Adithya can open from his
phone or laptop, anywhere, anytime, and see current holdings and current
decision-support verdicts** — without opening a terminal or remembering a CLI
command.

## What exists today (grounded, not aspirational)

- **Data pipeline** (`src/`) — CSV/PDF/email ingestion, ticker resolution,
  DuckDB storage, a read-only analytics engine (returns, risk, allocation,
  fees, income). This works end to end today and is the foundation everything
  else sits on. See `docs/project/overview.md` for the full tour.
- **Portfolio classification** — a narrow, already-working Claude Code agent
  that groups holdings into approved allocation buckets from a versioned YAML
  rule set. Currently archived alongside everything else from
  `Agent-Development`, but it was working and is cheap to bring back.
- **Research knowledge base + decision-support system** — a much larger,
  mostly-built system (`Knowledge-Base/`, KB agents, a decision rubric,
  stock-analyst/stock-data-prep agents) that scores individual tickers
  against a versioned, hand-authored rubric and writes cited thesis pages.
  This is real, substantial work — not a prototype to throw away — but it
  was built and scaled (a 25-ticker portfolio run) before the hosting/access
  problem was solved and before token cost was under control. It is now
  archived, to be **rebuilt smaller, not abandoned** (see roadmap).
- **CLI (`src/app.py`)** — the current interface to all of the above. This
  was always meant as a *testing and development* interface, not the
  end-user product. It stays as the developer-facing entry point; it is not
  the thing Adithya opens from his phone.

## What "done" looks like (v1 definition)

Adithya can, from his phone or laptop, in one place, without a terminal:

1. See current holdings, allocation, and key performance numbers, reflecting
   real up-to-date data.
2. Ask about a specific ticker ("what should I do about NVDA?") and get back
   a cited, rubric-scored verdict (Buy/Hold/Sell/Trim/Add/Watchlist/Avoid)
   with a written rationale — not a raw JSON dump, not a prediction stated as
   fact.
3. Trust that both of the above reflect the same underlying data — no
   separate spreadsheet, no manually re-run script, no stale export.

That's the finish line for v1. Anything beyond that (alerts, scheduled
digests, technical-analysis dimensions, multi-source research registries) is
real and already partially designed (see `docs/ideas-mcp.md`,
`docs/plans/stock-decision-support-technical-analysis.md`) but is explicitly
**out of scope until v1 is working end to end** — see the roadmap's phase
gating for why.

## What the agents are and are not

Carried forward directly from the lessons-learned retrospective, because
this is a product decision, not just an implementation detail:

- The LLM **never predicts markets**. It applies an explicit, versioned
  rubric (`Knowledge-Base/taxonomy/decision-rubric.yml`) that Adithya owns
  and edits; a deterministic script recomputes the weighted score and
  verdict from the model's per-criterion scores, so the model cannot talk
  its way to a different answer than the math produces.
- The model gets one place for genuine opinion (`analyst_view`), kept
  structurally separate from the mechanical verdict. If the two disagree
  often, that's a signal to retune the rubric — not to trust the model's
  opinion over the math.
- Mechanical work (fetching data, building a worksheet) and judgment work
  (scoring against the rubric, writing the narrative) are always two
  separate agents on two different models, not one agent doing both.

## Explicit non-goals

- **Not a trading bot.** Nothing in this system places trades or connects to
  Wealthsimple's live account/trading surface. Decision support ends at "here
  is the verdict and why" — the trade itself is manual, by Adithya, elsewhere.
- **Not scraping Wealthsimple.** Already decided against in
  `docs/project/issues.md` on legal/ToS grounds. Data comes only from
  exports/statements/emails Adithya already has, plus public Yahoo Finance
  data.
- **Not multi-user.** No auth system beyond "only Adithya can see this."
- **Not real-time.** Portfolio and market data refresh on a schedule or on
  request, not streamed live.
- **Not a rebuild of every archived agent at once.** The archived
  `kb-discovery`/`kb-intake`/`stock-analyst`/`stock-data-prep`/
  `holdings-reconciliation` system is the reference design for where this is
  headed, not a checklist to restore in one pass. It comes back incrementally,
  gated by the roadmap, specifically because "rebuild everything at once" is
  the failure mode this document exists to prevent.

## Hosting shape (starting point, not a permanent commitment)

Access-anywhere-anytime is the actual requirement, not a specific technology.
The starting point: a **Claude artifact** wired to the pipeline's data,
because it requires no separate hosting infrastructure, is reachable from
phone or laptop anywhere Claude is available, and can be stood up fast enough
to validate the whole "one place, always current" idea before investing
further. A dedicated self-hosted web app remains the fallback if the artifact
model hits a real limit (needs to trigger scheduled jobs Claude itself can't
run, needs to work fully offline, needs push notifications) — see the
roadmap for exactly where that decision gets revisited, not before.
