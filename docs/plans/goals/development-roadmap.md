# Development Roadmap

**Read [`project-vision.md`](project-vision.md) first.** This document is
the *order* of work toward that vision. Each phase ships something Adithya
can actually use or check before the next phase starts — that's the fix for
"we tried to do a lot at once, it failed." No phase after Phase 0 begins
until the previous phase's exit criteria are met.

Reference throughout: `docs/project/agent-development-lessons.md` (the
technical retrospective) — every phase that touches agents gates on its
checklist explicitly, not from memory.

---

## Phase 0 — Reset to a clean baseline

**Why first:** the repo is currently mid-archive — every
`Agent-Development` agent/skill is staged as renamed into `_archive/`, and
nearly the entire repo (`src/`, `tests/`, `Knowledge-Base/`, `docs/`) has
unstaged modifications on top of that. An unclear git state is itself a
symptom of too much in flight at once, and no new work should start on top
of it.

**Work:**
- Review the staged archive rename + the unstaged diff on top of it as one
  unit; decide what actually gets committed (the archive move itself is the
  right call per the vision doc — commit it) versus what should be reverted.
- Add one short note to `docs/project/handover.md`: the KB/decision-support
  system is parked as reference, superseded by this roadmap, not abandoned.
- Land on a single clean commit (or a small stack of clean commits) so
  Phase 1+ starts from a known-good `git status`.

**Exit criteria:** `git status` is clean, `uv run python -m unittest
discover -s tests` passes, and `handover.md` reflects reality.

---

## Phase 1 — Vision & scope

Already done — `project-vision.md` and this roadmap. Revisit only if the
vision itself needs to change (new constraint, changed priorities), not for
routine progress updates.

---

## Phase 2 — Pipeline hardening

**Why before anything else:** nothing downstream — hosted view or agents —
is worth building on top of ingestion that still needs manual database
surgery. This is the foundation, and it already mostly works; this phase
closes the known gaps rather than adding new surface area.

**Work (pulled from the existing backlog in `docs/project/todo.md`,
not reinvented here):**
- Separate activity-export database orchestration out of `data_sorter.py`
  into its own pipeline stage.
- Add FX-normalized cross-currency portfolio totals.
- Add operational monitoring for partial email syncs and failed Yahoo
  symbol lookups.
- Add a review command for statement/email rows that can't be reconciled
  automatically.
- Cross-reference `classify-portfolio` holdings against the Knowledge-Base
  wiki (held tickers with no thesis page, and vice versa) — this is the one
  backlog item marked `Priority`.

**Exit criteria:** `pipeline` runs end to end from a fresh `Data/` drop with
no manual intervention; full test suite green; `analytics` output matches
actual current holdings with no known gaps.

---

## Phase 3 — Minimal hosted view

**Why before agents:** validates the actual hosting shape — "open one place
from my phone, anywhere, anytime" — while the only thing behind it is
already-reliable pipeline data. If the hosting approach needs to change,
better to find out now than after agent output depends on it.

**Already resolved:** the original "Claude artifact" starting point was
checked against actual product behavior and ruled out — live artifacts only
render in the Claude desktop app (not mobile/web), and any artifact
touching local files needs the desktop app open and the laptop awake
regardless. See `project-vision.md`'s "Hosting shape" section for the
replacement approach below.

**Work:**
- Thin read-only backend API (Python) that serves already-computed pipeline
  output (holdings, allocation, analytics) — reads a pushed snapshot, never
  runs ingestion itself.
- Next.js + shadcn/ui frontend consuming that API — one responsive UI, no
  separate mobile build.
- Single-user auth gate in front of both.
- Deploy frontend + backend to an always-on host reachable without the
  laptop being on.
- Deliberately no agent reasoning in this phase — read-only numbers, so the
  hosting mechanism is tested in isolation.

**Exit criteria:** Adithya can check current portfolio state from his phone,
over the open internet, in under 10 seconds, with the laptop off and no
terminal involved.

---

## Phase 4 — Single-ticker decision support, rebuilt small

**Why scoped to one ticker:** the archived system's failure mode was scaling
to a full portfolio (25 tickers, 50-70M tokens) before the single-ticker
loop was proven cheap and reliable. Rebuild the same core value — a cited,
rubric-scored verdict for one ticker on demand — small first.

**Reused as-is from the archive** (already validated, not being redesigned):
`Knowledge-Base/taxonomy/decision-rubric.yml`, the thesis page template and
front-matter spec, the two-track equity/ETF scoring split.

**Rebuilt, applying the lessons checklist explicitly:**
- Two agents, split by mechanical vs. judgment: a `haiku` data-prep agent
  (fetch + build worksheet, zero judgment) and an `opus` analyst agent
  (score against the rubric, write the narrative) — never one agent doing
  both.
- Hard caps on any embedded evidence payload from day one (row count + char
  count), not retrofitted after a cost blowup.
- The prep agent reports via a script-printed stdout summary, never reads
  the full research JSON itself.
- A `--precompute-only` style preview mode on the validator so the analyst
  can self-check against the real verdict math before finalizing.
- One agent invocation per ticker — no looping a ticker list inside a single
  agent.

**Exit criteria:** ask "what should I do about \[ticker\]" and get back a
cited, rubric-scored verdict with a written thesis page, reliably, within a
token budget set in advance and actually measured (not estimated after the
fact).

---

## Phase 5 — Portfolio-wide decision support

**Why not sooner:** only fan out once Phase 4 has proven the single-ticker
cost and reliability. This phase is explicitly the "scale it up" step, done
deliberately instead of by accident.

**Work:**
- Apply lesson 6 directly: parallelize the fetch/score step (one agent
  spawn per ticker, concurrent, no shared state) and serialize only the KB
  write step (shared index/log files) — as one agent looping the
  deterministic commit script over the batch, not N agent spawns.
- Batch equities and ETFs concurrently, with the ETF batch's analyst step
  running on a `sonnet` model override (fund-track judgment is simpler; the
  validator still recomputes the math regardless of which model scored it).

**Exit criteria:** a full portfolio run completes within an explicit,
pre-set token budget, producing updated thesis pages per ticker and one
portfolio-level summary.

---

## Phase 6 — Bring decision support into the hosted view, then automate

**Why last:** this is where the two prior tracks (Phase 3's hosted view,
Phase 4/5's decision support) actually merge into the v1 definition of done
from the vision doc — and only then does scheduling/automation earn its
place, once there's a real recurring job to automate.

**Work:**
- Surface Phase 4/5 verdicts inside the Phase 3 hosted view, so holdings and
  decision-support verdicts live in one place instead of separate outputs.
- Revisit `docs/ideas-mcp.md`'s scheduling question (Hermes/cron/cloud
  scheduler) only now, scoped to the one job that actually needs it (e.g.
  weekly watchlist re-scoring) — not the general-purpose scheduling
  architecture originally sketched there.

**Exit criteria:** opening the hosted view from phone shows current holdings
and current decision-support verdicts together, refreshed on a schedule
Adithya chooses. This is the v1 finish line from the vision doc.

---

## What explicitly does not get scheduled here

Real, previously-scoped, and deliberately deferred until v1 above is
working: technical-analysis scoring dimension
(`docs/plans/stock-decision-support-technical-analysis.md`), holdings
reconciliation agent, general-purpose MCP email/scheduler servers beyond the
one job in Phase 6. These aren't rejected — they're exactly the kind of
"plausible next feature" that caused the original scope problem, so they
wait until there's a working v1 to extend rather than a foundation to
finish.
